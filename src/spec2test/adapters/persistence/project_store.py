"""Versioned project files.

A project file holds everything a reviewer needs to continue later: the
original documents, the captured requirements, the current drafts, every
earlier revision, the runs and the review history.

Writing goes through a unique temporary file and an atomic replace, so a
crash never leaves a half-written project behind. A lock file marks the
project as open; a second writer is reported instead of silently winning.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from ...domain.models import (
    ClarificationStatus,
    DocumentFormat,
    GenerationAttempt,
    GenerationRun,
    GenerationUnitResult,
    Measurement,
    OpenQuestion,
    Project,
    QuestionOrigin,
    Requirement,
    ReviewAction,
    ReviewEvent,
    ReviewStatus,
    RunStatus,
    Severity,
    SourceDocument,
    SourceSpan,
    TestCase,
    TestStep,
    UnitStatus,
    ValidationIssue,
)

PROJECT_FORMAT = "1"
SUPPORTED_FORMATS = frozenset({"1"})
PROJECT_SUFFIX = ".s2t.json"
LOCK_SUFFIX = ".lock"


class ProjectFileError(RuntimeError):
    """The file cannot be read as a project; the message says why."""


class ProjectLocked(RuntimeError):
    """Another process has this project open for writing."""


# ------------------------------------------------------------ serialising ---
def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


_ENUMS = {
    ClarificationStatus,
    DocumentFormat,
    QuestionOrigin,
    ReviewAction,
    ReviewStatus,
    RunStatus,
    Severity,
    UnitStatus,
}


@lru_cache(maxsize=None)
def _hints(target_type: Any) -> dict[str, Any]:
    """Resolve the real field types, not the annotation strings."""
    import spec2test.domain.models as models

    return get_type_hints(target_type, vars(models))


def _unwrap_optional(target_type: Any) -> Any:
    """``X | None`` becomes ``X``. Generics such as ``list[X]`` stay intact."""
    if get_origin(target_type) not in (Union, UnionType):
        return target_type
    args = [a for a in get_args(target_type) if a is not type(None)]
    return args[0] if len(args) == 1 else target_type


def _build(target_type: Any, value: Any, path: str) -> Any:
    """Rebuild a dataclass tree, reporting the field that is wrong."""
    if value is None:
        return None
    target_type = _unwrap_optional(target_type)
    origin = get_origin(target_type)
    if origin in (list, tuple):
        args = [a for a in get_args(target_type) if a is not Ellipsis]
        item_type = args[0] if args else Any
        if not isinstance(value, list):
            raise ProjectFileError(
                f"{path}: expected a list, got {type(value).__name__}"
            )
        items = [_build(item_type, item, f"{path}[{i}]") for i, item in enumerate(value)]
        return tuple(items) if origin is tuple else items
    if origin is dict or target_type is dict:
        return value
    if isinstance(target_type, type) and issubclass(target_type, Enum):
        if target_type not in _ENUMS:  # pragma: no cover - defensive
            return value
        try:
            return target_type(value)
        except ValueError as exc:
            raise ProjectFileError(f"{path}: unknown value {value!r}") from exc
    if is_dataclass(target_type):
        if not isinstance(value, dict):
            raise ProjectFileError(
                f"{path}: expected an object, got {type(value).__name__}"
            )
        hints = _hints(target_type)
        kwargs = {}
        for field in fields(target_type):
            if field.name.startswith("_") or field.name not in value:
                continue
            kwargs[field.name] = _build(
                hints.get(field.name, Any), value[field.name], f"{path}.{field.name}"
            )
        try:
            return target_type(**kwargs)
        except (TypeError, ValueError) as exc:
            raise ProjectFileError(f"{path}: {exc}") from exc
    return value


def project_to_dict(project: Project) -> dict:
    return {"project_format": PROJECT_FORMAT, "project": _plain(project)}


def project_from_dict(payload: Any) -> Project:
    if not isinstance(payload, dict):
        raise ProjectFileError(
            f"expected a project object, got {type(payload).__name__}"
        )
    version = payload.get("project_format")
    if version is None:
        raise ProjectFileError(
            "no 'project_format' field; this does not look like a Spec2Test project"
        )
    if version not in SUPPORTED_FORMATS:
        raise ProjectFileError(
            f"project format {version!r} is not supported "
            f"(this version reads {sorted(SUPPORTED_FORMATS)})"
        )
    body = payload.get("project")
    if not isinstance(body, dict):
        raise ProjectFileError("the 'project' section is missing or not an object")

    project = _build(Project, body, "project")
    # revisions is a plain mapping of lists and needs its own pass
    raw_revisions = body.get("revisions") or {}
    project.revisions = {
        case_id: [
            _build(TestCase, item, f"project.revisions.{case_id}[{i}]")
            for i, item in enumerate(items or [])
        ]
        for case_id, items in raw_revisions.items()
    }
    return project


# ------------------------------------------------------------------ store ---
class ProjectStore:
    """Implements durable project storage."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + LOCK_SUFFIX)
        self._lock_fd: int | None = None

    # --------------------------------------------------------------- io --
    def save(self, project: Project) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(project_to_dict(project), ensure_ascii=False, indent=2)
        handle, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return self.path

    def load(self) -> Project:
        if not self.path.exists():
            raise ProjectFileError(f"{self.path} does not exist")
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProjectFileError(f"{self.path} cannot be read: {exc}") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProjectFileError(
                f"{self.path} is not valid JSON "
                f"({exc.msg} at line {exc.lineno} column {exc.colno}); "
                "the file may be truncated"
            ) from exc
        return project_from_dict(payload)

    # ------------------------------------------------------------- lock --
    def acquire_lock(self, *, owner: str = "spec2test") -> None:
        """Mark the project as open for writing. Fails if someone else has it."""
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            holder = ""
            try:
                holder = self._lock_path.read_text(encoding="utf-8").strip()
            except OSError:  # pragma: no cover - unreadable lock
                pass
            raise ProjectLocked(
                f"{self.path} is already open for writing"
                + (f" by {holder}" if holder else "")
                + f". Close it there, or delete {self._lock_path} if that is stale."
            ) from exc
        os.write(fd, f"{owner} pid={os.getpid()}".encode("utf-8"))
        self._lock_fd = fd

    def release_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
        self._lock_path.unlink(missing_ok=True)

    @property
    def locked_by_other(self) -> bool:
        return self._lock_path.exists() and self._lock_fd is None

    def __enter__(self) -> "ProjectStore":
        self.acquire_lock()
        return self

    def __exit__(self, *_exc) -> None:
        self.release_lock()
