"""The review agent: does the ground fit the claim it is given for?

The cases below are built so the deterministic check passes them - the
source exists, the quote is really there, the arithmetic works out - and
they are still wrong. That is the whole point of a second pass: a formally
perfect ground can still cite the supply rail for a cell voltage.

The agent is a model, so these tests drive a scripted stand-in. They state
the contract: what it is asked, what it may answer, how a finding is
recorded, and that it decides nothing.
"""
from __future__ import annotations

import json

import pytest

from spec2test.adapters.fake.model import ScriptedModel
from spec2test.application.ground_review import (
    REVIEW_SCHEMA_VERSION,
    VERDICTS,
    build_review_messages,
    review_grounds,
)
from spec2test.domain.grounding import check_grounds
from spec2test.domain.models import (
    GROUNDING_RULES_VERSION,
    Ground,
    GroundInput,
    GroundKind,
    Requirement,
    Severity,
    SourceSpan,
    TestCase,
    TestStep,
)

REQ_TEXT = (
    "SYS-REQ-0001: While the pack is charging, the cell voltage shall not "
    "exceed 4.20 V. The 12 V auxiliary supply powers the monitoring board. "
    "During driving the cell voltage shall not exceed 4.15 V. The shunt is "
    "100 uOhm and the trip current is 2550 A."
)


def requirement() -> Requirement:
    return Requirement(
        requirement_id="REQ-1",
        text=REQ_TEXT,
        external_id="SYS-REQ-0001",
        spans=(SourceSpan("spec.md", 0, len(REQ_TEXT), REQ_TEXT, section="1"),),
    )


