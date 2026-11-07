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

_UNIT = (
    r"(?:deg/s|km/h|m/s2|m/s|degC|°C|K|deg|°|ms|µs|us|s|min|h|%|ppm|"
    r"kV|mV|µV|V|mA|µA|A|mAh|Ah|kWh|Wh|kW|W|Nm|MHz|kHz|Hz|bar|kPa|Pa|rpm|"
    r"mOhm|µOhm|kOhm|Ohm|mm|cm|m|g|kg|bit/s|kbit/s|Mbit/s)"
)

# Specifications written in Markdown often carry LaTeX: "$50\\text{ ms}$".
# Without normalising it, a correctly quoted value would be reported as
# unsupported, which floods a reviewer with false findings.
_MATH_REPLACEMENTS = (
    (re.compile(r"\\(?:text|mathrm|mathit|si|unit)\s*\{([^{}]*)\}"), r" \1 "),
    (re.compile(r"\\(?:,|;|:|!|quad|qquad)"), " "),
    (re.compile(r"\\ "), " "),
    (re.compile(r"\\pm"), " ± "),
    (re.compile(r"\\times"), " x "),
    (re.compile(r"\\(?:le|leq)\b"), " <= "),
    (re.compile(r"\\(?:ge|geq)\b"), " >= "),
    (re.compile(r"\\(?:mu|upmu)\b"), "µ"),
    (re.compile(r"\\Omega\b"), "Ohm"),
    (re.compile(r"\^\s*\\circ\s*"), "°"),
    (re.compile(r"\\circ\b"), "°"),
    (re.compile(r"[\$\\{}]"), " "),
)


def normalise_math(text: str) -> str:
    """Turn LaTeX-style notation into plain text so values can be read.

    ``$50\\text{ ms} \\pm 2\\text{ ms}$`` becomes ``50 ms ± 2 ms``.
    """
    for pattern, replacement in _MATH_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return re.sub(r"[ \t]{2,}", " ", text)
_NUMBER_WITH_UNIT = re.compile(
    rf"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT})\b", re.IGNORECASE
)
_DTC = re.compile(r"\b[PBCU][0-9A-F]{4}\b")
_MEASURABLE_HINT = re.compile(
    rf"(\d+(?:[.,]\d+)?\s*{_UNIT}\b)|(\b[PBCU][0-9A-F]{{4}}\b)", re.IGNORECASE
)

_UNIT_ALIASES = {
    "°": "deg",
    "us": "µs",
    "°c": "degc",
    "µohm": "µohm",
    "uohm": "µohm",
}


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
    for match in _NUMBER_WITH_UNIT.finditer(normalise_math(text)):
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
        if measurement.value is None or not str(measurement.value).strip():
            report.add(
                "C_MEASUREMENT_WITHOUT_VALUE",
                Severity.CLARIFICATION,
                f"{path}.measurements[{i}]",
                f"{measurement.quantity!r} is named as a quantity to measure, "
                "but no target value is given. Clarify the value before the "
                "case is used.",
            )
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
    """The stored steps must form a clear sequence.

    A draft is renumbered to 1..n when it is assembled, so a model that counts
    continuously across the three lists is not punished for it. What stays an
    error is an ambiguous sequence: a repeated or unordered position.
    """
    orders = [step.order for step in steps]
    if orders != sorted(orders) or len(set(orders)) != len(orders):
        report.add(
            "E_STEP_ORDER",
            Severity.ERROR,
            path,
            f"step order must be unique and increasing, got {orders}",
        )


# -------------------------------------------------------------- case rules ---

def _all_steps(case: TestCase):
    return (*case.preconditions, *case.steps, *case.postconditions)


def _wants_grounds(case: TestCase) -> bool:
    """True when the draft makes a concrete claim that could be sourced."""
    for step in _all_steps(case):
        if any(m.value is not None for m in step.measurements):
            return True
        if any(_numeric_claims(e) or _dtc_claims(e) for e in step.expectations):
            return True
    return False


def _has_any_ground(case: TestCase) -> bool:
    return any(step.grounds for step in _all_steps(case))


def validate_test_case(
    case: TestCase,
    requirements: Sequence[Requirement] | Mapping[str, Requirement],
    *,
    context: Sequence = (),
) -> ValidationReport:
    """Validate one test case against the requirements that were offered.

    ``context`` are the supporting requirements that were sent along. A value
    found only there is not treated as covered: it is reported with the source
    that states it, so the reviewer decides whether the attribution holds.
    """
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
    report.extend(_unsupported_details(case, resolved, path, context))

    # --- where each concrete expectation gets its authority from ----------
    from .grounding import check_grounds

    report.extend(check_grounds(case, resolved or list(lookup.values()), context=context))
    # Informational, not blocking: every draft written before grounds were
    # required would otherwise become unapprovable overnight, which helps
    # nobody. A ground that is *wrong* - unknown source, quote that is not
    # there, arithmetic that does not work out - is an error and does block.
    if _wants_grounds(case) and not _has_any_ground(case):
        report.add(
            "I_NO_GROUNDS_STATED",
            Severity.INFO,
            f"{path}.steps",
            "this draft states concrete values but says for none of them "
            "where they come from. A reviewer cannot tell a quoted limit "
            "from an invented one.",
        )

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
    case: TestCase,
    resolved: Sequence[Requirement],
    path: str,
    context: Sequence = (),
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
        if measurement.value is None:
            continue
        value = f"{measurement.value} {measurement.unit or ''}".strip()
        texts.append((f"{path}.measurements[{i}]", value))
        if measurement.tolerance:
            texts.append((f"{path}.measurements[{i}].tolerance", measurement.tolerance))

    for where, text in texts:
        for value, unit in _numeric_claims(text):
            if any(
                abs(value - allowed) < 1e-9 and unit == allowed_unit
                for allowed, allowed_unit in allowed_numbers
            ):
                continue
            source = _context_source(value, unit, context)
            if source is not None:
                issues.append(
                    ValidationIssue(
                        "C_VALUE_FROM_CONTEXT",
                        Severity.CLARIFICATION,
                        where,
                        f"{value:g} {unit} does not appear in the referenced "
                        f"requirement(s) but in {source}. Confirm that this "
                        "value belongs to this expectation.",
                    )
                )
                continue
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
def _context_source(value: float, unit: str, context: Sequence) -> str | None:
    """Which supporting requirement states this quantity, if exactly one does."""
    matches = [
        item
        for item in context
        if any(
            abs(value - other) < 1e-9 and unit == other_unit
            for other, other_unit in _numeric_claims(getattr(item, "text", ""))
        )
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    label = getattr(item, "label", None)
    return label or getattr(item, "external_id", None) or "the supporting context"


def may_approve(
    case: TestCase,
    requirements: Sequence[Requirement] | Mapping[str, Requirement],
    *,
    open_questions: Sequence[OpenQuestion] = (),
    context: Sequence = (),
) -> tuple[bool, ValidationReport]:
    """Approval gate. Unresolved criteria block approval.

    A question that a reviewer has answered no longer blocks: it stays on
    record with its answer, but it is cleared.
    """
    report = validate_test_case(case, requirements, context=context)
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
