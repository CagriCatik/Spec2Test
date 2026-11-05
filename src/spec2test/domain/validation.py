"""Structural and reference validation.

Formal validity is not technical correctness: a present requirement ID proves
nothing about whether the assignment makes sense. These rules only decide
whether a draft may be stored (``blocks_draft``) and whether a tester may
approve it (``blocks_approval``). Technical suitability stays with the tester.

No network, no GUI, no file access.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .models import (
    OpenQuestion,
    Requirement,
    Severity,
    TestCase,
    TestStep,
    ValidationIssue,
)

# Codes that block acceptance as a valid draft.
BLOCKING_CODES = frozenset(
    {
        "E_EMPTY_FIELD",
        "E_NO_REQUIREMENT_REFS",
        "E_UNKNOWN_REQUIREMENT_REF",
        "E_NO_STEPS",
        "E_STEP_WITHOUT_EXPECTATION",
        "E_EMPTY_ACTION",
        "E_STEP_ORDER",
        "E_INVALID_TYPE",
        "E_SCHEMA",
    }
)

_UNIT = r"(?:deg/s|km/h|m/s2|m/s|deg|°|ms|µs|us|s|%|mV|V|mA|A|Nm|kHz|Hz|bar|rpm|mm|cm|m|kPa|Ohm)"
_NUMBER_WITH_UNIT = re.compile(
    rf"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT})\b", re.IGNORECASE
)
_DTC = re.compile(r"\b[PBCU][0-9A-F]{4}\b")
_MEASURABLE_HINT = re.compile(
    rf"(\d+(?:[.,]\d+)?\s*{_UNIT}\b)|(\b[PBCU][0-9A-F]{{4}}\b)", re.IGNORECASE
)

_UNIT_ALIASES = {"°": "deg", "us": "µs"}


# ----------------------------------------------------------------- report ---
@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, code: str, severity: Severity, path: str, message: str) -> None:
        self.issues.append(ValidationIssue(code, severity, path, message))

    def extend(self, issues: Iterable[ValidationIssue]) -> None:
        self.issues.extend(issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def clarifications(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.CLARIFICATION]

    @property
    def blocks_draft(self) -> bool:
        """True when the answer must not be accepted as a valid draft."""
        return bool(self.errors)

    @property
    def blocks_approval(self) -> bool:
        """True when a tester must not set the case to ``approved`` yet."""
        return bool(self.errors) or bool(self.clarifications)

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}


# ------------------------------------------------------------- primitives ---
def _normalise_unit(unit: str) -> str:
    low = unit.lower()
    return _UNIT_ALIASES.get(low, low)


def _numeric_claims(text: str) -> set[tuple[float, str]]:
    """Numbers that carry a unit. Bare numbers are ignored on purpose:
    they are too noisy to attribute to a specification value."""
    out: set[tuple[float, str]] = set()
    for match in _NUMBER_WITH_UNIT.finditer(text):
        raw = match.group("value").replace(",", ".").lstrip("+")
        try:
            out.add((float(raw), _normalise_unit(match.group("unit"))))
        except ValueError:  # pragma: no cover - regex guarantees a number
            continue
    return out


def _dtc_claims(text: str) -> set[str]:
    return {m.group(0).upper() for m in _DTC.finditer(text)}


def requirement_lookup(
    requirements: Sequence[Requirement],
) -> dict[str, Requirement]:
    """Map internal IDs and external IDs to requirements.

    An external ID that occurs in more than one document is ambiguous and is
    therefore *not* resolvable by external ID; such a reference is reported as
    unknown so the caller must use the internal ID.
    """
    by_key: dict[str, Requirement] = {}
    external_counts: dict[str, int] = {}
    for req in requirements:
        by_key[req.requirement_id] = req
        if req.external_id:
            external_counts[req.external_id] = external_counts.get(req.external_id, 0) + 1
    for req in requirements:
        if req.external_id and external_counts[req.external_id] == 1:
            by_key.setdefault(req.external_id, req)
    return by_key


def _source_text(reqs: Sequence[Requirement]) -> str:
    parts: list[str] = []
    for req in reqs:
        parts.append(req.text)
        parts.extend(span.quote for span in req.spans)
    return "\n".join(parts)


# -------------------------------------------------------------- step rules ---
def _validate_step(
    step: TestStep,
    path: str,
    report: ValidationReport,
    *,
    expectation_severity: Severity,
    require_measurable: bool = True,
) -> None:
    if not step.action.strip():
        report.add("E_EMPTY_ACTION", Severity.ERROR, f"{path}.action", "action is empty")
    if not [e for e in step.expectations if e.strip()]:
        report.add(
            "E_STEP_WITHOUT_EXPECTATION"
            if expectation_severity is Severity.ERROR
            else "C_STEP_WITHOUT_EXPECTATION",
            expectation_severity,
            f"{path}.expectations",
            "step has no observable expectation; its execution must not be assumed",
        )
    for i, expectation in enumerate(step.expectations):
        if not expectation.strip():
            report.add(
                "E_EMPTY_FIELD",
                Severity.ERROR,
                f"{path}.expectations[{i}]",
                "expectation is empty",
            )
            continue
        has_measurement = bool(step.measurements)
        if (
            require_measurable
            and not has_measurement
            and not _MEASURABLE_HINT.search(expectation)
        ):
            report.add(
                "C_NO_MEASURABLE_CRITERION",
                Severity.CLARIFICATION,
                f"{path}.expectations[{i}]",
                "structural expectation without a measurable criterion",
            )
    for i, measurement in enumerate(step.measurements):
        if not measurement.evidence or not measurement.evidence.strip():
            report.add(
                "C_MEASUREMENT_WITHOUT_EVIDENCE",
                Severity.CLARIFICATION,
                f"{path}.measurements[{i}]",
                f"measurement {measurement.quantity!r} has no quote from the requirement",
            )


def _validate_step_sequence(
    steps: Sequence[TestStep], path: str, report: ValidationReport
) -> None:
    orders = [step.order for step in steps]
    if orders != list(range(1, len(steps) + 1)):
        report.add(
            "E_STEP_ORDER",
            Severity.ERROR,
            path,
            f"step order must be 1..{len(steps)}, got {orders}",
        )


# -------------------------------------------------------------- case rules ---
def validate_test_case(
    case: TestCase,
    requirements: Sequence[Requirement] | Mapping[str, Requirement],
) -> ValidationReport:
    """Validate one test case against the requirements that were offered."""
    report = ValidationReport()
    lookup = (
        dict(requirements)
        if isinstance(requirements, Mapping)
        else requirement_lookup(requirements)
    )
    path = f"test_case[{case.case_id}]"

    for field_name in ("title", "objective"):
        if not str(getattr(case, field_name)).strip():
            report.add(
                "E_EMPTY_FIELD", Severity.ERROR, f"{path}.{field_name}", "field is empty"
            )

    # --- requirement references -------------------------------------------
    if not case.requirement_ids:
        report.add(
            "E_NO_REQUIREMENT_REFS",
            Severity.ERROR,
            f"{path}.requirement_ids",
            "a test draft must reference at least one requirement",
        )
    seen: set[str] = set()
    resolved: list[Requirement] = []
    for i, ref in enumerate(case.requirement_ids):
        if ref in seen:
            report.add(
                "C_DUPLICATE_REQUIREMENT_REF",
                Severity.CLARIFICATION,
                f"{path}.requirement_ids[{i}]",
                f"requirement {ref!r} referenced more than once",
            )
            continue
        seen.add(ref)
        req = lookup.get(ref)
        if req is None:
            report.add(
                "E_UNKNOWN_REQUIREMENT_REF",
                Severity.ERROR,
                f"{path}.requirement_ids[{i}]",
                f"requirement {ref!r} is not part of this project state "
                "(or its external ID is ambiguous across documents)",
            )
        else:
            resolved.append(req)

    # --- steps -------------------------------------------------------------
    if not case.steps:
        report.add(
            "E_NO_STEPS",
            Severity.ERROR,
            f"{path}.steps",
            "a test draft needs at least one test step",
        )
    _validate_step_sequence(case.steps, f"{path}.steps", report)
    _validate_step_sequence(case.preconditions, f"{path}.preconditions", report)
    _validate_step_sequence(case.postconditions, f"{path}.postconditions", report)

    for i, step in enumerate(case.steps):
        _validate_step(
            step, f"{path}.steps[{i}]", report, expectation_severity=Severity.ERROR
        )
    for i, step in enumerate(case.preconditions):
        _validate_step(
            step,
            f"{path}.preconditions[{i}]",
            report,
            expectation_severity=Severity.CLARIFICATION,
            # State descriptions in pre-/postconditions are not measurements.
            require_measurable=False,
        )
    for i, step in enumerate(case.postconditions):
        _validate_step(
            step,
            f"{path}.postconditions[{i}]",
            report,
            expectation_severity=Severity.CLARIFICATION,
            require_measurable=False,
        )

    # --- unsupported detail ------------------------------------------------
    report.extend(_unsupported_details(case, resolved, path))

    if case.legacy_payload:
        report.add(
            "C_LEGACY_MAPPING_REQUIRED",
            Severity.CLARIFICATION,
            f"{path}.legacy_payload",
            "flat actions/expected_results could not be paired without guessing; "
            "manual assignment required",
        )
    return report


def _unsupported_details(
    case: TestCase, resolved: Sequence[Requirement], path: str
) -> list[ValidationIssue]:
    """Numbers, limits and DTCs the model added without cover in the source."""
    if not resolved:
        return []
    source = _source_text(resolved)
    allowed_numbers = _numeric_claims(source)
    allowed_dtcs = _dtc_claims(source)

    issues: list[ValidationIssue] = []
    texts: list[tuple[str, str]] = []
    for i, expectation in enumerate(case.all_expectations()):
        texts.append((f"{path}.expectations[{i}]", expectation))
    for i, measurement in enumerate(case.all_measurements()):
        value = f"{measurement.value} {measurement.unit or ''}".strip()
        texts.append((f"{path}.measurements[{i}]", value))
        if measurement.tolerance:
            texts.append((f"{path}.measurements[{i}].tolerance", measurement.tolerance))

    for where, text in texts:
        for value, unit in _numeric_claims(text):
            if not any(
                abs(value - allowed) < 1e-9 and unit == allowed_unit
                for allowed, allowed_unit in allowed_numbers
            ):
                issues.append(
                    ValidationIssue(
                        "C_UNSUPPORTED_VALUE",
                        Severity.CLARIFICATION,
                        where,
                        f"{value:g} {unit} is not covered by the referenced "
                        "requirements; record it as an open question or an "
                        "explicitly marked proposal instead of a specification",
                    )
                )
        for dtc in _dtc_claims(text):
            if dtc not in allowed_dtcs:
                issues.append(
                    ValidationIssue(
                        "C_UNSUPPORTED_DTC",
                        Severity.CLARIFICATION,
                        where,
                        f"DTC {dtc} does not appear in the referenced requirements",
                    )
                )
    return issues


# ----------------------------------------------------------------- gating ---
def may_approve(
    case: TestCase,
    requirements: Sequence[Requirement] | Mapping[str, Requirement],
    *,
    open_questions: Sequence[OpenQuestion] = (),
) -> tuple[bool, ValidationReport]:
    """Approval gate. Unresolved criteria block approval.

    A question that a reviewer has answered no longer blocks: it stays on
    record with its answer, but it is cleared.
    """
    report = validate_test_case(case, requirements)
    accepted = case.accepted_keys
    if accepted:
        # an accepted clarification no longer blocks; it stays in the record
        report.issues = [
            issue
            for issue in report.issues
            if issue.severity is Severity.ERROR
            or (issue.code, issue.path) not in accepted
        ]
    seen: set[str] = set()
    questions = []
    for question in (*open_questions, *case.open_questions):
        if question.resolved or question.question_id in seen:
            continue
        seen.add(question.question_id)
        questions.append(question)
    if questions:
        report.add(
            "C_OPEN_QUESTIONS",
            Severity.CLARIFICATION,
            f"test_case[{case.case_id}].open_questions",
            f"{len(questions)} unresolved question(s) must be clarified before approval",
        )
    return (not report.blocks_approval), report
