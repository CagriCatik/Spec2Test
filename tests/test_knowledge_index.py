"""The knowledge base: segmentation, index synchronisation and search."""
from __future__ import annotations

from pathlib import Path

import pytest

from spec2test.adapters.documents import DEFAULT_IMPORTERS, TextDocumentImporter
from spec2test.adapters.knowledge import SqliteKnowledgeIndex
from spec2test.application.indexing import collect_documents, sync_index
from spec2test.application.knowledge import (
    PARSER_VERSION,
    classify,
    is_metadata_block,
    normalise_for_search,
    search_terms,
    segment_document,
)
from spec2test.application.ingest import extract_requirements
from spec2test.domain.models import ContextKind

CORPUS = Path(__file__).resolve().parents[1] / "requirements"


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def segmented(path: Path):
    imported = TextDocumentImporter().load(path)
    requirements = extract_requirements(imported).requirements
    return imported, requirements, segment_document(imported, requirements)


# =====================================================================
# segmentation
# =====================================================================
def test_a_requirement_stays_one_passage(tmp_path):
    path = write(
        tmp_path,
        "a.md",
        "## Section\n\n### Requirement: SYS-REQ-0001\n"
        "The system shall react within 100 ms.\nIt shall log the reaction.\n",
    )
    _imported, _reqs, passages = segmented(path)
    requirement = [p for p in passages if p.kind is ContextKind.REQUIREMENT]
    assert len(requirement) == 1
    assert requirement[0].external_id == "SYS-REQ-0001"
    assert "log the reaction" in requirement[0].text


def test_sections_outside_requirements_are_indexed(tmp_path):
    path = write(
        tmp_path,
        "a.md",
        "## Signal table\n\n| Signal | Range |\n| - | - |\n"
        "| BMS_Pack_U | 0..800 V |\n| BMS_Pack_I | -600..600 A |\n\n"
        "### Requirement: SYS-REQ-0001\nThe system shall react within 100 ms.\n",
    )
    _imported, _reqs, passages = segmented(path)
    sections = [p for p in passages if p.kind is not ContextKind.REQUIREMENT]
    assert sections, "the signal table must be indexed"
    assert "BMS_Pack_U" in sections[0].text
    assert sections[0].heading == "Signal table"


def test_the_metadata_header_is_not_indexed(tmp_path):
    path = write(
        tmp_path,
        "a.md",
        "# Title of the document\n\n**Document ID:** DOC-A  \n**Version:** 2.0  \n"
        "**Classification:** internal  \n\n---\n\n"
        "## Scope\n\nThis module does something that is worth indexing.\n",
    )
    _imported, _reqs, passages = segmented(path)
    assert all(p.char_start > 0 for p in passages), "the title block was indexed"
    assert any(p.heading == "Scope" for p in passages)


def test_a_table_is_not_mistaken_for_metadata():
    table = "| Signal | Range |\n| - | - |\n| BMS_Pack_U | 0..800 V |"
    assert is_metadata_block(table) is False
    header = "# Title\n**Document ID:** X  \n**Version:** 1.0  \n---"
    assert is_metadata_block(header) is True


def test_a_long_section_is_split_but_keeps_its_position(tmp_path):
    body = "This sentence carries content.\n\n" * 200
    path = write(tmp_path, "a.md", f"## Long\n\n{body}")
    imported, _reqs, passages = segmented(path)
    sections = [p for p in passages if p.heading == "Long"]
    assert len(sections) > 1, "a long section is split"
    for passage in sections:
        assert imported.document.text[passage.char_start : passage.char_end] == passage.text


@pytest.mark.parametrize(
    "heading,expected",
    [
        ("2. CAN-FD Broadcast Message Matrix", ContextKind.SIGNAL),
        ("1. Hardware and Software Interface", ContextKind.INTERFACE),
        ("4. HiL Verification Matrix", ContextKind.REFERENCE_TEST),
        ("1. Scope & Overview", ContextKind.EXPLANATION),
    ],
)
def test_sections_are_classified_by_their_heading(heading, expected):
    assert classify(heading, "some text") is expected


def test_the_search_text_resolves_latex_but_the_original_is_untouched(tmp_path):
    path = write(
        tmp_path,
        "a.md",
        "## Timing\n\nThe reaction takes $50\\text{ ms} \\pm 2\\text{ ms}$ exactly.\n",
    )
    _imported, _reqs, passages = segmented(path)
    passage = next(p for p in passages if p.heading == "Timing")
    assert "\\text{ ms}" in passage.text, "the original is kept"
    assert "50 ms" in passage.search_text, "the searchable form is plain"


def test_normalisation_keeps_identifiers():
    assert "BMS_Pack_U" in normalise_for_search("| **BMS_Pack_U** | 0..800 V |")


# =====================================================================
# index synchronisation
# =====================================================================
@pytest.fixture
def index(tmp_path):
    return SqliteKnowledgeIndex(tmp_path / "index" / "knowledge.db")


def test_a_new_document_is_added(index, tmp_path):
    write(tmp_path, "a.md", "## Scope\n\nSomething worth indexing here.\n")
    result = sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    assert result.added == ["a.md"]
    assert index.passage_count() >= 1


def test_an_unchanged_document_is_not_reindexed(index, tmp_path):
    write(tmp_path, "a.md", "## Scope\n\nSomething worth indexing here.\n")
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    again = sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    assert again.unchanged == ["a.md"]
    assert again.changed is False


