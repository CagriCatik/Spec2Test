"""Keep the knowledge index in step with the documents on disk.

A file is read again when its content hash changed *or* when the parser
version changed; a file that disappeared is removed from the index. Nothing is
deleted silently: the result says what happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from ..domain.models import Requirement, SourceDocument
from .ingest import extract_requirements
from .knowledge import Passage, segment_document
from .ports import DocumentImportPort


class KnowledgeIndex(Protocol):
    """What the application needs from an index implementation."""

    def needs_reindex(self, document: SourceDocument) -> bool: ...

    def put_document(
        self,
        document: SourceDocument,
        passages: Sequence[Passage],
        *,
        path: str | None = ...,
        indexed_at: str = ...,
    ) -> None: ...

    def remove_document(self, document_id: str) -> None: ...

    def known_document_ids(self) -> set[str]: ...


@dataclass
class IndexSyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    documents: dict[str, SourceDocument] = field(default_factory=dict)
    passages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)

    def summary(self) -> str:
        return (
            f"{len(self.added)} added, {len(self.updated)} updated, "
            f"{len(self.removed)} removed, {len(self.unchanged)} unchanged, "
            f"{self.passages} passage(s)"
        )


def collect_documents(folder: Path | str, patterns: Sequence[str] = ("*.md",)) -> list[Path]:
    base = Path(folder)
    found: list[Path] = []
    for pattern in patterns:
        found.extend(sorted(base.glob(pattern)))
    return sorted(set(found))


def sync_index(
    index: KnowledgeIndex,
    paths: Iterable[Path],
    importers: Sequence[DocumentImportPort],
    *,
    id_pattern: str | None = None,
) -> IndexSyncResult:
    """Bring the index in line with these files."""
    result = IndexSyncResult()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen: set[str] = set()

    for path in paths:
        path = Path(path)
        importer = next((imp for imp in importers if imp.supports(path)), None)
        if importer is None:
            result.warnings.append(f"no importer for {path.name}; skipped")
            continue
        imported = importer.load(path)
        document = imported.document
        seen.add(document.document_id)
        result.documents[document.document_id] = document

        captured = (
            extract_requirements(imported, id_pattern=id_pattern)
            if id_pattern
            else extract_requirements(imported)
        )
        result.requirements.extend(captured.requirements)
        result.warnings.extend(captured.warnings)

        if not index.needs_reindex(document):
            result.unchanged.append(document.filename)
            continue

        passages = segment_document(imported, captured.requirements)
        known = document.document_id in index.known_document_ids()
        index.put_document(document, passages, path=str(path), indexed_at=now)
        result.passages += len(passages)
        (result.updated if known else result.added).append(document.filename)

    for document_id in sorted(index.known_document_ids() - seen):
        index.remove_document(document_id)
        result.removed.append(document_id)

    return result