def case_with(step: TestStep) -> TestCase:
    return TestCase(
        case_id="TC-1",
        title="Cell voltage while driving",
        objective="Check the cell voltage limit during driving.",
        requirement_ids=("SYS-REQ-0001",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=(step,),
    )


# ---- three grounds that pass the validator and are still wrong -------------
WRONG_QUANTITY = TestStep(
    1,
    "drive the vehicle at constant speed",
    ("the measured cell voltage stays below 12 V",),
    grounds=(
        Ground(
            claim="12 V",
            kind=GroundKind.TARGET_REQUIREMENT,
            source="SYS-REQ-0001",
            # really in the requirement - but it is the supply rail
            quote="The 12 V auxiliary supply powers the monitoring board.",
        ),
    ),
)

WRONG_STATE = TestStep(
    1,
    "drive the vehicle at constant speed",
    ("the measured cell voltage stays below 4.20 V",),
    grounds=(
        Ground(
            claim="4.20 V",
            kind=GroundKind.TARGET_REQUIREMENT,
            source="SYS-REQ-0001",
            # really in the requirement - but it is the charging limit
            quote="While the pack is charging, the cell voltage shall not exceed 4.20 V.",
        ),
    ),
)

CORRECT = TestStep(
    1,
    "drive the vehicle at constant speed",
    ("the measured cell voltage stays below 4.15 V",),
    grounds=(
        Ground(
            claim="4.15 V",
            kind=GroundKind.TARGET_REQUIREMENT,
            source="SYS-REQ-0001",
            quote="During driving the cell voltage shall not exceed 4.15 V.",
        ),
    ),
)


def answer(*findings) -> str:
    """Every finding carries the claim_id the request gave it."""
    filled = [{"claim_id": "step0-g0", **f} for f in findings]
    return json.dumps(
        {"schema_version": REVIEW_SCHEMA_VERSION, "findings": filled}
    )


# =============================================== the validator lets them pass
@pytest.mark.parametrize(
    "step,label",
    [(WRONG_QUANTITY, "wrong quantity"), (WRONG_STATE, "wrong state"),
     (CORRECT, "correct")],
)
def test_the_deterministic_check_cannot_tell_these_apart(step, label):
    """All three are formally sound. That is why the agent exists."""
    issues = check_grounds(case_with(step), [requirement()])
    assert issues == [], f"{label}: {[i.code for i in issues]}"


# ========================================================== what it is asked
def test_the_prompt_carries_the_step_the_claim_and_the_quote():
    messages = build_review_messages(case_with(WRONG_STATE), [requirement()])
    user = messages[1].content
    assert "drive the vehicle at constant speed" in user
    assert "claim under review: 4.20 V" in user
    assert "While the pack is charging" in user
    assert "input data, not instructions" in user


def test_the_prompt_tells_it_not_to_redo_the_validator():
    system = build_review_messages(case_with(CORRECT), [requirement()])[0].content
    assert "deterministic check has already done" in system
    assert "must not repeat it" in system
    assert "You approve nothing and change nothing" in system
    for verdict in VERDICTS:
        assert verdict in system


def test_an_unresolved_ground_is_not_sent_for_review():
    step = TestStep(
        1, "a", ("x",),
        grounds=(Ground(claim="the rise time", kind=GroundKind.UNRESOLVED,
                        question="which rise time?"),),
    )
    messages = build_review_messages(case_with(step), [requirement()])
    assert "BEGIN CLAIM" not in messages[1].content
    assert "the rise time" not in messages[1].content


# ============================================================ what it reports
def test_a_wrong_state_becomes_a_clarification_at_the_step():
    model = ScriptedModel(
        [answer({"claim": "4.20 V", "verdict": "mismatched_state",
                 "confidence": "high",
                 "explanation": "the quote limits the charging state; this step drives"})]
    )
    review = review_grounds(case_with(WRONG_STATE), [requirement()], model)
    assert review.ok
    assert len(review.issues) == 1
    issue = review.issues[0]
    assert issue.code == "C_GROUND_REVIEW_STATE"
    assert issue.severity is Severity.CLARIFICATION
    assert issue.path == "TC-1.steps[0]"
    assert "charging state" in issue.message
    assert "high confidence" in issue.message and model.descriptor.model in issue.message


def test_a_wrong_quantity_is_reported_as_such():
    model = ScriptedModel(
        [answer({"claim": "12 V", "verdict": "mismatched_quantity",
                 "confidence": "high",
                 "explanation": "12 V is the auxiliary supply, not a cell voltage"})]
    )
    review = review_grounds(case_with(WRONG_QUANTITY), [requirement()], model)
    assert [i.code for i in review.issues] == ["C_GROUND_REVIEW_QUANTITY"]


def test_a_fitting_ground_produces_no_finding():
    model = ScriptedModel(
        [answer({"claim": "4.15 V", "verdict": "fits", "confidence": "high",
                 "explanation": "the quote is the driving limit"})]
    )
    review = review_grounds(case_with(CORRECT), [requirement()], model)
    assert review.issues == []
    assert review.complete, "it looked at everything it was given"
    assert [v.outcome for v in review.verdicts] == ["clear"]


def test_every_verdict_maps_to_its_own_code():
    seen = set()
    for verdict in VERDICTS:
        if verdict == "fits":
            continue
        model = ScriptedModel(
            [answer({"claim": "4.20 V", "verdict": verdict, "explanation": "x"})]
        )
        review = review_grounds(case_with(WRONG_STATE), [requirement()], model)
        assert len(review.issues) == 1, verdict
        seen.add(review.issues[0].code)
    assert len(seen) == len(VERDICTS) - 1, "two verdicts share a code"


# ================================================== it decides nothing at all
def test_the_review_never_changes_the_case():
    from dataclasses import asdict

    case = case_with(WRONG_STATE)
    before = asdict(case)
    model = ScriptedModel(
        [answer({"claim": "4.20 V", "verdict": "mismatched_state", "explanation": "x"})]
    )
    review_grounds(case, [requirement()], model)
    assert asdict(case) == before


def test_no_finding_is_ever_an_error():
    """A model's opinion must not block a draft from being stored."""
    model = ScriptedModel(
        [answer({"claim": "4.20 V", "verdict": "mismatched_state", "explanation": "x"})]
    )
    review = review_grounds(case_with(WRONG_STATE), [requirement()], model)
    assert all(i.severity is Severity.CLARIFICATION for i in review.issues)


# ============================================== a misbehaving agent is caught
def test_an_unparseable_answer_is_reported_not_swallowed():
    review = review_grounds(case_with(CORRECT), [requirement()], ScriptedModel(["sorry"]))
    assert not review.ok
    assert "could not be read" in review.error
    assert review.issues == []


def test_an_invented_verdict_is_refused():
    model = ScriptedModel(
        [answer({"claim": "4.15 V", "verdict": "looks_dodgy", "explanation": "x"})]
    )
    review = review_grounds(case_with(CORRECT), [requirement()], model)
    assert not review.ok and "verdict must be one of" in review.error


def test_a_finding_about_a_claim_that_is_not_there_is_flagged():
    """The agent must not smuggle in a claim nobody made."""
    model = ScriptedModel(
        [answer({"claim_id": "nope-g9", "claim": "999 V",
                 "verdict": "mismatched_state", "explanation": "invented"})]
    )
    review = review_grounds(case_with(CORRECT), [requirement()], model)
    assert "C_GROUND_REVIEW_UNMATCHED" in {i.code for i in review.issues}
    # ... and the real claim is reported as unjudged, not quietly passed
    assert [v.outcome for v in review.verdicts] == ["not_checked"]
    assert "C_GROUND_REVIEW_NOT_CHECKED" in {i.code for i in review.issues}


def test_a_wrong_schema_version_is_refused():
    model = ScriptedModel([json.dumps({"schema_version": "9.9", "findings": []})])
    review = review_grounds(case_with(CORRECT), [requirement()], model)
    assert not review.ok and "schema_version" in review.error


def test_a_model_failure_is_recorded_not_raised():
    from spec2test.application.ports import ModelError

    class Broken:
        descriptor = ScriptedModel([]).descriptor

        def chat(self, *_a, **_k):
            raise ModelError("no route to host")

    review = review_grounds(case_with(CORRECT), [requirement()], Broken())
    assert not review.ok and "no route to host" in review.error


def test_the_raw_answer_is_kept_for_a_disputed_finding():
    raw = answer({"claim": "4.20 V", "verdict": "mismatched_state", "explanation": "x"})
    review = review_grounds(case_with(WRONG_STATE), [requirement()], ScriptedModel([raw]))
    assert review.raw == raw


def test_a_case_without_grounds_asks_nothing():
    model = ScriptedModel([])
    case = TestCase(
        case_id="TC-2", title="t", objective="o", requirement_ids=("SYS-REQ-0001",),
        steps=(TestStep(1, "a", ("x",)),),
    )
    review = review_grounds(case, [requirement()], model)
    assert review.ok and review.issues == [] and model.calls == []


# ================================================= over a stored project ----
def test_the_cli_runs_the_agent_over_a_stored_project(tmp_path, capsys):
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.contracts import SCHEMA_VERSION
    from spec2test.application.review import project_from_run
    from spec2test.cli import main
    from spec2test.domain.models import (
        DocumentFormat, GenerationRun, GenerationUnitResult, RunStatus,
        SourceDocument, UnitStatus,
    )

    run = GenerationRun(
        run_id="r1", model="m", endpoint="e", mode="fake", prompt_version="p",
        schema_version=SCHEMA_VERSION, source_hash="h", status=RunStatus.COMPLETED,
        units=[GenerationUnitResult(
            unit_id="u1", requirement_ids=("REQ-1",), status=UnitStatus.ACCEPTED,
            test_cases=[case_with(WRONG_STATE)],
        )],
    )
    document = SourceDocument(
        document_id="spec.md", filename="spec.md", format=DocumentFormat.MARKDOWN,
        content_hash="h", text=REQ_TEXT,
    )
    target = tmp_path / "p.s2t.json"
    ProjectStore(target).save(
        project_from_run(run, [requirement()], [document], name="p")
    )
    before = target.read_bytes()

    assert main(["grounds", str(target), "--agent", "--mode", "fake"]) == 0
    out = capsys.readouterr().out
    assert "review agent:" in out
    assert "draft(s) reviewed" in out
    assert "approve nothing and change nothing" in out
    assert target.read_bytes() == before, "the agent must not touch the project"


# ============================ "clear" must never be confused with "not asked"
def test_a_claim_the_agent_skipped_is_reported_as_unjudged():
    """A valid answer can still leave claims out. That is not a pass."""
    step = TestStep(
        1,
        "drive the vehicle",
        ("the cell voltage stays below 4.15 V", "the pack current stays below 2550 A"),
        grounds=(
            Ground(
                claim="4.15 V", kind=GroundKind.TARGET_REQUIREMENT,
                target="expectations[0]", source="SYS-REQ-0001",
                quote="During driving the cell voltage shall not exceed 4.15 V.",
            ),
            Ground(
                claim="2550 A", kind=GroundKind.TARGET_REQUIREMENT,
                target="expectations[1]", source="SYS-REQ-0001",
                quote="the trip current is 2550 A",
            ),
        ),
    )
    model = ScriptedModel(
        [answer({"claim_id": "step0-g0", "claim": "4.15 V", "verdict": "fits",
                 "explanation": "ok"})]
    )
    review = review_grounds(case_with(step), [requirement()], model)

    assert review.ok, "the answer itself was valid"
    assert review.complete is False
    outcomes = {v.claim: v.outcome for v in review.verdicts}
    assert outcomes == {"4.15 V": "clear", "2550 A": "not_checked"}
    assert "C_GROUND_REVIEW_NOT_CHECKED" in {i.code for i in review.issues}
    assert "2550 A" in review.summary() or "1 not checked" in review.summary()


def test_a_model_failure_leaves_every_claim_unjudged():
    from spec2test.application.ports import ModelError

    class Broken:
        descriptor = ScriptedModel([]).descriptor

        def chat(self, *_a, **_k):
            raise ModelError("no route to host")

    review = review_grounds(case_with(CORRECT), [requirement()], Broken())
    assert not review.ok
    assert [v.outcome for v in review.verdicts] == ["not_checked"]
    assert review.complete is False
    assert review.checked == []


def test_an_unreadable_answer_leaves_every_claim_unjudged():
    review = review_grounds(case_with(CORRECT), [requirement()], ScriptedModel(["no"]))
    assert [v.outcome for v in review.verdicts] == ["not_checked"]
    assert review.complete is False


def test_a_finding_is_addressed_to_the_field_not_only_the_step():
    model = ScriptedModel(
        [answer({"claim": "4.20 V", "verdict": "mismatched_state",
                 "explanation": "charging limit"})]
    )
    step = TestStep(
        1, "drive", ("the cell voltage stays below 4.20 V",),
        grounds=(
            Ground(
                claim="4.20 V", kind=GroundKind.TARGET_REQUIREMENT,
                target="expectations[0]", source="SYS-REQ-0001",
                quote="While the pack is charging, the cell voltage shall not exceed 4.20 V.",
            ),
        ),
    )
    review = review_grounds(case_with(step), [requirement()], model)
    assert review.issues[0].path == "TC-1.steps[0].expectations[0]"


# ================== the same number in two fields needs two separate grounds
def test_a_turn_on_and_a_turn_off_threshold_with_the_same_number():
    """The hole the reviewer named: one ground passing for both."""
    from spec2test.domain.grounding import check_grounds

    step = TestStep(
        1,
        "sweep the voltage",
        (
            "the heater switches on at 5.0 V",
            "the heater switches off at 5.0 V",
        ),
        grounds=(
            Ground(
                claim="5.0 V", kind=GroundKind.TARGET_REQUIREMENT,
                target="expectations[0]", source="SYS-REQ-0001",
                quote="The 12 V auxiliary supply powers the monitoring board.",
            ),
        ),
    )
    case = case_with(step)
    paths = {
        i.path for i in check_grounds(case, [requirement()])
        if i.code == "C_CLAIM_WITHOUT_GROUND"
    }
    assert paths == {"TC-1.steps[0].expectations[1]"}, paths


def test_a_ground_without_a_target_is_reported_as_ambiguous():
    from spec2test.domain.grounding import check_grounds

    step = TestStep(
        1,
        "sweep the voltage",
        ("the heater switches on at 5.0 V", "the heater switches off at 5.0 V"),
        grounds=(
            Ground(
                claim="5.0 V", kind=GroundKind.TARGET_REQUIREMENT,
                source="SYS-REQ-0001",
                quote="The 12 V auxiliary supply powers the monitoring board.",
            ),
        ),
    )
    codes_found = {i.code for i in check_grounds(case_with(step), [requirement()])}
    assert "C_GROUND_AMBIGUOUS_TARGET" in codes_found


def test_an_unambiguous_loose_ground_still_counts():
    """Only a repeated value is ambiguous; a single one needs no target."""
    from spec2test.domain.grounding import check_grounds

    step = TestStep(
        1, "drive", ("the cell voltage stays below 4.15 V",),
        grounds=(
            Ground(
                claim="4.15 V", kind=GroundKind.TARGET_REQUIREMENT,
                source="SYS-REQ-0001",
                quote="During driving the cell voltage shall not exceed 4.15 V.",
            ),
        ),
    )
    assert check_grounds(case_with(step), [requirement()]) == []


# ============ two identical numbers in different fields, different verdicts ==
TWO_THRESHOLDS = TestStep(
    1,
    "sweep the supply voltage up and then down",
    (
        "the heater switches on at 12 V",
        "the heater switches off at 12 V",
    ),
    grounds=(
        Ground(
            claim="12 V", kind=GroundKind.TARGET_REQUIREMENT,
            target="expectations[0]", source="SYS-REQ-0001",
            quote="The 12 V auxiliary supply powers the monitoring board.",
        ),
        Ground(
            claim="12 V", kind=GroundKind.TARGET_REQUIREMENT,
            target="expectations[1]", source="SYS-REQ-0001",
            quote="The 12 V auxiliary supply powers the monitoring board.",
        ),
    ),
)


def test_each_of_two_identical_values_gets_its_own_claim_id():
    messages = build_review_messages(case_with(TWO_THRESHOLDS), [requirement()])
    user = messages[1].content
    assert user.count("claim_id:") == 2
    assert "claim_id: step0-g0" in user and "claim_id: step0-g1" in user
    assert "field: expectations[0]" in user and "field: expectations[1]" in user


def test_two_identical_values_can_receive_different_verdicts():
    """Matching on the claim text would have given both the same verdict."""
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "findings": [
                        {"claim_id": "step0-g0", "claim": "12 V",
                         "verdict": "mismatched_quantity", "confidence": "high",
                         "explanation": "12 V is the supply rail, not a switch-on point"},
                        {"claim_id": "step0-g1", "claim": "12 V",
                         "verdict": "fits", "confidence": "low",
                         "explanation": "accepted for the switch-off point"},
                    ],
                }
            )
        ]
    )
    review = review_grounds(case_with(TWO_THRESHOLDS), [requirement()], model)

    assert review.complete
    outcomes = {v.claim_id: v.outcome for v in review.verdicts}
    assert outcomes == {"step0-g0": "finding", "step0-g1": "clear"}
    assert [i.path for i in review.issues] == ["TC-1.steps[0].expectations[0]"]
    assert "supply rail" in review.issues[0].message


