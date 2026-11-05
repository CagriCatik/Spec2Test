"""Resolved references as supporting context.

Written before the implementation, as the work order asks: missing targets,
ambiguous targets, self-references, cycles, budget overruns and contradictory
sources.
"""
from __future__ import annotations

import pytest

from spec2test.application.context import (
    ContextItem,
    ContextSelection,
    build_reference_context,
    detect_contradictions,
)
from spec2test.application.ingest import reference_report
from spec2test.domain.models import Requirement, SourceDocument, SourceSpan


def requirement(
    rid: str,
    external: str,
    text: str,
    *,
    references: tuple[str, ...] = (),
    document: str = "doc-1",
) -> Requirement:
    body = f"{external}: {text}"
    return Requirement(
        requirement_id=rid,
        text=body,
        spans=(SourceSpan(document, 0, len(body), body, page=1),),
        external_id=external,
        references=references,
    )


def documents(*ids: str) -> dict[str, SourceDocument]:
    from spec2test.domain.models import DocumentFormat

    return {
        doc_id: SourceDocument(
            document_id=doc_id,
            filename=f"{doc_id}.md",
            format=DocumentFormat.MARKDOWN,
            content_hash=f"hash-{doc_id}",
            text="...",
            declared_id=f"BMS-SRS-{doc_id}",
            version="2.4.0",
        )
        for doc_id in ids
    }


# =====================================================================
# selection
# =====================================================================
def test_a_directly_referenced_requirement_becomes_context():
    under_test = requirement(
        "R1", "SYS-REQ-0001", "react as in SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    target = requirement("R2", "SYS-REQ-0002", "react within 100 ms.", document="doc-2")
    report = reference_report([under_test, target])

    selection = build_reference_context(
        [under_test], report, documents=documents("doc-1", "doc-2")
    )

    assert [item.external_id for item in selection.items] == ["SYS-REQ-0002"]
    assert selection.issues == []


def test_context_is_not_expanded_recursively():
    """Only what the requirement under test cites itself."""
    first = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    second = requirement(
        "R2", "SYS-REQ-0002", "see SYS-REQ-0003.", references=("SYS-REQ-0003",)
    )
    third = requirement("R3", "SYS-REQ-0003", "the deepest one.")
    report = reference_report([first, second, third])

    selection = build_reference_context([first], report, documents=documents("doc-1"))

    assert [item.external_id for item in selection.items] == ["SYS-REQ-0002"]


def test_a_requirement_of_the_same_unit_is_not_repeated_as_context():
    first = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    second = requirement("R2", "SYS-REQ-0002", "react within 100 ms.")
    report = reference_report([first, second])

    selection = build_reference_context(
        [first, second], report, documents=documents("doc-1")
    )

    assert selection.items == ()


def test_a_self_reference_is_ignored():
    only = requirement(
        "R1", "SYS-REQ-0001", "as in SYS-REQ-0001 itself.", references=("SYS-REQ-0001",)
    )
    report = reference_report([only])
    selection = build_reference_context([only], report, documents=documents("doc-1"))
    assert selection.items == ()
    assert selection.issues == []


def test_a_cycle_terminates_and_adds_each_side_once():
    first = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    second = requirement(
        "R2", "SYS-REQ-0002", "see SYS-REQ-0001.", references=("SYS-REQ-0001",)
    )
    report = reference_report([first, second])

    selection = build_reference_context([first], report, documents=documents("doc-1"))

    assert [item.external_id for item in selection.items] == ["SYS-REQ-0002"]


def test_a_reference_without_a_target_is_reported():
    only = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-9999.", references=("SYS-REQ-9999",)
    )
    report = reference_report([only])
    selection = build_reference_context([only], report, documents=documents("doc-1"))

    assert selection.items == ()
    codes = {issue.code for issue in selection.issues}
    assert "C_UNRESOLVED_REFERENCE" in codes
    assert any("SYS-REQ-9999" in issue.message for issue in selection.issues)


def test_an_ambiguous_reference_is_reported_and_not_used():
    citing = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    first = requirement("R2", "SYS-REQ-0002", "variant A.", document="doc-2")
    second = requirement("R3", "SYS-REQ-0002", "variant B.", document="doc-3")
    report = reference_report([citing, first, second])

    selection = build_reference_context(
        [citing], report, documents=documents("doc-1", "doc-2", "doc-3")
    )

    assert selection.items == (), "an ambiguous target must not be sent"
    assert "C_AMBIGUOUS_REFERENCE" in {issue.code for issue in selection.issues}


