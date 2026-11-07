"""One list of sources, shown and used — there is no second one.

The window used to hold two competing ideas of "what are we working on":
``_spec_paths``, set by the File menu and by ``paths.pdf_path`` in the
configuration, and the knowledge base of the scope panel. ``start_run``
preferred the first, the panel displayed the second, and nothing said so.
A user could see ``requirements/`` on screen while the run read a single
file from a config entry written months ago.

This module is the single answer. :class:`SourceSet` holds every source
the user added, and :meth:`SourceSet.documents` returns exactly the files
the next run will read. The panel renders the same object. There is no
path by which the two can disagree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: what the importers can read. PDF stays optional: it needs PyMuPDF.
TEXT_SUFFIXES = (".md", ".markdown", ".txt")
PDF_SUFFIX = ".pdf"
ALL_SUFFIXES = (*TEXT_SUFFIXES, PDF_SUFFIX)


def pdf_available() -> bool:
    """Whether PDFs can be imported in this installation."""
    try:  # pragma: no cover - depends on the machine
        import pymupdf  # noqa: F401
    except ImportError:
        try:
            import fitz  # noqa: F401
        except ImportError:
            return False
    return True


@dataclass(frozen=True)
class SourceEntry:
    """One thing the user added: a folder, or a single document."""

    path: Path
    #: "folder" or "file"
    kind: str

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def documents(self) -> list[Path]:
        """The files this entry contributes, in a stable order."""
        if self.kind == "file":
            return [self.path] if self.path.is_file() else []
        if not self.path.is_dir():
            return []
        found = [
            candidate
            for candidate in sorted(self.path.rglob("*"))
            if candidate.is_file() and candidate.suffix.lower() in ALL_SUFFIXES
        ]
        return found

    def label(self) -> str:
        if self.kind == "folder":
            return f"{self.path.name}/  ({len(self.documents())} document(s))"
        return self.path.name


@dataclass
class SourceSet:
    """Every source, in the order the user added them. Shown == used."""

    entries: list[SourceEntry] = field(default_factory=list)

    # ------------------------------------------------------------ edit --
    def add_folder(self, path: Path | str) -> bool:
        return self._add(SourceEntry(Path(path), "folder"))

    def add_files(self, paths) -> int:
        return sum(self._add(SourceEntry(Path(p), "file")) for p in paths)

    def _add(self, entry: SourceEntry) -> bool:
        if any(e.path == entry.path and e.kind == entry.kind for e in self.entries):
            return False
        self.entries.append(entry)
        return True

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            del self.entries[index]

    def clear(self) -> None:
        self.entries.clear()

    def replace_files(self, paths) -> None:
        """Set the single-file entries, keeping any folders."""
        self.entries = [e for e in self.entries if e.kind == "folder"]
        self.add_files(paths)

    # ------------------------------------------------------------ read --
    def documents(self) -> list[Path]:
        """What the next run reads. Deduplicated, order preserved."""
        seen: set[Path] = set()
        out: list[Path] = []
        for entry in self.entries:
            for path in entry.documents():
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                out.append(path)
        return out

    def files(self) -> list[Path]:
        """Only the explicitly added single documents."""
        return [e.path for e in self.entries if e.kind == "file"]

    def folders(self) -> list[Path]:
        return [e.path for e in self.entries if e.kind == "folder"]

    @property
    def is_empty(self) -> bool:
        return not self.documents()

    def missing(self) -> list[SourceEntry]:
        """Entries whose path is gone since it was added."""
        return [e for e in self.entries if not e.exists]

    def unsupported(self) -> list[Path]:
        """Added files this installation cannot read."""
        if pdf_available():
            return []
        return [p for p in self.documents() if p.suffix.lower() == PDF_SUFFIX]

    def summary(self) -> str:
        documents = self.documents()
        if not documents:
            return "No source added yet."
        parts = []
        folders = self.folders()
        if folders:
            parts.append(f"{len(folders)} folder(s)")
        files = self.files()
        if files:
            parts.append(f"{len(files)} added document(s)")
        text = f"{len(documents)} document(s) will be read, from " + ", ".join(parts)
        gone = self.missing()
        if gone:
            text += f" - {len(gone)} source(s) no longer exist"
        blocked = self.unsupported()
        if blocked:
            text += (
                f" - {len(blocked)} PDF(s) cannot be read: install PyMuPDF "
                'with pip install -e ".[gui]"'
            )
        return text


__all__ = [
    "ALL_SUFFIXES",
    "PDF_SUFFIX",
    "TEXT_SUFFIXES",
    "SourceEntry",
    "SourceSet",
    "pdf_available",
]
