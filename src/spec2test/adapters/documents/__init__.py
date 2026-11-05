from .pdf_import import PdfDocumentImporter, extract_pages
from .text_import import TextDocumentImporter

DEFAULT_IMPORTERS = (TextDocumentImporter(), PdfDocumentImporter())

__all__ = [
    "DEFAULT_IMPORTERS",
    "PdfDocumentImporter",
    "TextDocumentImporter",
    "extract_pages",
]
