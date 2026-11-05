"""PDF import that keeps page references and the extracted text.

Empty or image-only pages are reported; OCR is out of scope.
"""
from __future__ import annotations

from pathlib import Path

from ...application.ingest import content_hash
from ...application.ports import ImportedDocument, TextSegment
from ...domain.models import DocumentFormat, SourceDocument

PAGE_SEPARATOR = "\n\n"


def _open_pdf(path: Path):
    try:  # PyMuPDF >= 1.24 exposes the new module name
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover - older PyMuPDF
        import fitz as pymupdf  # type: ignore
    return pymupdf.open(str(path))


def extract_pages(path: Path) -> list[str]:
    """The single place where PDF page text is read."""
    with _open_pdf(Path(path)) as doc:
        return [page.get_text() for page in doc]


class PdfDocumentImporter:
    """Implements :class:`DocumentImportPort` for PDF files."""

    def supports(self, path: Path) -> bool:
        return Path(path).suffix.lower() == ".pdf"

    def load(self, path: Path) -> ImportedDocument:
        path = Path(path)
        pages = extract_pages(path)

        segments: list[TextSegment] = []
        warnings: list[str] = []
        parts: list[str] = []
        cursor = 0
        for number, page_text in enumerate(pages, 1):
            start = cursor
            end = start + len(page_text)
            is_empty = not page_text.strip()
            segments.append(
                TextSegment(
                    index=number - 1,
                    text=page_text,
                    char_start=start,
                    char_end=end,
                    page=number,
                    is_empty=is_empty,
                )
            )
            parts.append(page_text)
            cursor = end + len(PAGE_SEPARATOR)
            if is_empty:
                warnings.append(
                    f"{path.name}: page {number} has no extractable text "
                    "(empty or image-only); OCR is out of scope"
                )

        text = PAGE_SEPARATOR.join(parts)
        document = SourceDocument(
            document_id=f"{path.stem}-{content_hash(text)[:8]}",
            filename=path.name,
            format=DocumentFormat.PDF,
            content_hash=content_hash(text),
            text=text,
            page_count=len(pages),
            empty_pages=tuple(s.page for s in segments if s.is_empty and s.page),
        )
        if not pages:
            warnings.append(f"{path.name}: the PDF contains no pages")
        return ImportedDocument(
            document=document, segments=tuple(segments), warnings=tuple(warnings)
        )
