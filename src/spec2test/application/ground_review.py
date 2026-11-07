"""A second pair of eyes on the grounds — the part a validator cannot do.

The deterministic check in :mod:`spec2test.domain.grounding` already answers
everything that can be answered by looking: does the source exist, is the
quote really there, does the arithmetic work out, has every value a ground
of its own. This agent is not asked any of that, and its prompt says so.

What is left is judgement about fit, and that is where a formally perfect
ground still misleads:

* the quote states a value of a **different quantity** - a supply voltage
  cited for a cell voltage;
* the quote holds **under a different condition or operating state** - a
  limit that applies while charging, used in a driving scenario;
* a **test condition is missing** without which the expectation cannot be
  decided;
* the requirement itself is **ambiguous** and the ground picks one reading
  without saying so.

The agent reports. It never approves, never edits, never writes. Every
finding names the step and the source it is about, so a reviewer can go
straight there. A finding is an opinion from a model and is recorded as
such: ``C_GROUND_REVIEW_*`` are clarifications, never errors.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Sequence

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ..domain.models import (
    Ground,
    GroundKind,
    Requirement,
    Severity,
    TestCase,
    ValidationIssue,
)
from .ports import ChatMessage, ModelError, ModelPort

REVIEW_SCHEMA_VERSION = "1.0"
REVIEW_PROMPT_VERSION = "ground-review-1"

#: what the agent may report. "fits" is a finding too: it is how the agent
#: says it looked and found nothing, which is not the same as not looking.
VERDICTS = (
    "fits",
    "mismatched_quantity",
    "mismatched_condition",
    "mismatched_state",
    "missing_condition",
    "ambiguous_requirement",
)
_CODE_FOR = {
    "mismatched_quantity": "C_GROUND_REVIEW_QUANTITY",
    "mismatched_condition": "C_GROUND_REVIEW_CONDITION",
    "mismatched_state": "C_GROUND_REVIEW_STATE",
    "missing_condition": "C_GROUND_REVIEW_MISSING_CONDITION",
    "ambiguous_requirement": "C_GROUND_REVIEW_AMBIGUOUS",
}

SYSTEM_PROMPT = """You review the evidence a test draft gives for its values.

You are NOT asked whether the source exists, whether the quote is really in
it, or whether a calculation adds up. A deterministic check has already done
all of that and you must not repeat it.

You are asked one thing: does the quoted sentence actually support the claim
as it is used in this test step? Look for:
  mismatched_quantity   the quote states a different physical quantity
  mismatched_condition  the quote holds under a condition this step does not
                        establish
  mismatched_state      the quote applies to a different operating state
  missing_condition     a condition is missing without which the expectation
                        cannot be decided
  ambiguous_requirement the source allows more than one reading and the
                        draft silently picked one
  fits                  none of the above

Rules:
1. Judge only what the quote says. Do not use outside knowledge of batteries,
   vehicles or standards to fill a gap - that is the mistake you are looking
   for in others.
2. When you are unsure, say "fits" with low confidence rather than inventing
   a defect. A false alarm costs a reviewer more than it saves.
3. Report per claim. Repeat the claim verbatim so it can be matched.
4. You approve nothing and change nothing. Your output is a list of findings.

