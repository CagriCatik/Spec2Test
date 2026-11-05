"""The text format a reviewer edits steps in."""
from __future__ import annotations

import pytest

from spec2test.application.step_text import (
    StepTextError,
    steps_to_text,
    text_to_steps,
)
from spec2test.domain.models import Measurement, TestStep


def test_steps_render_as_readable_text():
    steps = (
        TestStep(1, "drive at 50 km/h", ("the speed reaches 50 km/h", "no fault")),
        TestStep(2, "trigger the event", ("the reaction takes 100 ms",)),
    )
    assert steps_to_text(steps) == (
        "1. drive at 50 km/h\n"
        "   - the speed reaches 50 km/h\n"
        "   - no fault\n"
        "2. trigger the event\n"
        "   - the reaction takes 100 ms"
    )


def test_a_round_trip_keeps_actions_and_expectations():
    steps = (
        TestStep(1, "drive at 50 km/h", ("the speed reaches 50 km/h",)),
        TestStep(2, "trigger", ("100 ms", "no DTC")),
    )
    parsed = text_to_steps(steps_to_text(steps))
    assert [s.action for s in parsed] == [s.action for s in steps]
    assert [s.expectations for s in parsed] == [s.expectations for s in steps]


def test_steps_are_renumbered_so_inserting_is_easy():
    parsed = text_to_steps(
        "7. first\n   - a\n2. second\n   - b\n"
    )
    assert [s.order for s in parsed] == [1, 2]
    assert [s.action for s in parsed] == ["first", "second"]


def test_numbering_is_optional():
    parsed = text_to_steps("do this\n- it works\ndo that\n- it also works")
    assert [s.action for s in parsed] == ["do this", "do that"]
    assert parsed[0].expectations == ("it works",)


@pytest.mark.parametrize("bullet", ["-", "*", "•"])
def test_common_bullets_are_understood(bullet):
    parsed = text_to_steps(f"an action\n  {bullet} an expectation")
    assert parsed[0].expectations == ("an expectation",)


def test_blank_lines_are_ignored():
    parsed = text_to_steps("\n\n1. action\n\n   - expectation\n\n")
    assert len(parsed) == 1 and parsed[0].expectations == ("expectation",)


def test_an_expectation_without_an_action_is_refused():
    with pytest.raises(StepTextError) as excinfo:
        text_to_steps("- an orphan expectation")
    assert "line 1" in str(excinfo.value)


def test_empty_text_gives_no_steps():
    assert text_to_steps("   \n\n") == ()


def test_measurements_are_shown_but_not_reconstructed():
    steps = (
        TestStep(
            1,
            "measure",
            ("deviation is 0.2 deg",),
            (Measurement("deviation", "0.2", "deg", evidence="at most 0.2 deg"),),
        ),
    )
    text = steps_to_text(steps)
    assert "deviation = 0.2 deg" in text
    parsed = text_to_steps(text)
    assert parsed[0].measurements == ()
    assert len(parsed[0].expectations) == 2, "the measurement line stays as text"
