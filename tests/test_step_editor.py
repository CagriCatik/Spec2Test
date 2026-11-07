"""The structured editor, and what happens to a ground when a field moves.

A ground belongs to a field address. Editing a case rearranges fields, and
the question each test below answers is: does the evidence follow its
field, stop covering a changed one, or silently cover the wrong one?
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from spec2test.domain.grounding import check_grounds  # noqa: E402
from spec2test.domain.models import (  # noqa: E402
    GROUNDING_RULES_VERSION,
    Ground,
    GroundKind,
    Measurement,
    Requirement,
    Severity,
    SourceSpan,
    TestCase,
    TestStep,
)
from spec2test.ui.step_editor import StepEditor  # noqa: E402

REQ_TEXT = (
    "SYS-REQ-1: The latch asserts within 2.0 us. The release takes 5.0 ms. "
    "The measured response time is 2.0 us."
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def requirement():
    return Requirement(
        requirement_id="REQ-1", text=REQ_TEXT, external_id="SYS-REQ-1",
        spans=(SourceSpan("spec.md", 0, len(REQ_TEXT), REQ_TEXT),),
    )


def ground(claim, target, quote):
    return Ground(
        claim=claim, kind=GroundKind.TARGET_REQUIREMENT, target=target,
        source="SYS-REQ-1", quote=quote,
    )


def two_expectations() -> TestStep:
    return TestStep(
        1,
        "inject the pulse",
        ("the latch asserts within 2.0 us", "the release takes 5.0 ms"),
        grounds=(
            ground("2.0 us", "expectations[0]", "The latch asserts within 2.0 us."),
            ground("5.0 ms", "expectations[1]", "The release takes 5.0 ms."),
        ),
    )


def editor_with(step, app) -> StepEditor:
    editor = StepEditor()
    editor.set_steps((step,))
    return editor


def grounds_of(step):
    return {g.target: g for g in step.grounds}


# ========================================================= nothing changed ==
def test_an_untouched_step_comes_back_identical(app):
    step = two_expectations()
    assert editor_with(step, app).steps() == (step,)


def test_a_measurement_keeps_every_field(app):
    step = TestStep(
        1, "measure", (),
        (Measurement("response time", "2.0", "us", tolerance="0.1",
                     evidence="within 2.0 us"),),
    )
    back = editor_with(step, app).steps()[0]
    measurement = back.measurements[0]
    assert measurement.value == "2.0" and measurement.unit == "us"
    assert measurement.tolerance == "0.1"
    assert measurement.evidence == "within 2.0 us"


# ============================================================ only moved ====
def test_a_ground_follows_its_field_when_rows_are_reordered(app):
    """Same sentence, new position: the quote still holds."""
    editor = editor_with(two_expectations(), app)
    node = editor.tree.topLevelItem(0)
    first = node.takeChild(0)
    node.insertChild(1, first)   # swap the two expectations

    back = editor.steps()[0]
    assert back.expectations == (
        "the release takes 5.0 ms",
        "the latch asserts within 2.0 us",
    )
    by_target = grounds_of(back)
    assert by_target["expectations[0]"].claim == "5.0 ms"
    assert by_target["expectations[1]"].claim == "2.0 us"
    assert not any(g.is_stale for g in back.grounds), "a move is not a change"


def test_deleting_an_earlier_row_renumbers_the_later_ground(app):
    editor = editor_with(two_expectations(), app)
    node = editor.tree.topLevelItem(0)
    node.removeChild(node.child(0))

    back = editor.steps()[0]
    assert back.expectations == ("the release takes 5.0 ms",)
    by_target = grounds_of(back)
    assert by_target["expectations[0]"].claim == "5.0 ms"
    assert not by_target["expectations[0]"].is_stale
    gone = [g for g in back.grounds if g.is_stale]
    assert len(gone) == 1 and "was removed" in gone[0].stale_reason


def test_inserting_a_row_does_not_shift_a_ground_onto_it(app):
    """The hole this guards: a new first row inheriting the old ground."""
    editor = editor_with(two_expectations(), app)
    node = editor.tree.topLevelItem(0)
    editor.tree.setCurrentItem(node)
    editor.add_expectation()
    node.child(node.childCount() - 1).setText(1, "the fuse stays intact")
    fresh = node.takeChild(node.childCount() - 1)
    node.insertChild(0, fresh)   # the new row goes first

    back = editor.steps()[0]
    assert back.expectations[0] == "the fuse stays intact"
    by_target = grounds_of(back)
    assert by_target["expectations[1]"].claim == "2.0 us"
    assert by_target["expectations[2]"].claim == "5.0 ms"
    assert "expectations[0]" not in by_target, (
        "the inserted row must not inherit a ground"
    )


# ============================================================== changed =====
def test_editing_an_expectation_makes_its_ground_stale(app):
    """An old quote must not go on covering a sentence that now differs."""
    editor = editor_with(two_expectations(), app)
    node = editor.tree.topLevelItem(0)
    node.child(0).setText(1, "the latch asserts within 1.0 us")

    back = editor.steps()[0]
    stale = grounds_of(back)["expectations[0]"]
    assert stale.is_stale
    assert "was edited" in stale.stale_reason
    assert stale.quote, "the quote is kept so a reviewer can judge it"
    assert not grounds_of(back)["expectations[1]"].is_stale


def test_editing_a_measurement_makes_its_ground_stale(app):
    step = TestStep(
        1, "measure", (),
        (Measurement("response time", "2.0", "us", evidence="within 2.0 us"),),
        grounds=(ground("2.0 us", "measurements[0]", "measured response time is 2.0 us"),),
    )
    editor = editor_with(step, app)
    node = editor.tree.topLevelItem(0)
    node.child(0).setText(2, "ms")   # the unit changes

    back = editor.steps()[0]
    assert grounds_of(back)["measurements[0]"].is_stale


def test_a_stale_ground_stops_covering_its_claim(app):
    """The claim must reappear as unsupported, not stay quietly covered."""
    editor = editor_with(two_expectations(), app)
    node = editor.tree.topLevelItem(0)
    node.child(0).setText(1, "the latch asserts within 1.0 us")
    edited = editor.steps()

    case = TestCase(
        case_id="TC-1", title="t", objective="o",
        requirement_ids=("SYS-REQ-1",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=edited,
    )
    issues = check_grounds(case, [requirement()])
    codes = {i.code for i in issues}
    assert "C_GROUND_STALE" in codes
    assert "C_CLAIM_WITHOUT_GROUND" in codes, (
        "the changed value is unsupported again until someone looks"
    )
    assert all(i.severity is Severity.CLARIFICATION for i in issues)
    # the untouched expectation is not dragged in
    stale_paths = {i.path for i in issues if i.code == "C_CLAIM_WITHOUT_GROUND"}
    assert all("expectations[1]" not in p for p in stale_paths), stale_paths


def test_a_stale_ground_blocks_approval_but_not_the_draft(app):
    from spec2test.domain.validation import may_approve

    editor = editor_with(two_expectations(), app)
    editor.tree.topLevelItem(0).child(0).setText(1, "changed entirely")
    case = TestCase(
        case_id="TC-2", title="t", objective="o",
        requirement_ids=("SYS-REQ-1",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=editor.steps(),
    )
    allowed, report = may_approve(case, [requirement()])
    assert allowed is False
    assert report.blocks_draft is False


def test_a_ground_without_a_target_is_left_alone(app):
    loose = Ground(
        claim="2.0 us", kind=GroundKind.TARGET_REQUIREMENT, source="SYS-REQ-1",
        quote="The latch asserts within 2.0 us.",
    )
    step = TestStep(1, "a", ("the latch asserts within 2.0 us",), grounds=(loose,))
    editor = editor_with(step, app)
    editor.tree.topLevelItem(0).child(0).setText(1, "something else")
    back = editor.steps()[0]
    assert back.grounds[0] is loose or back.grounds[0] == loose