# =====================================================================
# provenance
# =====================================================================
def test_every_context_item_carries_its_origin():
    citing = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    target = requirement("R2", "SYS-REQ-0002", "react within 100 ms.", document="doc-2")
    report = reference_report([citing, target])

    item = build_reference_context(
        [citing], report, documents=documents("doc-1", "doc-2")
    ).items[0]

    assert item.requirement_id == "R2"
    assert item.external_id == "SYS-REQ-0002"
    assert item.document_id == "doc-2"
    assert item.document_version == "2.4.0"
    assert item.declared_document_id == "BMS-SRS-doc-2"
    assert item.content_hash == "hash-doc-2"
    assert item.spans and item.spans[0].page == 1
    assert item.cited_by == ("SYS-REQ-0001",)
    assert item.text.startswith("SYS-REQ-0002")


def test_a_missing_document_entry_does_not_lose_the_requirement():
    citing = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    target = requirement("R2", "SYS-REQ-0002", "react within 100 ms.", document="doc-2")
    report = reference_report([citing, target])

    selection = build_reference_context([citing], report, documents={})
    item = selection.items[0]
    assert item.document_version is None
    assert item.content_hash is None
    assert item.text


# =====================================================================
# budget
# =====================================================================
def test_context_beyond_the_budget_is_reported_not_truncated():
    citing = requirement(
        "R1",
        "SYS-REQ-0001",
        "see SYS-REQ-0002 and SYS-REQ-0003.",
        references=("SYS-REQ-0002", "SYS-REQ-0003"),
    )
    long_text = "x shall be 1 s. " * 100
    first = requirement("R2", "SYS-REQ-0002", long_text, document="doc-2")
    second = requirement("R3", "SYS-REQ-0003", long_text, document="doc-3")
    report = reference_report([citing, first, second])

    selection = build_reference_context(
        [citing],
        report,
        documents=documents("doc-1", "doc-2", "doc-3"),
        char_budget=len(long_text) + 200,
    )

    assert len(selection.items) == 1, "only what fits is sent"
    assert all(item.text.endswith("1 s. ") for item in selection.items), (
        "an item is sent whole or not at all"
    )
    codes = {issue.code for issue in selection.issues}
    assert "C_CONTEXT_OMITTED" in codes
    assert any("SYS-REQ-0003" in issue.message for issue in selection.issues)


def test_a_single_oversized_reference_is_reported():
    citing = requirement(
        "R1", "SYS-REQ-0001", "see SYS-REQ-0002.", references=("SYS-REQ-0002",)
    )
    target = requirement("R2", "SYS-REQ-0002", "y " * 5000, document="doc-2")
    report = reference_report([citing, target])

    selection = build_reference_context(
        [citing], report, documents=documents("doc-1", "doc-2"), char_budget=100
    )
    assert selection.items == ()
    assert "C_CONTEXT_OMITTED" in {issue.code for issue in selection.issues}


def test_the_number_of_items_can_be_limited():
    references = tuple(f"SYS-REQ-{i:04d}" for i in range(2, 8))
    citing = requirement(
        "R1", "SYS-REQ-0001", "see several.", references=references
    )
    targets = [
        requirement(f"R{i}", f"SYS-REQ-{i:04d}", "short.", document="doc-2")
        for i in range(2, 8)
    ]
    report = reference_report([citing, *targets])

    selection = build_reference_context(
        [citing], report, documents=documents("doc-1", "doc-2"), max_items=2
    )
    assert len(selection.items) == 2
    assert "C_CONTEXT_OMITTED" in {issue.code for issue in selection.issues}


