"""TXT and Markdown import with section and character positions."""
from __future__ import annotations

import re
from pathlib import Path

from ...application.ingest import content_hash
from ...application.ports import ImportedDocument, TextSegment
from ...domain.models import DocumentFormat, SourceDocument

_HEADING = re.compile(r"^(#{1,6})\s*(.+?)\s*$", re.MULTILINE)
# Metadata a document declares about itself, e.g. "**Version:** 2.4.0"
_DECLARED = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*|__)?\s*(?P<key>Document ID|Dokument-ID|Version)"
    r"\s*:?\s*(?:\*\*|__)?\s*:?\s*(?P<value>\S[^\n]*?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_SUFFIXES = {".txt": DocumentFormat.TXT, ".md": DocumentFormat.MARKDOWN,
             ".markdown": DocumentFormat.MARKDOWN}


class TextDocumentImporter:
    """Implements :class:`DocumentImportPort` for plain text and Markdown."""

    def supports(self, path: Path) -> bool:
        return Path(path).suffix.lower() in _SUFFIXES

    def load(self, path: Path) -> ImportedDocument:
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        fmt = _SUFFIXES.get(path.suffix.lower(), DocumentFormat.TXT)
        declared_id, version = declared_metadata(text)
        document = SourceDocument(
            document_id=f"{path.stem}-{content_hash(text)[:8]}",
            filename=path.name,
            format=fmt,
            content_hash=content_hash(text),
            text=text,  # unmodified source text
            page_count=1,
            declared_id=declared_id,
            version=version,
        )
        segments = _sections(text)
        warnings: list[str] = []
        if not text.strip():
            warnings.append(f"{path.name}: document is empty")
        return ImportedDocument(
            document=document, segments=segments, warnings=tuple(warnings)
        )


def declared_metadata(text: str) -> tuple[str | None, str | None]:
    """The document ID and version a document states about itself."""
    declared_id = version = None
    for match in _DECLARED.finditer(text[:4000]):
        key = match.group("key").lower()
        value = match.group("value").strip().rstrip("*").strip()
        if key.startswith(("document id", "dokument")) and declared_id is None:
            declared_id = value
        elif key == "version" and version is None:
            version = value
    return declared_id, version


def _sections(text: str) -> tuple[TextSegment, ...]:
    """Split on Markdown headings; plain text becomes a single section."""
    headings = list(_HEADING.finditer(text))
    if not headings:
        return (
            TextSegment(
                index=0,
                text=text,
                char_start=0,
                char_end=len(text),
                section="document",
                is_empty=not text.strip(),
            ),
        )

    segments: list[TextSegment] = []
    if headings[0].start() > 0:
        preamble = text[: headings[0].start()]
        segments.append(
            TextSegment(
                index=0,
                text=preamble,
                char_start=0,
                char_end=headings[0].start(),
                section="preamble",
                is_empty=not preamble.strip(),
            )
        )
    for i, heading in enumerate(headings):
        start = heading.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[start:end]
        segments.append(
            TextSegment(
                index=len(segments),
                text=body,
                char_start=start,
                char_end=end,
                section=heading.group(2),
                is_empty=not body[heading.end() - start :].strip(),
            )
        )
    return tuple(segments)
