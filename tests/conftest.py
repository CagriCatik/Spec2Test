"""Shared fixtures. Nothing here needs a network connection."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SPECS = FIXTURES / "specs"
PAGES = FIXTURES / "pages"


@pytest.fixture(scope="session")
def expectations() -> dict:
    import yaml

    return yaml.safe_load((FIXTURES / "expectations.yml").read_text(encoding="utf-8"))


@pytest.fixture
def spec_a() -> Path:
    return SPECS / "spec_a.md"


@pytest.fixture
def spec_b() -> Path:
    return SPECS / "spec_b.md"


@pytest.fixture(scope="session")
def page_texts() -> list[str]:
    return [
        (PAGES / f"page_{i}.txt").read_text(encoding="utf-8") for i in range(1, 6)
    ]


def build_pdf(target: Path, pages: list[str]) -> Path:
    """Build a deterministic PDF from the versioned page fixtures."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - older PyMuPDF
        import fitz as pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((56, 72), text, fontsize=11)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))
    doc.close()
    return target


@pytest.fixture
def spec_pdf(tmp_path: Path, page_texts: list[str]) -> Path:
    return build_pdf(tmp_path / "spec_pages.pdf", page_texts)


@pytest.fixture
def empty_page_pdf(tmp_path: Path) -> Path:
    return build_pdf(
        tmp_path / "with_empty.pdf",
        ["PAGE-MARKER-1 text", "", "PAGE-MARKER-3 text"],
    )
