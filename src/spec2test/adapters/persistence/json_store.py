"""Versioned run storage: write to a temporary file, then replace atomically."""
from __future__ import annotations

import dataclasses
import json
import os
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


class JsonRunStore:
    """Implements :class:`RunStorePort`."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def save_run(self, run: GenerationRun) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{run.run_id}.json"
        payload = json.dumps(run_to_dict(run), ensure_ascii=False, indent=2)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)  # atomic replace; a partial file is never visible
        return target
