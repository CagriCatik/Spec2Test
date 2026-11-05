"""Versioned run storage: write to a temporary file, then replace atomically."""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

from ...domain.models import GenerationRun

STORE_VERSION = "1"


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def run_to_dict(run: GenerationRun) -> dict:
    """Full exchange shape: raw answers, findings and review status included."""
    return {
        "store_version": STORE_VERSION,
        "run": _plain(run),
        "summary": {
            "status": run.status.value,
            "test_case_drafts": len(run.test_cases),
            "open_questions": len(run.open_questions),
            "units_accepted": len(run.accepted_units),
            "units_rejected": len(run.rejected_units),
        },
    }


class RunAlreadyStoredError(FileExistsError):
    """Raised instead of overwriting the result of an earlier run."""


class JsonRunStore:
    """Implements :class:`RunStorePort`.

    A stored run is never overwritten silently: run IDs are collision-free, so
    an existing file means a different run already used that name.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def path_for(self, run: GenerationRun) -> Path:
        return self.directory / f"{run.run_id}.json"

    def save_run(self, run: GenerationRun, *, overwrite: bool = False) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(run)
        if target.exists() and not overwrite:
            raise RunAlreadyStoredError(
                f"{target} already exists; refusing to overwrite the result of an "
                "earlier run. Pass overwrite=True to replace it deliberately."
            )
        payload = json.dumps(run_to_dict(run), ensure_ascii=False, indent=2)

        # A unique temporary file per write: concurrent writers cannot corrupt
        # each other's partial file.
        handle, tmp_name = tempfile.mkstemp(
            dir=self.directory, prefix=f".{run.run_id}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)  # atomic; a partial file is never visible
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return target
