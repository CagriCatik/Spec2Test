"""The stored context must reach every later validation.

Generation validates with the context it sent, so a value taken from a
retrieved passage becomes C_VALUE_FROM_CONTEXT. Review and export revalidated
without it and turned the same value into C_UNSUPPORTED_VALUE, losing the
provenance the run had recorded.
"""
from __future__ import annotations

import json

import pytest

from spec2test.adapters.fake.model import ScriptedModel
from spec2test.adapters.knowledge import SqliteKnowledgeIndex
from spec2test.application.contracts import SCHEMA_VERSION
from spec2test.application.generate import GenerationSettings, generate_drafts
from spec2test.application.indexing import sync_index
from spec2test.application.review import (
    accept_finding,
    approval_report,
    context_for_case,
    project_from_run,
    validation_overview,
)
from spec2test.application.specification import build_specification
from spec2test.bootstrap import build_importers

TARGET = """# Current validation

### Requirement: SYS-REQ-0001
* **Title:** Pack current validation
* The BMS shall validate the pack current from `Shunt_Current_Raw` and
  `Hall_Current_Raw` and raise `Current_Sensor_Plausibility_Error`.
"""

SIGNALS = """# Signal definitions

## Signals
`Shunt_Current_Raw` is sampled with a period of 10 ms and a tolerance of 2 A.
`Hall_Current_Raw` follows the same period.
"""

ANSWER = {
    "schema_version": SCHEMA_VERSION,
    "test_cases": [
        {
            "title": "Validate pack current sampling",
            "objective": "Check the sampling period against the signal section.",
            "requirement_refs": ["SYS-REQ-0001"],
            "steps": [
                {
                    "order": 1,
                    "action": "sample Shunt_Current_Raw",
                    # 10 ms appears only in the retrieved signal section
                    "expectations": ["the sample period is 10 ms"],
                }
            ],
        }
    ],
}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "a_target.md").write_text(TARGET, encoding="utf-8")
    (tmp_path / "b_signals.md").write_text(SIGNALS, encoding="utf-8")
    paths = sorted(tmp_path.glob("*.md"))
    index = SqliteKnowledgeIndex(tmp_path / "k.db")
    synced = sync_index(index, paths, build_importers())
    requirements = list(synced.requirements)
    target = [r for r in requirements if r.external_id == "SYS-REQ-0001"]

    run = generate_drafts(
        target,
        ScriptedModel([json.dumps(ANSWER)] * 8),
        settings=GenerationSettings(
            group_size=1, max_search_items=3, use_retrieval=True
        ),
        documents=synced.documents,
        catalogue=requirements,
        knowledge=index,
    )
    unit = run.units[0]
    assert unit.context, "the fixture needs retrieved context to be meaningful"
    assert "C_VALUE_FROM_CONTEXT" in {i.code for i in unit.issues}, (
        f"generation did not report the value as taken from context: "
        f"{[i.code for i in unit.issues]}"
    )
    built = project_from_run(
        run, requirements, list(synced.documents.values()), name="revalidation"
    )
    return built, run


def test_the_review_sees_the_same_finding_as_the_generation(project):
    built, _run = project
    case = built.test_cases[0]
    _allowed, report = approval_report(built, case.case_id)
    codes = {i.code for i in report.issues}
    assert "C_VALUE_FROM_CONTEXT" in codes, codes
    assert "C_UNSUPPORTED_VALUE" not in codes, (
        "the review lost the context and called a sourced value unsupported"
    )


def test_the_specification_sees_the_same_finding(project):
    built, _run = project
    entry = build_specification(built).entries[0]
    codes = {i.code for i in entry.findings}
    assert "C_VALUE_FROM_CONTEXT" in codes, codes
    assert "C_UNSUPPORTED_VALUE" not in codes, codes
    assert entry.context, "the entry carries the context it was validated with"


def test_the_overview_sees_the_same_finding(project):
    built, _run = project
    case = built.test_cases[0]
    codes = {i.code for i in validation_overview(built)[case.case_id].issues}
    assert "C_VALUE_FROM_CONTEXT" in codes, codes
    assert "C_UNSUPPORTED_VALUE" not in codes, codes


def test_a_finding_can_be_accepted_under_the_code_the_review_shows(project):
    """Accepting failed: the review named a code generation never produced."""
    built, _run = project
    case = built.test_cases[0]
    _allowed, report = approval_report(built, case.case_id)
    issue = next(i for i in report.issues if i.code == "C_VALUE_FROM_CONTEXT")

    updated = accept_finding(
        built,
        case.case_id,
        issue.code,
        issue.path,
        justification="the signal section is the agreed source for this period",
    )
    assert (issue.code, issue.path) in updated.accepted_keys


def test_context_for_case_returns_what_the_unit_sent(project):
    built, run = project
    case = built.test_cases[0]
    stored = context_for_case(built, case.case_id)
    assert [c.requirement_id for c in stored] == [
        c.requirement_id for c in run.units[0].context
    ]


def test_an_unknown_case_has_no_context(project):
    built, _run = project
    assert context_for_case(built, "TC-does-not-exist") == []


def test_a_project_without_runs_still_validates(project):
    """A project whose runs were dropped must not lose the ability to review."""
    from dataclasses import replace

    built, _run = project
    stripped = replace(built, runs=[])
    case = stripped.test_cases[0]
    assert context_for_case(stripped, case.case_id) == []
    _allowed, report = approval_report(stripped, case.case_id)
    assert "C_UNSUPPORTED_VALUE" in {i.code for i in report.issues}, (
        "without the stored context the value is unsupported again - correctly"
    )