def test_one_answer_for_two_identical_values_leaves_the_other_unjudged():
    """The text alone used to cover both. Now the second comes back open."""
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "findings": [
                        {"claim_id": "step0-g0", "claim": "12 V",
                         "verdict": "fits", "explanation": "ok"}
                    ],
                }
            )
        ]
    )
    review = review_grounds(case_with(TWO_THRESHOLDS), [requirement()], model)
    outcomes = {v.claim_id: v.outcome for v in review.verdicts}
    assert outcomes == {"step0-g0": "clear", "step0-g1": "not_checked"}
    assert review.complete is False
    assert "C_GROUND_REVIEW_NOT_CHECKED" in {i.code for i in review.issues}


def test_a_claim_id_the_request_did_not_give_is_refused():
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "findings": [
                        {"claim_id": "made-up", "claim": "12 V",
                         "verdict": "mismatched_state", "explanation": "x"}
                    ],
                }
            )
        ]
    )
    review = review_grounds(case_with(TWO_THRESHOLDS), [requirement()], model)
    assert "C_GROUND_REVIEW_UNMATCHED" in {i.code for i in review.issues}
    assert all(v.outcome == "not_checked" for v in review.verdicts)


def test_a_finding_without_a_claim_id_is_refused():
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "findings": [{"claim": "12 V", "verdict": "fits"}],
                }
            )
        ]
    )
    review = review_grounds(case_with(TWO_THRESHOLDS), [requirement()], model)
    assert not review.ok and "claim_id" in review.error
    assert all(v.outcome == "not_checked" for v in review.verdicts)


