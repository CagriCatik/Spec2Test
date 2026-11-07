"""Every concrete expectation says where it comes from, and that is checked.

The motivating case is real. A draft stated a 250 mV comparator threshold
and a 255 mV test pulse side by side; the report claimed the arithmetic
2550 A x 100 uOhm = 255 mV supported both. It supports exactly one of them,
and the 250 mV belongs to a different current (2500 A). Nothing in the
record made that checkable. Now it is.
"""
from __future__ import annotations

import json

import pytest

from spec2test.application.assembly import build_from_response
from spec2test.application.contracts import SCHEMA_VERSION, parse_model_response
from spec2test.domain.grounding import check_calculation, check_grounds
from spec2test.domain.models import (
    ContextItem,
    ContextKind,
    Ground,
    GroundInput,
    GroundKind,
    Measurement,
    Requirement,
    SelectionReason,
    Severity,
    SourceSpan,
    TestCase,
    TestStep,
)
from spec2test.domain.validation import validate_test_case

REQ_TEXT = (
    "SYS-REQ-0001: The comparator is referenced against "
    "HW_Comparator_Reference_Voltage of 250.0 mV, corresponding to 2500 A. "
    "The voltage across the primary 100 uOhm current shunt is amplified. "
    "Total response time to hardware latch assertion: 2.0 us. A pulse "
    "simulating 2550 A is applied to the shunt terminals."
)


def requirement() -> Requirement:
    return Requirement(
        requirement_id="REQ-1",
        text=REQ_TEXT,
        external_id="SYS-REQ-0001",
        spans=(SourceSpan("spec.md", 0, len(REQ_TEXT), REQ_TEXT, section="1"),),
    )


def context_item(text="The arc chamber quenches the arc within 3 ms.") -> ContextItem:
    return ContextItem(
        requirement_id="spec.md:sec:900",
        external_id="SYS-REQ-0099",
        text=text,
        document_id="spec.md",
        spans=(SourceSpan("spec.md", 900, 900 + len(text), text),),
        cited_by=("SYS-REQ-0001",),
        kind=ContextKind.EXPLANATION,
        reason=SelectionReason.FULL_TEXT_SEARCH,
    )


def case_with(*grounds: Ground, expectations=("the latch asserts within 2.0 us",)):
    return TestCase(
        case_id="TC-1",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        steps=(
            TestStep(1, "inject the pulse", tuple(expectations), grounds=grounds),
        ),
    )


def codes(issues):
    return {i.code for i in issues}


# ============================================================ calculations ==
def test_a_correct_calculation_passes():
    ground = Ground(
        claim="255 mV",
        kind=GroundKind.CALCULATION,
        inputs=(
            GroundInput("2550 A", "SYS-REQ-0001", "simulating 2550 A"),
            GroundInput("100 uOhm", "SYS-REQ-0001", "100 uOhm current shunt"),
        ),
        formula="I * R",
    )
    assert check_calculation(ground) is None


def test_the_conflation_that_started_this_is_caught():
    """2550 A x 100 uOhm is 255 mV, not the 250 mV threshold."""
    ground = Ground(
        claim="250 mV",
        kind=GroundKind.CALCULATION,
        inputs=(
            GroundInput("2550 A", "SYS-REQ-0001"),
            GroundInput("100 uOhm", "SYS-REQ-0001"),
        ),
    )
    problem = check_calculation(ground)
    assert problem is not None
    assert "255" in problem or "0.255" in problem


@pytest.mark.parametrize(
    "claim,inputs",
    [
        ("0.255 V", [("2550 A", 1), ("100 uOhm", 1)]),
        ("2 us", [("2.0 us", 1)]),
        ("25.5 A", [("255 mV", 1), ("10 mOhm", 1)]),
    ],
)
def test_units_are_reduced_before_comparing(claim, inputs):
    ground = Ground(
        claim=claim,
        kind=GroundKind.CALCULATION,
        inputs=tuple(GroundInput(v, "SYS-REQ-0001") for v, _ in inputs),
    )
    assert check_calculation(ground) is None, check_calculation(ground)


def test_an_unreadable_value_is_reported_not_accepted():
    ground = Ground(
        claim="about half the rail",
        kind=GroundKind.CALCULATION,
        inputs=(GroundInput("12 V", "SYS-REQ-0001"),),
    )
    assert "not a number" in (check_calculation(ground) or "")