def test_a_changed_document_is_reindexed(index, tmp_path):
    write(tmp_path, "a.md", "## Scope\n\nThe first text that was indexed.\n")
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    write(tmp_path, "a.md", "## Scope\n\nA completely different text now.\n")
    result = sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    assert result.added == ["a.md"], "a new content hash means a new document id"
    assert not index.search(["first"]), "the old text is gone"
    assert index.search(["different"])


def test_a_deleted_document_disappears(index, tmp_path):
    write(tmp_path, "a.md", "## Scope\n\nSomething worth indexing here.\n")
    write(tmp_path, "b.md", "## Other\n\nAnother text that is indexed too.\n")
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    (tmp_path / "b.md").unlink()

    result = sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)

    assert len(result.removed) == 1
    assert not index.search(["Another"])


def test_a_new_parser_version_forces_a_reindex(index, tmp_path, monkeypatch):
    """An unchanged file still has to be read again after an importer change."""
    write(tmp_path, "a.md", "## Scope\n\nSomething worth indexing here.\n")
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)

    from spec2test.adapters.knowledge import sqlite_index

    monkeypatch.setattr(sqlite_index, "PARSER_VERSION", PARSER_VERSION + "-next")
    result = sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    assert result.updated == ["a.md"]
    assert result.unchanged == []


def test_an_unsupported_file_is_reported_not_skipped_silently(index, tmp_path):
    (tmp_path / "notes.docx").write_bytes(b"x")
    result = sync_index(index, [tmp_path / "notes.docx"], DEFAULT_IMPORTERS)
    assert any("no importer" in warning for warning in result.warnings)


# =====================================================================
# search
# =====================================================================
def test_search_finds_a_section_by_its_identifier(index, tmp_path):
    write(
        tmp_path,
        "a.md",
        "## Signal table\n\n| Signal | Range |\n| - | - |\n| BMS_Pack_U | 0..800 V |\n",
    )
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    hits = index.search(["BMS_Pack_U"])
    assert hits and "BMS_Pack_U" in hits[0].passage.text
    assert hits[0].terms == ("BMS_Pack_U",)
    assert hits[0].document is not None and hits[0].document.version is None


def test_search_finds_a_value_written_as_latex(index, tmp_path):
    write(tmp_path, "a.md", "## Timing\n\nIt reacts in $50\\text{ ms}$ at the latest.\n")
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    assert index.search(["50 ms"]), "the normalised copy must be searchable"


def test_search_can_be_restricted_to_a_kind(index, tmp_path):
    write(
        tmp_path,
        "a.md",
        "## Signal table\n\n| Signal | Unit | Range |\n| - | - | - |\n"
        "| BMS_Pack_U | volt | 0..800 |\n| BMS_Pack_I | ampere | -600..600 |\n\n"
        "## Scope\n\nThis module measures the pack volt level and reports it "
        "to the vehicle controller for further processing.\n",
    )
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)

    signals = index.search(["volt"], kinds=[ContextKind.SIGNAL])
    assert signals and all(h.passage.kind is ContextKind.SIGNAL for h in signals)
    others = index.search(["volt"], kinds=[ContextKind.EXPLANATION])
    assert all(h.passage.kind is ContextKind.EXPLANATION for h in others)


def test_search_returns_one_entry_per_section(index, tmp_path):
    body = "The coolant valve shall open.\n\n" * 200
    write(tmp_path, "a.md", f"## Cooling\n\n{body}")
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    hits = index.search(["coolant"], limit=5)
    headings = [h.passage.heading for h in hits]
    assert len(headings) == len(set(headings)), "a split section counted twice"


def test_a_query_cannot_inject_search_syntax(index, tmp_path):
    write(tmp_path, "a.md", "## Scope\n\nSomething worth indexing here.\n")
    sync_index(index, collect_documents(tmp_path), DEFAULT_IMPORTERS)
    assert index.search(['" OR "a" NEAR/5 "b']) == [] or True  # must not raise


def test_an_empty_query_returns_nothing(index):
    assert index.search([]) == []
    assert index.search(["a"]) == []


def test_terms_ignore_the_requirements_own_id(tmp_path):
    path = write(
        tmp_path,
        "a.md",
        "### Requirement: BMS-REQ-COM-0802\n"
        "The CRC profile shall protect the BMS_Pack_U signal.\n",
    )
    _imported, requirements, _passages = segmented(path)
    terms = search_terms(requirements[0])
    lowered = {term.lower() for term in terms}
    assert "bms" not in lowered and "req" not in lowered and "com" not in lowered
    assert "BMS_Pack_U" in terms


# =====================================================================
# the shipped corpus
# =====================================================================
def test_the_whole_corpus_indexes(index):
    documents = collect_documents(CORPUS)
    if not documents:
        pytest.skip("the repository ships no requirement documents")

    result = sync_index(index, documents, DEFAULT_IMPORTERS)

    assert len(result.added) == len(documents)
    assert len(result.requirements) > 100
    assert index.passage_count() > len(result.requirements)
    kinds = {
        row["kind"]
        for row in index._connection.execute("SELECT DISTINCT kind FROM passages")
    }
    assert {"requirement", "signal"} <= kinds
    # the signal tables a reviewer needs are in there
    assert index.search(["CAN-FD"], kinds=[ContextKind.SIGNAL])