# =====================================================================
# contradictions
# =====================================================================
def test_a_differing_value_between_sources_is_reported():
    under_test = requirement(
        "R1",
        "SYS-REQ-0001",
        "the reaction shall take 100 ms as in SYS-REQ-0002.",
        references=("SYS-REQ-0002",),
    )
    target = requirement(
        "R2", "SYS-REQ-0002", "the reaction shall take 50 ms.", document="doc-2"
    )
    item = ContextItem(
        requirement_id="R2",
        external_id="SYS-REQ-0002",
        text=target.text,
        document_id="doc-2",
        spans=target.spans,
        cited_by=("SYS-REQ-0001",),
    )

    issues = detect_contradictions([under_test], [item])

    assert [issue.code for issue in issues] == ["C_POSSIBLE_VALUE_CONFLICT"]
    message = issues[0].message
    assert "ms" in message and "100" in message and "50" in message
    assert "SYS-REQ-0002" in message


def test_matching_values_are_not_reported():
    under_test = requirement(
        "R1",
        "SYS-REQ-0001",
        "the reaction shall take 100 ms as in SYS-REQ-0002.",
        references=("SYS-REQ-0002",),
    )
    item = ContextItem(
        requirement_id="R2",
        external_id="SYS-REQ-0002",
        text="SYS-REQ-0002: the reaction shall take 100 ms.",
        document_id="doc-2",
        spans=(),
        cited_by=("SYS-REQ-0001",),
    )
    assert detect_contradictions([under_test], [item]) == []


def test_several_values_of_the_same_unit_are_not_called_a_contradiction():
    """Two durations in one requirement say nothing about which would clash."""
    under_test = requirement(
        "R1",
        "SYS-REQ-0001",
        "sample every 2 ms, report within 50 ms, hold for 200 ms.",
        references=("SYS-REQ-0002",),
    )
    item = ContextItem(
        requirement_id="R2",
        external_id="SYS-REQ-0002",
        text="SYS-REQ-0002: detect within 0.5 ms.",
        document_id="doc-2",
        spans=(),
        cited_by=("SYS-REQ-0001",),
    )
    assert detect_contradictions([under_test], [item]) == []


def test_a_unit_that_appears_only_in_the_context_is_not_a_contradiction():
    under_test = requirement("R1", "SYS-REQ-0001", "the reaction shall take 100 ms.")
    item = ContextItem(
        requirement_id="R2",
        external_id="SYS-REQ-0002",
        text="SYS-REQ-0002: the voltage shall be 400 V.",
        document_id="doc-2",
        spans=(),
        cited_by=("SYS-REQ-0001",),
    )
    assert detect_contradictions([under_test], [item]) == []


def test_a_selection_without_context_is_empty_and_quiet():
    only = requirement("R1", "SYS-REQ-0001", "no references here.")
    selection = build_reference_context(
        [only], reference_report([only]), documents=documents("doc-1")
    )
    assert isinstance(selection, ContextSelection)
    assert selection.items == () and selection.issues == []


# =====================================================================
# the context reaches the model and the record
# =====================================================================
def _spec(tmp_path):
    """Two documents: one requirement cites a requirement in the other."""
    (tmp_path / "a.md").write_text(
        "**Document ID:** DOC-A\n**Version:** 1.2.0\n\n"
        "### Requirement: SYS-REQ-0001\n"
        "The controller shall limit the current as specified in SYS-REQ-0002.\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "**Document ID:** DOC-B\n**Version:** 3.0.1\n\n"
        "### Requirement: SYS-REQ-0002\n"
        "The current limit shall be 2.5 A.\n",
        encoding="utf-8",
    )
    from spec2test.adapters.documents import DEFAULT_IMPORTERS
    from spec2test.application.ingest import import_documents

    results = import_documents(
        [tmp_path / "a.md", tmp_path / "b.md"], DEFAULT_IMPORTERS
    )
    requirements = [r for result in results for r in result.requirements]
    documents = {result.document.document_id: result.document for result in results}
    return requirements, documents


def test_the_prompt_separates_the_roles(tmp_path):
    from spec2test.adapters.fake.model import ScriptedModel
    from spec2test.application.generate import GenerationSettings, generate_drafts

    requirements, documents = _spec(tmp_path)
    under_test = [r for r in requirements if r.external_id == "SYS-REQ-0001"]
    model = ScriptedModel(["not json"])

    generate_drafts(
        under_test,
        model,
        settings=GenerationSettings(max_repair_attempts=0),
        documents=documents,
        catalogue=requirements,
    )

    system, user = model.calls[0][0].content, model.calls[0][1].content
    assert "REQUIREMENTS UNDER TEST" in user
    assert "SUPPORTING CONTEXT" in user
    assert "SYS-REQ-0002" in user
    assert "DOC-B" in user and "version 3.0.1" in user, "the origin is stated"
    assert user.index("REQUIREMENTS UNDER TEST") < user.index("SUPPORTING CONTEXT")
    assert "do not write test cases for them" in user
    assert "Supporting context is background only" in system