def test_a_calculation_without_inputs_is_refused_outright():
    with pytest.raises(ValueError, match="no input values"):
        Ground(claim="255 mV", kind=GroundKind.CALCULATION)


def test_an_input_without_a_source_is_refused_outright():
    with pytest.raises(ValueError, match="needs a source"):
        GroundInput(value="2550 A", source="  ")


# ================================================================ sources ===
def test_a_quoted_requirement_passes():
    ground = Ground(
        claim="2.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001",
        quote="Total response time to hardware latch assertion: 2.0 us",
    )
    assert codes(check_grounds(case_with(ground), [requirement()])) <= {
        "I_CLAIM_WITHOUT_GROUND"
    }


def test_a_quote_that_is_not_there_blocks_approval_not_the_draft():
    ground = Ground(
        claim="1.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001",
        quote="Total response time: 1.0 us",
    )
    issues = check_grounds(case_with(ground), [requirement()])
    assert "C_GROUND_QUOTE_NOT_FOUND" in codes(issues)
    wrong = next(i for i in issues if i.code == "C_GROUND_QUOTE_NOT_FOUND")
    assert wrong.severity is Severity.CLARIFICATION
    assert not any(i.severity is Severity.ERROR for i in issues), (
        "a wrong ground must not stop the draft from being saved"
    )


def test_an_unknown_source_is_an_error():
    ground = Ground(
        claim="2.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-9999",
        quote="whatever",
    )
    assert "C_GROUND_SOURCE_UNKNOWN" in codes(
        check_grounds(case_with(ground), [requirement()])
    )


def test_a_source_that_is_only_in_the_context_resolves(tmp_path):
    ground = Ground(
        claim="3 ms",
        kind=GroundKind.CONTEXT,
        source="SYS-REQ-0099",
        quote="quenches the arc within 3 ms",
    )
    assert codes(
        check_grounds(case_with(ground), [requirement()], context=[context_item()])
    ) <= {"I_CLAIM_WITHOUT_GROUND"}
    # ... and is an error when that context was not sent
    assert "C_GROUND_SOURCE_UNKNOWN" in codes(
        check_grounds(case_with(ground), [requirement()])
    )


def test_a_table_prefix_counts_as_part_of_its_source():
    """A row group's column header is sent, so a quote may rely on it."""
    item = context_item("| 0x9A0201 | Cell Voltage Sense Line Open | 1000 ms |")
    item = ContextItem(
        requirement_id=item.requirement_id,
        external_id=item.external_id,
        text=item.text,
        document_id=item.document_id,
        spans=item.spans,
        cited_by=item.cited_by,
        kind=ContextKind.SIGNAL,
        reason=SelectionReason.FULL_TEXT_SEARCH,
        prefix="| DTC Hex Code | Diagnostic Name | Debounce Time |",
    )
    ground = Ground(
        claim="1000 ms",
        kind=GroundKind.CONTEXT,
        source="SYS-REQ-0099",
        quote="DTC Hex Code | Diagnostic Name | Debounce Time",
    )
    assert codes(
        check_grounds(case_with(ground), [requirement()], context=[item])
    ) <= {"I_CLAIM_WITHOUT_GROUND"}


def test_markdown_and_whitespace_do_not_decide_a_quote():
    ground = Ground(
        claim="2.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001",
        quote="  Total   response time  to hardware   latch assertion:  2.0 us ",
    )
    assert codes(check_grounds(case_with(ground), [requirement()])) <= {
        "I_CLAIM_WITHOUT_GROUND"
    }


def test_a_source_without_a_quote_is_a_clarification():
    ground = Ground(
        claim="2.0 us", kind=GroundKind.TARGET_REQUIREMENT, source="SYS-REQ-0001"
    )
    issues = check_grounds(case_with(ground), [requirement()])
    assert "C_GROUND_WITHOUT_QUOTE" in codes(issues)
    assert issues[0].severity is Severity.CLARIFICATION


# ============================================================= unresolved ===
def test_an_unresolved_ground_must_carry_a_question():
    ground = Ground(claim="the rise time", kind=GroundKind.UNRESOLVED)
    issues = check_grounds(case_with(ground), [requirement()])
    assert "C_GROUND_UNRESOLVED_WITHOUT_QUESTION" in codes(issues)


