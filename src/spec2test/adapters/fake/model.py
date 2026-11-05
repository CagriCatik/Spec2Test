"""Fake model adapters for tests, CI and the offline example run.

They implement :class:`ModelPort` without any network access, so a use case can
be exercised unchanged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ...application.contracts import SCHEMA_VERSION
from ...application.deadline import Deadline
from ...application.ports import (
    ChatMessage,
    ModelDescriptor,
    ModelError,
    ModelReply,
)

_UNIT = r"(?:deg/s|km/h|m/s|deg|°|ms|s|%|V|A|Nm|Hz|bar|rpm|mm)"
_VALUE = re.compile(rf"([-+]?\d+(?:[.,]\d+)?)\s*({_UNIT})\b", re.IGNORECASE)
_REF = re.compile(r"^requirement_ref:\s*(\S+)$", re.MULTILINE)
_SIGNAL = re.compile(r"\b([A-Z][A-Za-z0-9]*_[A-Za-z0-9_]+)\b")
_DTC = re.compile(r"\b([PBCU][0-9A-F]{4})\b")


@dataclass
class ScriptedModel:
    """Replays prepared answers in order. Raises the given errors as they come."""

    replies: Sequence[str | ModelReply | ModelError]
    model: str = "fake-scripted"
    endpoint: str = "fake://scripted"
    mode: str = "fake"
    calls: list[list[ChatMessage]] = field(default_factory=list)
    _index: int = 0

    @property
    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            model=self.model, endpoint=self.endpoint, mode=self.mode, parameters={}
        )

    def chat(
        self, messages: Sequence[ChatMessage], *, deadline: Deadline | None = None
    ) -> ModelReply:
        self.calls.append(list(messages))
        if self._index >= len(self.replies):
            raise AssertionError(
                f"ScriptedModel received call {self._index + 1} but only "
                f"{len(self.replies)} reply/replies were prepared"
            )
        reply = self.replies[self._index]
        self._index += 1
        if isinstance(reply, ModelError):
            raise reply
        if isinstance(reply, ModelReply):
            return reply
        return ModelReply(text=reply, duration_s=0.0, model=self.model)

    @property
    def call_count(self) -> int:
        return self._index


@dataclass
class RuleBasedFakeModel:
    """Produces a contract-valid answer from the requirement text itself.

    It only ever uses values that appear in the requirement and raises an open
    question when a requirement has no measurable value. This is a stand-in for
    a model, not a test oracle: no test asserts on its wording.
    """

    model: str = "fake-rule-based"
    endpoint: str = "fake://rule-based"
    mode: str = "fake"
    max_cases: int = 1
    transform: Callable[[dict], dict] | None = None
    calls: list[list[ChatMessage]] = field(default_factory=list)

    @property
    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            model=self.model, endpoint=self.endpoint, mode=self.mode, parameters={}
        )

    def chat(
        self, messages: Sequence[ChatMessage], *, deadline: Deadline | None = None
    ) -> ModelReply:
        self.calls.append(list(messages))
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        payload = self._build(user)
        if self.transform:
            payload = self.transform(payload)
        return ModelReply(
            text=json.dumps(payload, ensure_ascii=False), duration_s=0.0, model=self.model
        )

    # ------------------------------------------------------------------
    def _build(self, user_message: str) -> dict:
        refs = _REF.findall(user_message)
        body = user_message
        values = [
            (v.replace(",", "."), unit) for v, unit in _VALUE.findall(body)
        ]
        dtcs = _DTC.findall(body)
        signals = [s for s in _SIGNAL.findall(body) if not s.startswith("RAS_SW")]

        expectations: list[str] = []
        measurements: list[dict] = []
        for value, unit in values[:3]:
            expectations.append(f"The observed value matches {value} {unit}")
            measurements.append(
                {
                    "quantity": f"value at {unit}",
                    "value": value,
                    "unit": unit,
                    "evidence": f"{value} {unit}",
                }
            )
        for dtc in dtcs[:1]:
            expectations.append(f"Diagnostic trouble code {dtc} is stored")
        for signal in signals[:1]:
            expectations.append(f"Signal {signal} reports the state")

        open_questions: list[dict] = []
        if not values:
            open_questions.append(
                {
                    "question": "The requirement states no measurable limit; which "
                    "value and tolerance apply?",
                    "requirement_refs": refs,
                }
            )
            expectations.append("The system reaches the state described in the requirement")

        case = {
            "title": f"Verify {refs[0] if refs else 'requirement'}",
            "objective": "Check the behaviour described in the referenced requirement.",
            "requirement_refs": refs,
            "preconditions": [
                {
                    "order": 1,
                    "action": "Bring the system into the operating state named in the requirement",
                    "expectations": ["The system reports a valid state"],
                }
            ],
            "steps": [
                {
                    "order": 1,
                    "action": "Apply the stimulus described in the requirement",
                    "expectations": expectations,
                    "measurements": measurements,
                }
            ],
            "postconditions": [
                {
                    "order": 1,
                    "action": "Return the system to its initial state",
                    "expectations": ["The system is back in a safe state"],
                }
            ],
            "open_questions": open_questions,
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "test_cases": [case][: self.max_cases],
            "open_questions": [],
        }