# ========== a number inside a quote is never adopted as a calculation input ==
def test_a_number_in_the_quote_is_not_used_as_a_calculation_input():
    """The real answer that was rejected: values only inside the quotes.

    The check must report the gap, never read the number out of the quote
    and pretend the arithmetic was given.
    """
    from spec2test.domain.grounding import check_calculation, check_grounds
    from spec2test.domain.models import GroundInput

    ground = Ground(
        claim="255 mV",
        kind=GroundKind.CALCULATION,
        target="measurements[0]",
        inputs=(
            GroundInput(source="SYS-REQ-0001", quote="the primary 100 uOhm shunt"),
            GroundInput(source="SYS-REQ-0001", quote="a pulse simulating 2550 A"),
        ),
        formula="V = I * R = 2550 A * 100 uOhm = 255 mV",
    )
    problem = check_calculation(ground)
    assert problem and "has no value of its own" in problem, problem

    step = TestStep(
        1, "inject the pulse", ("the amplitude is 255 mV",), grounds=(ground,)
    )
    issues = check_grounds(case_with(step), [requirement()])
    assert "C_GROUND_CALCULATION_NOT_REPRODUCIBLE" in {i.code for i in issues}


def test_an_incomplete_calculation_reliably_blocks_approval():
    from spec2test.domain.models import GroundInput
    from spec2test.domain.validation import may_approve

    ground = Ground(
        claim="255 mV",
        kind=GroundKind.CALCULATION,
        target="expectations[0]",
        inputs=(GroundInput(source="SYS-REQ-0001", quote="2550 A"),),
        formula="I * R",
    )
    case = case_with(
        TestStep(1, "inject", ("the amplitude is 255 mV",), grounds=(ground,))
    )
    allowed, report = may_approve(case, [requirement()])
    assert allowed is False
    assert report.blocks_draft is False, "but the draft is still storable"
    assert "C_GROUND_CALCULATION_NOT_REPRODUCIBLE" in report.codes()