def test_an_unresolved_ground_with_a_question_is_accepted():
    """A correctly named gap is right behaviour, not a defect."""
    ground = Ground(
        claim="the rise time",
        kind=GroundKind.UNRESOLVED,
        question="Which rise time should the injected pulse have?",
    )
    assert codes(check_grounds(case_with(ground), [requirement()])) <= {
        "I_CLAIM_WITHOUT_GROUND"
    }


# ============================================== the rule inside validation ==
def test_a_draft_with_values_and_no_grounds_is_noted_but_not_blocked():
    """Drafts written before this was asked for must stay approvable."""
    report = validate_test_case(case_with(), [requirement()])
    assert "I_NO_GROUNDS_STATED" in report.codes()
    issue = next(i for i in report.issues if i.code == "I_NO_GROUNDS_STATED")
    assert issue.severity is Severity.INFO
    assert not any(
        i.code == "I_NO_GROUNDS_STATED"
        for i in report.issues
        if i.severity is not Severity.INFO
    )


def test_a_draft_without_any_concrete_value_is_not_nagged():
    case = TestCase(
        case_id="TC-2",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        steps=(TestStep(1, "observe the state", ("the system reaches standby",)),),
    )
    assert "I_NO_GROUNDS_STATED" not in validate_test_case(case, [requirement()]).codes()


def test_a_wrong_ground_blocks_approval():
    from spec2test.domain.validation import may_approve

    ground = Ground(
        claim="1.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001",
        quote="Total response time: 1.0 us",
    )
    allowed, report = may_approve(case_with(ground), [requirement()])
    assert allowed is False
    assert "C_GROUND_QUOTE_NOT_FOUND" in report.codes()


def test_a_measurement_ground_is_checked_too():
    case = TestCase(
        case_id="TC-3",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        steps=(
            TestStep(
                1,
                "measure",
                ("the latch asserts",),
                (Measurement("response time", "2.0", "us", evidence="2.0 us"),),
                grounds=(
                    Ground(
                        claim="2.0 us",
                        kind=GroundKind.TARGET_REQUIREMENT,
                        source="SYS-REQ-0001",
                        quote="not in the requirement at all",
                    ),
                ),
            ),
        ),
    )
    assert "C_GROUND_QUOTE_NOT_FOUND" in validate_test_case(case, [requirement()]).codes()


# ================================================= contract and round trip ==
def _payload(grounds):
    return {
        "schema_version": SCHEMA_VERSION,
        "test_cases": [
            {
                "title": "t",
                "objective": "o",
                "requirement_refs": ["SYS-REQ-0001"],
                "steps": [
                    {
                        "order": 1,
                        "action": "a",
                        "expectations": ["the latch asserts within 2.0 us"],
                        "grounds": grounds,
                    }
                ],
            }
        ],
    }


def test_the_model_may_supply_grounds():
    payload = _payload(
        [
            {
                "claim": "2.0 us",
                "kind": "target_requirement",
                "source": "SYS-REQ-0001",
                "quote": "Total response time to hardware latch assertion: 2.0 us",
            }
        ]
    )
    result = parse_model_response(json.dumps(payload))
    assert result.ok, result.error_summary()
    cases, _questions = build_from_response(
        result.response, [requirement()], unit_id="u"
    )
    ground = cases[0].steps[0].grounds[0]
    assert ground.kind is GroundKind.TARGET_REQUIREMENT
    assert ground.source == "SYS-REQ-0001"
    assert check_grounds(cases[0], [requirement()]) == []


def test_a_calculation_from_the_model_is_recomputed():
    payload = _payload(
        [
            {
                "claim": "250 mV",
                "kind": "calculation",
                "formula": "I * R",
                "inputs": [
                    {"value": 2550, "source": "SYS-REQ-0001"},
                    {"value": "100 uOhm", "source": "SYS-REQ-0001"},
                ],
            }
        ]
    )
    result = parse_model_response(json.dumps(payload))
    assert result.ok, result.error_summary()
    cases, _questions = build_from_response(
        result.response, [requirement()], unit_id="u"
    )
    assert "C_GROUND_CALCULATION_NOT_REPRODUCIBLE" in {
        i.code for i in check_grounds(cases[0], [requirement()])
    }


def test_an_invented_kind_is_refused():
    from spec2test.application.contracts import ParseOutcome

    result = parse_model_response(json.dumps(_payload([{"claim": "x", "kind": "vibes"}])))
    assert result.outcome is ParseOutcome.SCHEMA_INVALID


