"""Domain tests: no Qt, no HTTP, no PDF, no Excel."""
from __future__ import annotations

import pytest

from spec2test.domain.models import (
    GenerationRun,
    GenerationUnitResult,
    Measurement,
    OpenQuestion,
    Requirement,
    ReviewStatus,
    RunStatus,
    Severity,
    SourceSpan,
    TestCase,
    TestStep,
    UnitStatus,
)
from spec2test.domain.validation import (
    may_approve,
    requirement_lookup,
    validate_test_case,
)


def span(doc="doc-1", start=0, end=40, quote="at most 0.2 deg at 50 km/h", page=1):
    return SourceSpan(document_id=doc, char_start=start, char_end=end, quote=quote, page=page)


def requirement(rid="REQ-1", ext="RAS-SW-REQ-0001", text="limit 0.2 deg at 50 km/h, DTC C1A27"):
    return Requirement(requirement_id=rid, external_id=ext, text=text, spans=(span(),))


def case(**kwargs):
    base = dict(
        case_id="TC-1",
        title="Check the limit",
        objective="verify the deviation",
        requirement_ids=("REQ-1",),
        steps=(
            TestStep(order=1, action="drive at 50 km/h", expectations=("deviation is 0.2 deg",)),
        ),
    )
    base.update(kwargs)
    return TestCase(**base)


# ------------------------------------------------------------------ basics ---
def test_a_valid_case_has_no_blocking_finding():
    report = validate_test_case(case(), [requirement()])
    assert report.blocks_draft is False
    assert report.errors == []


def test_empty_fields_are_errors():
    report = validate_test_case(case(title="  ", objective=""), [requirement()])
    assert report.blocks_draft is True
    assert report.codes() >= {"E_EMPTY_FIELD"}


def test_missing_requirement_reference_is_an_error():
    report = validate_test_case(case(requirement_ids=()), [requirement()])
    assert "E_NO_REQUIREMENT_REFS" in report.codes()
    assert report.blocks_draft is True


def test_unknown_requirement_reference_is_an_error():
    report = validate_test_case(case(requirement_ids=("REQ-DOES-NOT-EXIST",)), [requirement()])
    assert "E_UNKNOWN_REQUIREMENT_REF" in report.codes()
    assert report.blocks_draft is True


def test_duplicate_reference_is_reported_but_keeps_the_draft():
    report = validate_test_case(case(requirement_ids=("REQ-1", "REQ-1")), [requirement()])
    assert "C_DUPLICATE_REQUIREMENT_REF" in report.codes()
    assert report.blocks_draft is False
    assert report.blocks_approval is True


def test_a_case_without_steps_is_an_error():
    report = validate_test_case(case(steps=()), [requirement()])
    assert "E_NO_STEPS" in report.codes()
    assert report.blocks_draft is True


def test_a_step_without_an_expectation_is_an_error():
    report = validate_test_case(
        case(steps=(TestStep(order=1, action="do it"),)), [requirement()]
    )
    assert "E_STEP_WITHOUT_EXPECTATION" in report.codes()
    assert report.blocks_draft is True


def test_step_order_must_be_consecutive():
    report = validate_test_case(
        case(
            steps=(
                TestStep(order=1, action="a", expectations=("0.2 deg",)),
                TestStep(order=5, action="b", expectations=("0.2 deg",)),
            )
        ),
        [requirement()],
    )
    assert "E_STEP_ORDER" in report.codes()


def test_a_step_order_below_one_is_rejected_by_the_model_itself():
    with pytest.raises(ValueError):
        TestStep(order=0, action="x")


# ------------------------------------------- preconditions are steps too -----
def test_a_precondition_may_need_several_steps_and_is_not_assumed_to_succeed():
    report = validate_test_case(
        case(
            preconditions=(
                TestStep(order=1, action="power up the ECU", expectations=("ECU answers",)),
                TestStep(order=2, action="clear the DTC memory"),  # no verification
            )
        ),
        [requirement()],
    )
    # missing verification of a precondition is visible but does not kill the draft
    assert "C_STEP_WITHOUT_EXPECTATION" in report.codes()
    assert report.blocks_draft is False
    assert report.blocks_approval is True


