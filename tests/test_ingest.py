"""Import tests: source spans, page boundaries, duplicate IDs, empty pages."""
from __future__ import annotations

from pathlib import Path

import pytest

from spec2test.adapters.documents import PdfDocumentImporter, TextDocumentImporter
from spec2test.application.ingest import (
    extract_requirements,
    import_documents,
    internal_requirement_id,
)
from spec2test.domain.models import ClarificationStatus


def ingest(path: Path):
    importer = (
        TextDocumentImporter() if path.suffix.lower() != ".pdf" else PdfDocumentImporter()
    )
    return extract_requirements(importer.load(path))


# ------------------------------------------------------------- fixture set ---
def test_all_ten_fixture_requirements_are_captured(spec_a, spec_b, expectations):
    result_a = ingest(spec_a)
    result_b = ingest(spec_b)

    external = {r.external_id for r in result_a.requirements if r.external_id}
    expected = {
        key for key in expectations["requirements"] if key != "RAS-SW-REQ-0010"
    }
    assert expected <= external

    without_id = [r for r in result_a.requirements if r.external_id is None]
    assert len(without_id) == 1
    assert "diagnostic session" in without_id[0].text
    assert result_b.with_external_id("RAS-SW-REQ-0001"), "fixture B requirement missing"


def test_requirement_like_prose_is_captured_as_a_candidate_for_review(spec_b):
    """A paragraph without an ID is a proposal, never a confirmed requirement."""
    result = ingest(spec_b)
    candidates = [r for r in result.requirements if r.external_id is None]
    assert candidates, "the explanatory paragraph should surface for review"
    for candidate in candidates:
        assert candidate.clarification_status is ClarificationStatus.NEEDS_CLARIFICATION
        assert any("needs review" in note for note in candidate.notes)
    assert "C_REQUIREMENT_WITHOUT_ID" in {issue.code for issue in result.issues}


def test_every_requirement_keeps_a_quotable_source_span(spec_a):
    result = ingest(spec_a)
    document = result.document
    for req in result.requirements:
        assert req.spans, f"{req.requirement_id} has no source span"
        for span in req.spans:
            assert span.document_id == document.document_id
            assert span.quote
            # the quote must really be at that position in the extracted text
            assert document.slice(span.char_start, span.char_end) == span.quote


def test_internal_ids_are_stable_across_imports(spec_a):
    first = ingest(spec_a)
    second = ingest(spec_a)
    assert [r.requirement_id for r in first.requirements] == [
        r.requirement_id for r in second.requirements
    ]


def test_an_internal_id_is_not_presented_as_a_document_id(spec_a):
    result = ingest(spec_a)
    anonymous = next(r for r in result.requirements if r.external_id is None)
    assert anonymous.requirement_id.startswith("REQ-")
    assert anonymous.display_id().endswith("(internal)")
    assert anonymous.has_external_id is False


def test_the_same_external_id_in_two_documents_is_never_merged(spec_a, spec_b):
    result_a = ingest(spec_a)
    result_b = ingest(spec_b)
    first = result_a.with_external_id("RAS-SW-REQ-0001")[0]
    second = result_b.with_external_id("RAS-SW-REQ-0001")[0]

    assert first.requirement_id != second.requirement_id
    assert first.text != second.text
    assert first.spans[0].document_id != second.spans[0].document_id


def test_a_duplicate_external_id_inside_one_document_is_reported(tmp_path):
    path = tmp_path / "dup.md"
    path.write_text(
        "SYS-REQ-0001: The system shall start within 2 s.\n\n"
        "SYS-REQ-0001: The system shall stop within 3 s.\n",
        encoding="utf-8",
    )
    result = ingest(path)
    assert len(result.requirements) == 2
    assert result.requirements[0].requirement_id != result.requirements[1].requirement_id
    assert "C_DUPLICATE_EXTERNAL_ID" in {issue.code for issue in result.issues}


def test_a_vague_requirement_is_marked_as_needing_clarification(spec_a):
    result = ingest(spec_a)
    vague = result.with_external_id("RAS-SW-REQ-0007")[0]
    assert vague.clarification_status is ClarificationStatus.NEEDS_CLARIFICATION


def test_a_requirement_with_numbers_is_not_marked_vague(spec_a):
    result = ingest(spec_a)
    precise = result.with_external_id("RAS-SW-REQ-0001")[0]
    assert precise.clarification_status is ClarificationStatus.CLEAR


# ------------------------------------------------------------------- unicode --
def test_unicode_and_multiline_requirements_survive(tmp_path):
    path = tmp_path / "unicode.md"
    path.write_text(
        "SYS-REQ-0042: Der Lenkwinkel muss ±10,5° betragen\n"
        "und innerhalb von 100 ms erreicht sein – geprüft über CAN.\n",
        encoding="utf-8",
    )
    result = ingest(path)
    requirement = result.requirements[0]
    assert "±10,5°" in requirement.text
    assert "–" in requirement.text
    assert "100 ms" in requirement.text


# ----------------------------------------------------------------------- pdf --
def test_pdf_import_keeps_page_references(spec_pdf):
    result = ingest(spec_pdf)
    assert result.document.page_count == 5
    pages = {span.page for req in result.requirements for span in req.spans}
    assert pages, "no page reference captured"
    assert pages <= {1, 2, 3, 4, 5}


def test_a_requirement_across_a_page_boundary_keeps_both_pages(spec_pdf):
    result = ingest(spec_pdf)
    spanning = result.with_external_id("RAS-SW-REQ-0010")
    assert spanning, "the page-spanning requirement was not captured"
    pages = sorted({span.page for span in spanning[0].spans})
    assert pages == [3, 4], f"expected pages 3 and 4, got {pages}"
    assert "RAS_Status" in spanning[0].text, "the continuation on page 4 is missing"


def test_pdf_pages_are_all_present_in_the_extracted_text(spec_pdf):
    result = ingest(spec_pdf)
    for number in range(1, 6):
        assert f"PAGE-MARKER-{number}" in result.document.text


def test_an_empty_pdf_page_is_reported_and_not_silently_dropped(empty_page_pdf):
    result = ingest(empty_page_pdf)
    assert result.document.page_count == 3
    assert result.document.empty_pages == (2,)
    assert any("page 2" in warning for warning in result.warnings)
    assert any("OCR" in warning for warning in result.warnings)


def test_a_document_without_requirements_is_an_explicit_error(tmp_path):
    path = tmp_path / "prose.md"
    path.write_text("# Notes\n\nThis document describes nothing testable.\n", "utf-8")
    result = ingest(path)
    assert result.requirements == []
    assert "E_NO_REQUIREMENTS" in {issue.code for issue in result.issues}


def test_an_unsupported_format_is_refused(tmp_path):
    path = tmp_path / "spec.docx"
    path.write_bytes(b"not really a document")
    with pytest.raises(ValueError) as excinfo:
        import_documents([path], (TextDocumentImporter(), PdfDocumentImporter()))
    assert "no importer" in str(excinfo.value)


def test_importers_select_by_suffix(spec_a, spec_pdf):
    assert TextDocumentImporter().supports(spec_a)
    assert not TextDocumentImporter().supports(spec_pdf)
    assert PdfDocumentImporter().supports(spec_pdf)


def test_content_hash_changes_with_the_content(tmp_path):
    first = tmp_path / "a.txt"
    first.write_text("SYS-REQ-1: must do 1 s\n", encoding="utf-8")
    second = tmp_path / "b.txt"
    second.write_text("SYS-REQ-1: must do 2 s\n", encoding="utf-8")
    assert ingest(first).document.content_hash != ingest(second).document.content_hash