def test_grounds_survive_being_saved_and_reopened(tmp_path):
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.review import project_from_run
    from spec2test.domain.models import DocumentFormat, GenerationRun, SourceDocument
    from spec2test.domain.models import GenerationUnitResult, RunStatus, UnitStatus

    ground = Ground(
        claim="255 mV",
        kind=GroundKind.CALCULATION,
        inputs=(
            GroundInput("2550 A", "SYS-REQ-0001", "simulating 2550 A"),
            GroundInput("100 uOhm", "SYS-REQ-0001", "100 uOhm current shunt"),
        ),
        formula="I * R",
    )
    case = case_with(ground)
    run = GenerationRun(
        run_id="r1", model="m", endpoint="e", mode="fake",
        prompt_version="p", schema_version=SCHEMA_VERSION, source_hash="h",
        status=RunStatus.COMPLETED,
        units=[
            GenerationUnitResult(
                unit_id="u1",
                requirement_ids=("REQ-1",),
                status=UnitStatus.ACCEPTED,
                test_cases=[case],
            )
        ],
    )
    document = SourceDocument(
        document_id="spec.md", filename="spec.md", format=DocumentFormat.MARKDOWN,
        content_hash="h", text=REQ_TEXT,
    )
    project = project_from_run(run, [requirement()], [document], name="p")
    target = tmp_path / "p.s2t.json"
    ProjectStore(target).save(project)

    reopened = ProjectStore(target).load()
    restored = reopened.test_cases[0].steps[0].grounds[0]
    assert restored.kind is GroundKind.CALCULATION
    assert [i.value for i in restored.inputs] == ["2550 A", "100 uOhm"]
    assert restored.formula == "I * R"
    assert check_calculation(restored) is None


def test_a_step_can_look_up_the_ground_for_a_claim():
    ground = Ground(
        claim="2.0 us", kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001", quote="2.0 us",
    )
    step = TestStep(1, "a", ("x",), grounds=(ground,))
    assert step.ground_for("2.0 us") is ground
    assert step.ground_for("3.0 us") is None


# ==================================================== the report on drafts ==
def test_the_cli_reports_grounds_of_a_stored_project(tmp_path, capsys):
    """Existing projects are read, never rewritten."""
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.review import project_from_run
    from spec2test.cli import main
    from spec2test.domain.models import (
        DocumentFormat,
        GenerationRun,
        GenerationUnitResult,
        RunStatus,
        SourceDocument,
        UnitStatus,
    )

    good = Ground(
        claim="2.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001",
        quote="Total response time to hardware latch assertion: 2.0 us",
    )
    bad = Ground(
        claim="1.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001",
        quote="Total response time: 1.0 us",
    )
    cases = [case_with(good), case_with(bad), case_with()]
    for index, case in enumerate(cases, 1):
        object.__setattr__(case, "case_id", f"TC-{index}")
    run = GenerationRun(
        run_id="r1", model="m", endpoint="e", mode="fake", prompt_version="p",
        schema_version=SCHEMA_VERSION, source_hash="h", status=RunStatus.COMPLETED,
        units=[
            GenerationUnitResult(
                unit_id="u1",
                requirement_ids=("REQ-1",),
                status=UnitStatus.ACCEPTED,
                test_cases=cases,
            )
        ],
    )
    document = SourceDocument(
        document_id="spec.md", filename="spec.md", format=DocumentFormat.MARKDOWN,
        content_hash="h", text=REQ_TEXT,
    )
    target = tmp_path / "p.s2t.json"
    before = None
    ProjectStore(target).save(
        project_from_run(run, [requirement()], [document], name="p")
    )
    before = target.read_bytes()

    assert main(["grounds", str(target)]) == 0
    out = capsys.readouterr().out
    assert "3 draft(s)" in out
    assert "grounds stated: 2" in out
    assert "drafts without any ground: 1" in out
    assert "grounds that do not check out:" in out
    assert "C_GROUND_QUOTE_NOT_FOUND" in out
    assert target.read_bytes() == before, "the report must not rewrite the project"


def test_the_cli_says_so_when_the_project_cannot_be_read(tmp_path, capsys):
    from spec2test.cli import main

    assert main(["grounds", str(tmp_path / "missing.s2t.json")]) == 2
    assert "could not be read" in capsys.readouterr().out