def test_the_context_is_stored_with_the_run(tmp_path):
    from spec2test.adapters.fake.model import RuleBasedFakeModel
    from spec2test.application.generate import generate_drafts

    requirements, documents = _spec(tmp_path)
    under_test = [r for r in requirements if r.external_id == "SYS-REQ-0001"]

    run = generate_drafts(
        under_test, RuleBasedFakeModel(), documents=documents, catalogue=requirements
    )

    assert len(run.context_items) == 1
    item = run.context_items[0]
    assert item.external_id == "SYS-REQ-0002"
    assert item.document_version == "3.0.1"
    assert item.content_hash
    assert item.text.strip().endswith("2.5 A.")
    assert "supporting context items sent=1" in "\n".join(run.report_lines())


def test_the_stored_context_survives_a_project_round_trip(tmp_path):
    from spec2test.adapters.fake.model import RuleBasedFakeModel
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.generate import generate_drafts
    from spec2test.application.review import project_from_run

    requirements, documents = _spec(tmp_path)
    under_test = [r for r in requirements if r.external_id == "SYS-REQ-0001"]
    run = generate_drafts(
        under_test, RuleBasedFakeModel(), documents=documents, catalogue=requirements
    )
    project = project_from_run(
        run, requirements, list(documents.values()), name="demo"
    )

    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    reopened = ProjectStore(path).load()

    stored = reopened.runs[0].context_items
    assert len(stored) == 1
    assert isinstance(stored[0], ContextItem)
    assert stored[0].external_id == "SYS-REQ-0002"
    assert stored[0].document_version == "3.0.1"
    assert stored[0].text.strip().endswith("2.5 A.")


def test_context_can_be_switched_off(tmp_path):
    from spec2test.adapters.fake.model import ScriptedModel
    from spec2test.application.generate import GenerationSettings, generate_drafts

    requirements, documents = _spec(tmp_path)
    under_test = [r for r in requirements if r.external_id == "SYS-REQ-0001"]
    model = ScriptedModel(["not json"])

    run = generate_drafts(
        under_test,
        model,
        settings=GenerationSettings(use_reference_context=False, max_repair_attempts=0),
        documents=documents,
        catalogue=requirements,
    )

    assert run.context_items == []
    assert "SUPPORTING CONTEXT" not in model.calls[0][1].content


def test_a_value_found_only_in_the_context_names_its_source(tmp_path):
    """It is reported, not silently accepted: the attribution needs a review."""
    from spec2test.domain.models import TestCase, TestStep
    from spec2test.domain.validation import validate_test_case

    requirements, documents = _spec(tmp_path)
    under_test = [r for r in requirements if r.external_id == "SYS-REQ-0001"]
    item = ContextItem(
        requirement_id="R2",
        external_id="SYS-REQ-0002",
        text="SYS-REQ-0002: The current limit shall be 2.5 A.",
        document_id="doc-b",
        document_filename="b.md",
        document_version="3.0.1",
    )
    case = TestCase(
        case_id="TC-1",
        title="t",
        objective="o",
        requirement_ids=(under_test[0].requirement_id,),
        steps=(TestStep(1, "raise the load", ("the current is limited to 2.5 A",)),),
    )

    without = validate_test_case(case, under_test)
    assert "C_UNSUPPORTED_VALUE" in without.codes()

    with_context = validate_test_case(case, under_test, context=[item])
    assert "C_UNSUPPORTED_VALUE" not in with_context.codes()
    assert "C_VALUE_FROM_CONTEXT" in with_context.codes()
    finding = next(
        i for i in with_context.issues if i.code == "C_VALUE_FROM_CONTEXT"
    )
    assert "SYS-REQ-0002" in finding.message and "b.md" in finding.message
    assert with_context.blocks_draft is False
    assert with_context.blocks_approval is True, "the attribution still needs review"
