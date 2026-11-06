"""A small text format so a reviewer can edit steps as text.

    1. drive at 50 km/h
       - the speed reaches 50 km/h
       - no fault is stored
    2. trigger the event
       - the reaction takes 100 ms

An action starts a step, a line beginning with "-" adds an expectation to the
step above it. The numbering is cosmetic: steps are renumbered on parsing, so
a reviewer can insert a step without renumbering by hand.

Pure text handling, no Qt.
"""
from __future__ import annotations

import re
from typing import Sequence

from ..domain.models import TestStep

_ACTION = re.compile(r"^\s*(?:\d+[.)]\s*)?(?P<text>\S.*?)\s*$")
_EXPECTATION = re.compile(r"^\s*[-*•]\s*(?P<text>\S.*?)\s*$")

INDENT = "   "


class StepTextError(ValueError):
    """The text cannot be read as a list of steps."""


def steps_to_text(steps: Sequence[TestStep]) -> str:
    lines: list[str] = []
    for step in steps:
        lines.append(f"{step.order}. {step.action}")
        for expectation in step.expectations:
            lines.append(f"{INDENT}- {expectation}")
        for measurement in step.measurements:
            value = (
                f"{measurement.value} {measurement.unit or ''}".strip()
                if measurement.value is not None
                else "value open"
            )
            lines.append(f"{INDENT}- {measurement.quantity} = {value}")
    return "\n".join(lines)


def text_to_steps(text: str) -> tuple[TestStep, ...]:
    """Parse the text back into steps.

    Measurements are not reconstructed: an edited step keeps its expectations
    as text. Anything the reviewer typed is preserved verbatim.
    """
    actions: list[tuple[str, list[str]]] = []
    for number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip():
            continue
        expectation = _EXPECTATION.match(raw_line)
        if expectation:
            if not actions:
                raise StepTextError(
                    f"line {number}: an expectation needs an action above it"
                )
            actions[-1][1].append(expectation.group("text"))
            continue
        action = _ACTION.match(raw_line)
        if action:
            actions.append((action.group("text"), []))
    return tuple(
        TestStep(order=index, action=action, expectations=tuple(expectations))
        for index, (action, expectations) in enumerate(actions, 1)
    )