def test_the_specification_shows_where_the_values_come_from(tmp_path):
    from spec2test.adapters.export import to_markdown
    from spec2test.application.review import project_from_run
    from spec2test.application.specification import build_specification
    from spec2test.domain.models import (
        DocumentFormat,
        GenerationRun,
        GenerationUnitResult,
        RunStatus,
        SourceDocument,
        UnitStatus,
    )

    case = case_with(
        Ground(
            claim="2.0 us",
            kind=GroundKind.TARGET_REQUIREMENT,
            source="SYS-REQ-0001",
            quote="Total response time to hardware latch assertion: 2.0 us",
        ),
        Ground(
            claim="255 mV",
            kind=GroundKind.CALCULATION,
            inputs=(
                GroundInput("2550 A", "SYS-REQ-0001"),
                GroundInput("100 uOhm", "SYS-REQ-0001"),
            ),
            formula="I * R",
        ),
        Ground(
            claim="the rise time",
            kind=GroundKind.UNRESOLVED,
            question="Which rise time should the pulse have?",
        ),
    )
    run = GenerationRun(
        run_id="r1", model="m", endpoint="e", mode="fake", prompt_version="p",
        schema_version=SCHEMA_VERSION, source_hash="h", status=RunStatus.COMPLETED,
        units=[
            GenerationUnitResult(
                unit_id="u1", requirement_ids=("REQ-1",),
                status=UnitStatus.ACCEPTED, test_cases=[case],
            )
        ],
    )
    document = SourceDocument(
        document_id="spec.md", filename="spec.md", format=DocumentFormat.MARKDOWN,
        content_hash="h", text=REQ_TEXT,
    )
    project = project_from_run(run, [requirement()], [document], name="p")
    markdown = to_markdown(build_specification(project))

    assert "Where the values come from" in markdown
    assert "target_requirement" in markdown and "SYS-REQ-0001" in markdown
    assert "I * R" in markdown
    assert "Which rise time should the pulse have?" in markdown


# ================================ the rule version decides how it is weighed ==
def _valued_case(**kwargs):
    return TestCase(
        case_id="TC-9",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        steps=(TestStep(1, "measure", ("the latch asserts within 2.0 us",)),),
        **kwargs,
    )


def test_a_stored_draft_from_before_the_rule_stays_approvable():
    """No retroactive claim that an old draft was ever checked."""
    from spec2test.domain.validation import may_approve

    case = _valued_case()
    assert case.is_legacy_grounding
    report = validate_test_case(case, [requirement()])
    assert "I_CLAIM_WITHOUT_GROUND" in report.codes()
    assert all(
        i.severity is Severity.INFO
        for i in report.issues
        if i.code.endswith("CLAIM_WITHOUT_GROUND")
    )
    allowed, _ = may_approve(case, [requirement()])
    assert allowed, "an old draft must not become unapprovable overnight"


def test_a_draft_written_under_the_rule_needs_grounds_to_be_approved():
    from spec2test.domain.models import GROUNDING_RULES_VERSION
    from spec2test.domain.validation import may_approve

    case = _valued_case(grounding_rules=GROUNDING_RULES_VERSION)
    report = validate_test_case(case, [requirement()])
    assert "C_CLAIM_WITHOUT_GROUND" in report.codes()
    allowed, _ = may_approve(case, [requirement()])
    assert allowed is False


def test_such_a_draft_can_still_be_saved():
    """Only approval is stricter; a draft is still worth keeping."""
    from spec2test.domain.models import GROUNDING_RULES_VERSION

    report = validate_test_case(
        _valued_case(grounding_rules=GROUNDING_RULES_VERSION), [requirement()]
    )
    assert report.blocks_draft is False
    assert report.blocks_approval is True


def test_a_documented_tester_reason_releases_it():
    """The explicit justification the rule allows instead of a ground."""
    from spec2test.domain.models import AcceptedFinding, GROUNDING_RULES_VERSION
    from spec2test.domain.validation import may_approve

    case = _valued_case(grounding_rules=GROUNDING_RULES_VERSION)
    report = validate_test_case(case, [requirement()])
    issue = next(i for i in report.issues if i.code == "C_CLAIM_WITHOUT_GROUND")
    accepted = TestCase(
        case_id=case.case_id,
        title=case.title,
        objective=case.objective,
        requirement_ids=case.requirement_ids,
        steps=case.steps,
        grounding_rules=GROUNDING_RULES_VERSION,
        accepted_findings=(
            AcceptedFinding(
                code=issue.code,
                path=issue.path,
                justification="agreed with the system owner on 2026-11-07",
                by="anna",
                at="2026-11-07T10:00:00+00:00",
            ),
        ),
    )
    allowed, _report = may_approve(accepted, [requirement()])
    assert allowed, "a documented reason must be a way through"


