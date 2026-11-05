"""Document import adapters."""
from __future__ import annotations

import logging
from pathlib import Path

from .pdf_import import PdfDocumentImporter, extract_pages
from .text_import import TextDocumentImporter

DEFAULT_IMPORTERS = (TextDocumentImporter(), PdfDocumentImporter())


def load_guidance_file(path: Path | str | None) -> str | None:
    """Read an optional project guidance file.

    The content is appended to the contract rules of the system prompt; it can
    never replace them. A missing or unreadable file is not an error.
    """
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        logging.warning("Could not read guidance file %s: %s", path, exc)
        return None


__all__ = [
    "DEFAULT_IMPORTERS",
    "PdfDocumentImporter",
    "TextDocumentImporter",
    "extract_pages",
    "load_guidance_file",
]