# --------------------------------------------------- unsupported details -----
def test_an_invented_limit_is_reported_as_unsupported():
    report = validate_test_case(
        case(
            steps=(
                TestStep(
                    order=1,
                    action="drive",
                    expectations=("the angle stays below 7.5 deg",),
                ),
            )
        ),
        [requirement()],
    )
    assert "C_UNSUPPORTED_VALUE" in report.codes()
    assert report.blocks_draft is False, "a draft may be stored"
    assert report.blocks_approval is True, "but it must not be approved"


def test_a_value_present_in_the_requirement_is_accepted():
    report = validate_test_case(case(), [requirement()])
    assert "C_UNSUPPORTED_VALUE" not in report.codes()


def test_a_differently_written_but_identical_value_is_accepted():
    req = requirement(text="the limit is -10.0 deg")
    report = validate_test_case(
        case(
            steps=(
                TestStep(order=1, action="drive", expectations=("angle reaches -10 deg",)),
            )
        ),
        [req],
    )
    assert "C_UNSUPPORTED_VALUE" not in report.codes()


def test_an_invented_dtc_is_reported():
    report = validate_test_case(
        case(
            steps=(
                TestStep(order=1, action="inject", expectations=("DTC P0420 is stored",)),
            )
        ),
        [requirement()],
    )
    assert "C_UNSUPPORTED_DTC" in report.codes()


def test_a_measurement_without_evidence_is_reported():
    report = validate_test_case(
        case(
            steps=(
                TestStep(
                    order=1,
                    action="measure",
                    expectations=("deviation is 0.2 deg",),
                    measurements=(Measurement(quantity="deviation", value="0.2", unit="deg"),),
                ),
            )
        ),
        [requirement()],
    )
    assert "C_MEASUREMENT_WITHOUT_EVIDENCE" in report.codes()


def test_a_structural_expectation_without_a_criterion_gets_a_finding():
    report = validate_test_case(
        case(
            steps=(
                TestStep(order=1, action="drive", expectations=("the system behaves well",)),
            )
        ),
        [requirement()],
    )
    assert "C_NO_MEASURABLE_CRITERION" in report.codes()
    assert report.blocks_draft is False


# ------------------------------------------------------- reference lookup ----
def test_an_external_id_used_in_two_documents_is_not_resolvable():
    first = Requirement("REQ-A", "text one", (span(doc="doc-a"),), external_id="SHARED-REQ-1")
    second = Requirement("REQ-B", "text two", (span(doc="doc-b"),), external_id="SHARED-REQ-1")
    lookup = requirement_lookup([first, second])
    assert "SHARED-REQ-1" not in lookup, "an ambiguous external ID must not resolve"
    assert lookup["REQ-A"] is first and lookup["REQ-B"] is second

    report = validate_test_case(case(requirement_ids=("SHARED-REQ-1",)), [first, second])
    assert "E_UNKNOWN_REQUIREMENT_REF" in report.codes()


def test_an_unambiguous_external_id_resolves():
    req = requirement()
    assert requirement_lookup([req])["RAS-SW-REQ-0001"] is req


# -------------------------------------------------------------- approval -----
def test_formal_validity_is_not_approval():
    valid = case()
    assert valid.review_status is ReviewStatus.DRAFT
    allowed, report = may_approve(valid, [requirement()])
    assert allowed is True and report.blocks_draft is False
    # a present requirement ID proves nothing about technical correctness:
    # approval stays a deliberate tester action
    assert valid.with_status(ReviewStatus.APPROVED).review_status is ReviewStatus.APPROVED


def test_open_questions_block_approval():
    with_question = case(
        open_questions=(
            OpenQuestion(question_id="Q1", question="which limit applies?"),
        )
    )
    allowed, report = may_approve(with_question, [requirement()])
    assert allowed is False
    assert "C_OPEN_QUESTIONS" in report.codes()


def test_an_edit_after_approval_creates_a_new_revision_and_drops_the_approval():
    approved = case().with_status(ReviewStatus.APPROVED)
    edited = approved.next_revision(title="Check the limit again")
    assert edited.revision == 2
    assert edited.review_status is ReviewStatus.DRAFT


