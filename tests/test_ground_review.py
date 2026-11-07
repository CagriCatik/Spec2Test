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
    return json.dumps(
        {"schema_version": REVIEW_SCHEMA_VERSION, "findings": list(findings)}
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
    assert review.checked_claims == 1, "it looked, and said so"


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
        [answer({"claim": "999 V", "verdict": "mismatched_state",
                 "explanation": "invented"})]
    )
    review = review_grounds(case_with(CORRECT), [requirement()], model)
    assert [i.code for i in review.issues] == ["C_GROUND_REVIEW_UNMATCHED"]


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