def test_only_a_technical_revision_raises_the_rule_version():
    case = _valued_case()
    assert case.next_revision(title="a better title").grounding_rules is None
    revised = case.next_revision(
        steps=(TestStep(1, "measure", ("the latch asserts within 1.0 us",)),)
    )
    assert revised.grounding_rules == "1"


def test_a_generated_draft_is_held_to_the_current_rule():
    from spec2test.domain.models import GROUNDING_RULES_VERSION

    payload = _payload(
        [
            {
                "claim": "2.0 us",
                "kind": "target_requirement",
                "source": "SYS-REQ-0001",
                "quote": "Total response time to hardware latch assertion: 2.0 us",
            }
        ]
    )
    result = parse_model_response(json.dumps(payload))
    cases, _ = build_from_response(result.response, [requirement()], unit_id="u")
    assert cases[0].grounding_rules == GROUNDING_RULES_VERSION


# ========================= one ground does not cover the whole test case =====
def test_a_ground_for_one_value_does_not_cover_another():
    """The hole the reviewer named: a quoted limit hiding three invented ones."""
    from spec2test.domain.models import GROUNDING_RULES_VERSION

    case = TestCase(
        case_id="TC-10",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=(
            TestStep(
                1,
                "measure",
                (
                    "the latch asserts within 2.0 us",
                    "the threshold is 250 mV",
                    "the recovery takes 500 ms",
                ),
                grounds=(
                    Ground(
                        claim="2.0 us",
                        kind=GroundKind.TARGET_REQUIREMENT,
                        source="SYS-REQ-0001",
                        quote="Total response time to hardware latch assertion: 2.0 us",
                    ),
                ),
            ),
        ),
    )
    issues = check_grounds(case, [requirement()])
    uncovered = {
        i.message.split("'")[1]
        for i in issues
        if i.code == "C_CLAIM_WITHOUT_GROUND"
    }
    assert "250 mv" in uncovered and "500 ms" in uncovered
    assert "2 µs" not in uncovered, "the grounded value must not be reported"


def test_a_ground_in_another_step_does_not_count():
    from spec2test.domain.models import GROUNDING_RULES_VERSION

    ground = Ground(
        claim="2.0 us",
        kind=GroundKind.TARGET_REQUIREMENT,
        source="SYS-REQ-0001",
        quote="Total response time to hardware latch assertion: 2.0 us",
    )
    case = TestCase(
        case_id="TC-11",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=(
            TestStep(1, "prepare", (), grounds=(ground,)),
            TestStep(2, "measure", ("the latch asserts within 2.0 us",)),
        ),
    )
    paths = {
        i.path for i in check_grounds(case, [requirement()])
        if i.code == "C_CLAIM_WITHOUT_GROUND"
    }
    assert "TC-11.steps[1].expectations[0]" in paths


def test_the_same_value_written_differently_still_counts_as_grounded():
    from spec2test.domain.models import GROUNDING_RULES_VERSION

    case = TestCase(
        case_id="TC-12",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=(
            TestStep(
                1,
                "measure",
                ("the latch asserts within 2000 ns",),
                grounds=(
                    Ground(
                        claim="2.0 us",
                        kind=GroundKind.TARGET_REQUIREMENT,
                        source="SYS-REQ-0001",
                        quote="Total response time to hardware latch assertion: 2.0 us",
                    ),
                ),
            ),
        ),
    )
    assert "C_CLAIM_WITHOUT_GROUND" not in codes(check_grounds(case, [requirement()]))


def test_prose_without_a_value_needs_no_ground():
    from spec2test.domain.models import GROUNDING_RULES_VERSION

    case = TestCase(
        case_id="TC-13",
        title="t",
        objective="o",
        requirement_ids=("SYS-REQ-0001",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=(TestStep(1, "observe", ("the system returns to a safe state",)),),
    )
    assert check_grounds(case, [requirement()]) == []