# ------------------------------------------------------------- run status ----
def _unit(status, unit_id="U1"):
    return GenerationUnitResult(
        unit_id=unit_id, requirement_ids=("REQ-1",), status=status
    )


def _run():
    return GenerationRun(
        run_id="r1",
        model="m",
        endpoint="e",
        mode="fake",
        prompt_version="p",
        schema_version="1.0",
        source_hash="h",
    )


def test_run_status_completed_partial_failed_cancelled():
    accepted = _unit(UnitStatus.ACCEPTED)
    accepted.test_cases = [case()]

    run = _run()
    run.units = [accepted]
    assert run.resolve_status() is RunStatus.COMPLETED

    run = _run()
    run.units = [accepted, _unit(UnitStatus.REJECTED, "U2")]
    assert run.resolve_status() is RunStatus.PARTIAL

    run = _run()
    run.units = [_unit(UnitStatus.REJECTED)]
    assert run.resolve_status() is RunStatus.FAILED

    run = _run()
    assert run.resolve_status() is RunStatus.FAILED, "a run without units is not a success"

    run = _run()
    run.units = [accepted]
    assert run.resolve_status(cancelled=True) is RunStatus.CANCELLED


def test_rejected_units_stay_in_the_report_with_their_source_reference():
    rejected = _unit(UnitStatus.REJECTED)
    rejected.source_spans = (span(),)
    rejected.issues = [
        __import__("spec2test.domain.models", fromlist=["ValidationIssue"]).ValidationIssue(
            "E_INVALID_JSON", Severity.ERROR, "$", "broken"
        )
    ]
    accepted = _unit(UnitStatus.ACCEPTED, "U2")
    accepted.test_cases = [case()]
    run = _run()
    run.units = [accepted, rejected]
    run.resolve_status()

    report = "\n".join(run.report_lines())
    assert "partial" in report
    assert "REJECTED U1" in report
    assert "doc-1" in report and "page 1" in report
    assert "E_INVALID_JSON" in report


# ------------------------------------------- values written as LaTeX ---------
@pytest.mark.parametrize(
    "written,expected",
    [
        (r"$50\text{ ms}$", (50.0, "ms")),
        (r"$50\,\text{ms}$", (50.0, "ms")),
        (r"$\pm 1.0^\circ\text{C}$", (1.0, "degc")),
        (r"$100\,\mu\Omega$", (100.0, "µohm")),
        (r"$2.5\text{ A}$", (2.5, "a")),
        ("plain 50 ms", (50.0, "ms")),
    ],
)
def test_values_are_read_whatever_notation_the_document_uses(written, expected):
    from spec2test.domain.validation import _numeric_claims

    assert expected in _numeric_claims(f"the limit is {written} exactly")


def test_a_correctly_quoted_latex_value_is_not_reported_as_unsupported():
    """Otherwise a real specification floods the reviewer with false findings."""
    text = r"SYS-REQ-1: the reaction shall take $50\text{ ms} \pm 2\text{ ms}$."
    requirement = Requirement(
        "REQ-1", text, (SourceSpan("doc", 0, len(text), text, page=1),), "SYS-REQ-1"
    )
    drafted = case(
        requirement_ids=("REQ-1",),
        steps=(TestStep(1, "trigger", ("the reaction takes 50 ms",)),),
    )
    report = validate_test_case(drafted, [requirement])
    assert "C_UNSUPPORTED_VALUE" not in report.codes()


def test_an_invented_value_is_still_reported_next_to_latex():
    text = r"SYS-REQ-1: the reaction shall take $50\text{ ms}$."
    requirement = Requirement(
        "REQ-1", text, (SourceSpan("doc", 0, len(text), text, page=1),), "SYS-REQ-1"
    )
    drafted = case(
        requirement_ids=("REQ-1",),
        steps=(TestStep(1, "trigger", ("the reaction takes 10 ms",)),),
    )
    assert "C_UNSUPPORTED_VALUE" in validate_test_case(drafted, [requirement]).codes()