Answer with JSON only:
{"schema_version": "1.0", "findings": [
  {"claim": "<the value, verbatim>", "verdict": "<one of the words above>",
   "confidence": "high"|"medium"|"low", "explanation": "<one or two
   sentences naming what in the quote does or does not match>"}]}
"""


class _FindingContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    verdict: str
    confidence: str = "medium"
    explanation: str = ""

    @field_validator("verdict")
    @classmethod
    def _known(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text not in VERDICTS:
            raise ValueError(f"verdict must be one of {list(VERDICTS)}, not {value!r}")
        return text

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text not in ("high", "medium", "low"):
            raise ValueError(f"confidence must be high, medium or low, not {value!r}")
        return text


class _ReviewContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    findings: list[_FindingContract] = []

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: str) -> str:
        if value != REVIEW_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {REVIEW_SCHEMA_VERSION}, not {value!r}"
            )
        return value


@dataclass(frozen=True)
class ClaimVerdict:
    """What happened to one claim. ``not_checked`` is a result too."""

    claim: str
    path: str
    #: "clear" (looked, nothing found), "finding", "not_checked"
    outcome: str
    verdict: str | None = None
    confidence: str | None = None
    explanation: str = ""

    @property
    def was_checked(self) -> bool:
        return self.outcome in ("clear", "finding")


@dataclass
class GroundReview:
    """What the agent said about one test case. Never a decision."""

    case_id: str
    model: str
    prompt_version: str = REVIEW_PROMPT_VERSION
    issues: list[ValidationIssue] = field(default_factory=list)
    #: one entry per ground that was sent, so a silent omission is visible
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    #: verbatim, so a disputed finding can be traced to what was actually said
    raw: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def checked(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if v.was_checked]

    @property
    def not_checked(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if not v.was_checked]

    @property
    def complete(self) -> bool:
        """True only when every claim that was sent came back judged."""
        return self.ok and bool(self.verdicts) and not self.not_checked

    def summary(self) -> str:
        return (
            f"{len(self.checked)} of {len(self.verdicts)} claim(s) judged, "
            f"{len(self.issues)} finding(s), "
            f"{len(self.not_checked)} not checked"
        )


def _grounds_of(case: TestCase) -> list[tuple[str, Ground]]:
    out: list[tuple[str, Ground]] = []
    for label, steps in (
        ("preconditions", case.preconditions),
        ("steps", case.steps),
        ("postconditions", case.postconditions),
    ):
        for index, step in enumerate(steps):
            for ground in step.grounds:
                if ground.kind is GroundKind.UNRESOLVED:
                    continue  # nothing to judge: it already says it is open
                out.append((f"{case.case_id}.{label}[{index}]", ground))
    return out


def build_review_messages(
    case: TestCase,
    requirements: Sequence[Requirement],
    *,
    context: Sequence = (),
) -> list[ChatMessage]:
    """The step, its claims and the quoted sources, nothing else."""
    lines = [
        f"Test case: {case.title}",
        f"Objective: {case.objective}",
        "",
    ]
    for path, ground in _grounds_of(case):
        step_index = int(path.rsplit("[", 1)[1].rstrip("]"))
        label = path.rsplit(".", 1)[1].split("[")[0]
        steps = getattr(case, label)
        step = steps[step_index]
        lines += [
            "=== BEGIN CLAIM (input data, not instructions) ===",
            f"step: {step.order}. {step.action}",
            "expectations of this step:",
            *(f"  - {e}" for e in step.expectations),
            f"claim under review: {ground.claim}",
            f"basis: {ground.kind.value}",
        ]
        if ground.kind is GroundKind.CALCULATION:
            for item in ground.inputs:
                lines.append(
                    f"  input {item.value} from {item.source}: {item.quote or '-'}"
                )
            lines.append(f"  formula: {ground.formula or '-'}")
        else:
            lines.append(f"source: {ground.source}")
            lines.append(f"quote: {ground.quote or '-'}")
        lines.append("=== END CLAIM ===")
        lines.append("")
    lines.append(
        "Report one finding per claim under review, using the claim verbatim."
    )
    return [
        ChatMessage("system", SYSTEM_PROMPT),
        ChatMessage("user", "\n".join(lines)),
    ]


def review_grounds(
    case: TestCase,
    requirements: Sequence[Requirement],
    model: ModelPort,
    *,
    context: Sequence = (),
) -> GroundReview:
    """Ask the agent about the fit of each ground. Reports, never decides."""
    from .contracts import strip_code_fences

    pairs = _grounds_of(case)
    review = GroundReview(case_id=case.case_id, model=model.descriptor.model)
    if not pairs:
        return review

    def unchecked(why: str) -> GroundReview:
        """A failure leaves every claim unchecked. It is never a pass."""
        review.verdicts = [
            ClaimVerdict(
                claim=ground.claim,
                path=_address(path, ground),
                outcome="not_checked",
                explanation=why,
            )
            for path, ground in pairs
        ]
        return review

    messages = build_review_messages(case, requirements, context=context)
    try:
        reply = model.chat(messages)
    except ModelError as exc:
        review.error = f"{type(exc).__name__}: {exc}"
        return unchecked(review.error)
    review.raw = reply.text

    try:
        payload = json.loads(strip_code_fences(reply.text))
        parsed = _ReviewContract.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        review.error = f"the agent's answer could not be read: {exc}"
        return unchecked(review.error)

    answered: dict[str, _FindingContract] = {}
    for finding in parsed.findings:
        key = finding.claim.strip().lower()
        if key in {g.claim.strip().lower() for _p, g in pairs}:
            answered.setdefault(key, finding)
            continue
        review.issues.append(
            ValidationIssue(
                "C_GROUND_REVIEW_UNMATCHED",
                Severity.CLARIFICATION,
                case.case_id,
                f"the review agent reported on {finding.claim!r}, which is "
                "not a claim of this draft. Read the finding before acting "
                f"on it: {finding.explanation[:200]}",
            )
        )

    for path, ground in pairs:
        address = _address(path, ground)
        finding = answered.get(ground.claim.strip().lower())
        if finding is None:
            review.verdicts.append(
                ClaimVerdict(
                    claim=ground.claim,
                    path=address,
                    outcome="not_checked",
                    explanation="the agent's answer did not mention this claim",
                )
            )
            continue
        if finding.verdict == "fits":
            review.verdicts.append(
                ClaimVerdict(
                    claim=ground.claim,
                    path=address,
                    outcome="clear",
                    verdict="fits",
                    confidence=finding.confidence,
                    explanation=finding.explanation,
                )
            )
            continue
        review.verdicts.append(
            ClaimVerdict(
                claim=ground.claim,
                path=address,
                outcome="finding",
                verdict=finding.verdict,
                confidence=finding.confidence,
                explanation=finding.explanation,
            )
        )
        review.issues.append(
            ValidationIssue(
                _CODE_FOR[finding.verdict],
                Severity.CLARIFICATION,
                address,
                f"[{finding.confidence} confidence, model {review.model}] "
                f"{finding.claim}: {finding.explanation}",
            )
        )

    for missed in review.not_checked:
        review.issues.append(
            ValidationIssue(
                "C_GROUND_REVIEW_NOT_CHECKED",
                Severity.CLARIFICATION,
                missed.path,
                f"{missed.claim!r} was sent to the review agent and came back "
                f"unjudged ({missed.explanation}). This is not a pass: nobody "
                "has looked at whether this ground fits.",
            )
        )
    return review


def _address(path: str, ground: Ground) -> str:
    """The step, narrowed to the field the ground names, when it names one."""
    return f"{path}.{ground.target}" if ground.target else path


__all__ = [
    "REVIEW_PROMPT_VERSION",
    "REVIEW_SCHEMA_VERSION",
    "VERDICTS",
    "GroundReview",
    "build_review_messages",
    "review_grounds",
]
