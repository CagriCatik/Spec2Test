"""Check the grounds a draft gives for its concrete expectations.

What is checked here is narrow on purpose:

* a named source exists among the requirements under test or the context
  that was sent, and the quote really appears in it;
* a calculation's inputs each have their own source, and the arithmetic
  works out;
* **every** concrete value has a ground of its own. One correct ground
  somewhere in the case is not enough: it would let a quoted limit cover
  three invented ones standing next to it;
* an unresolved value is an open question rather than a binding
  expectation.

What is **not** checked, and cannot be: whether the value is the right one
for this test. ``HW_Comparator_Reference_Voltage`` may be quoted correctly
and still be the wrong threshold for the scenario. That stays with the
tester; this only makes the claim followable.

The model proposes the grounds. A missing or wrong one is a finding, never
a silent repair: inventing a source would defeat the purpose.

**Every finding here is a clarification, not an error.** Saving a draft
stays possible; what a wrong ground blocks is the approval. Rejecting a
whole draft and spending a repair call because one quote is a few words
off would cost more than it is worth, and the draft is still the best
starting point a reviewer has.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import (
    GROUNDING_RULES_VERSION,
    Ground,
    GroundKind,
    Requirement,
    Severity,
    TestCase,
    TestStep,
)
from .validation import ValidationIssue, normalise_math

#: ``0x9A0201``. The corpus writes diagnostic codes this way; the existing
#: unsupported-value rule only knows the ``P0A80`` form. Widening that rule
#: would change what every draft is judged on, so the hex form is recognised
#: here only, where it means "this is a concrete value and needs a source".
_HEX_CODE = re.compile(r"\b0x[0-9A-Fa-f]{2,8}\b")
#: ``2550 A``, ``100 uOhm``, ``-0.5 V``. Shared with the arithmetic check.
_VALUE = re.compile(
    r"(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*(?P<unit>[^\s\d*/=+-]{0,12})"
)
#: scale factors this module can follow. Anything else is reported as
#: "not reproducible", not guessed.
_SCALE = {
    "": 1.0, "a": 1.0, "v": 1.0, "s": 1.0, "ohm": 1.0, "hz": 1.0,
    "ma": 1e-3, "mv": 1e-3, "ms": 1e-3, "mohm": 1e-3, "khz": 1e3,
    "ua": 1e-6, "uv": 1e-6, "us": 1e-6, "uohm": 1e-6, "mhz": 1e6,
    "na": 1e-9, "ns": 1e-9, "ka": 1e3, "kv": 1e3, "kohm": 1e3,
}
_UNIT_ALIASES = {
    "µ": "u", "μ": "u", "ω": "ohm", "Ω": "ohm",
}


def _canonical_unit(unit: str) -> str:
    text = unit.strip().lower()
    for source, target in _UNIT_ALIASES.items():
        text = text.replace(source, target)
    return text


def _as_si(value: str) -> tuple[float, str] | None:
    """``255 mV`` -> ``(0.255, "v")``. ``None`` when it is not a number."""
    match = _VALUE.fullmatch(normalise_math(value).strip())
    if match is None:
        return None
    try:
        number = float(match.group("value").replace(",", "."))
    except ValueError:  # pragma: no cover - the regex guarantees a number
        return None
    unit = _canonical_unit(match.group("unit"))
    scale = _SCALE.get(unit)
    if scale is None:
        return None
    base = unit.lstrip("munk") or unit
    for prefix in ("ma", "mv", "ms", "mohm", "ua", "uv", "us", "uohm",
                   "na", "ns", "ka", "kv", "kohm", "khz", "mhz"):
        if unit == prefix:
            base = prefix[1:]
            break
    return number * scale, base


def _quote_is_in(quote: str, text: str) -> bool:
    """Loose containment: whitespace and Markdown must not decide this."""
    def flatten(value: str) -> str:
        return re.sub(r"[\s`*_|]+", " ", normalise_math(value)).strip().lower()

    needle = flatten(quote)
    return bool(needle) and needle in flatten(text)


@dataclass(frozen=True)
class _Source:
    identifier: str
    text: str


def _sources(
    requirements: Sequence[Requirement], context: Sequence
) -> dict[str, _Source]:
    found: dict[str, _Source] = {}
    for requirement in requirements:
        for key in (requirement.requirement_id, requirement.external_id):
            if key:
                found[key] = _Source(key, requirement.text)
    for item in context:
        text = getattr(item, "full_text", None) or item.text
        if getattr(item, "prefix", None):
            text = f"{item.prefix}\n{item.text}"
        for key in (item.requirement_id, item.external_id):
            if key:
                found[key] = _Source(key, text)
    return found


def check_calculation(ground: Ground) -> str | None:
    """Recompute a stated formula. Returns why it fails, or ``None``.

    Only multiplication and division of the given inputs are followed, which
    covers the cases this corpus produces (a current across a shunt, a rate
    over a period). Anything else is reported as not reproducible rather
    than accepted on trust.
    """
    target = _as_si(ground.claim)
    if target is None:
        return (
            f"the result {ground.claim!r} is not a number with a unit this "
            "check can read"
        )
    values = []
    for position, item in enumerate(ground.inputs):
        if not item.value.strip():
            return (
                f"input {position + 1} has no value of its own. Observed in a "
                "real run: the number was written into the quote instead "
                f"({item.quote[:60]!r}). Put it in \"value\" so the "
                "arithmetic can be recomputed"
            )
        parsed = _as_si(item.value)
        if parsed is None:
            return f"the input {item.value!r} is not a number with a unit"
        values.append(parsed[0])

    product = 1.0
    for value in values:
        product *= value
    candidates = [product]
    if len(values) == 2 and values[1] != 0:
        candidates.append(values[0] / values[1])
    if len(values) == 2 and values[0] != 0:
        candidates.append(values[1] / values[0])
    if len(values) == 1:
        candidates.append(values[0])

    wanted = target[0]
    for candidate in candidates:
        if abs(candidate - wanted) <= max(abs(wanted) * 1e-3, 1e-12):
            return None
    shown = " x ".join(item.value for item in ground.inputs)
    return (
        f"{shown} does not give {ground.claim}: the inputs multiply to "
        f"{product:g} in base units, the claim is {wanted:g}"
    )


def claims_of(step: TestStep) -> list[tuple[str, str]]:
    """The concrete claims of one step: (what was claimed, where).

    A claim is a value that could be wrong: a number with a unit, or a DTC
    code. Prose without a value is not a claim - "the system reaches
    standby" needs no source, "within 50 ms" does.
    """
    from .validation import _dtc_claims, _numeric_claims

    found: list[tuple[str, str]] = []
    for index, expectation in enumerate(step.expectations):
        for value, unit in sorted(_numeric_claims(expectation)):
            found.append((_format_claim(value, unit), f"expectations[{index}]"))
        for dtc in sorted(_dtc_claims(expectation)):
            found.append((dtc, f"expectations[{index}]"))
        for code in sorted({m.group(0).lower() for m in _HEX_CODE.finditer(expectation)}):
            found.append((code, f"expectations[{index}]"))
    for index, measurement in enumerate(step.measurements):
        if measurement.value is None or not str(measurement.value).strip():
            continue
        for value, unit in sorted(
            _numeric_claims(f"{measurement.value} {measurement.unit or ''}")
        ):
            found.append((_format_claim(value, unit), f"measurements[{index}]"))
    # the same value in the same field is one claim; the same value in two
    # different fields is two claims, and each needs its own ground
    seen: set[tuple[str, str]] = set()
    unique = []
    for claim, where in found:
        if (claim, where) in seen:
            continue
        seen.add((claim, where))
        unique.append((claim, where))
    return unique


def _format_claim(value: float, unit: str) -> str:
    number = f"{value:g}"
    return f"{number} {unit}".strip()


def _ground_covers(ground: Ground, claim: str) -> bool:
    """Does this ground speak about this claim?

    Compared by value and unit, not by string: ``0.05 s`` and ``50 ms`` are
    the same claim, and a ground that writes it the other way still counts.
    """
    from .validation import _dtc_claims, _numeric_claims

    text = ground.claim
    if claim in _dtc_claims(text):
        return True
    if claim.startswith("0x") and claim in {
        m.group(0).lower() for m in _HEX_CODE.finditer(text)
    }:
        return True
    wanted = _numeric_claims(claim)
    if not wanted:
        return claim.strip().lower() in text.strip().lower()
    mine = _numeric_claims(text)
    if wanted & mine:
        return True
    # the units may differ in scale: compare in base units
    target = _as_si(claim)
    if target is None:
        return False
    for value, unit in mine:
        other = _as_si(_format_claim(value, unit))
        if other is None or other[1] != target[1]:
            continue
        if abs(other[0] - target[0]) <= max(abs(target[0]) * 1e-9, 1e-15):
            return True
    return False


def missing_grounds(
    case: TestCase, *, severity: Severity
) -> list[ValidationIssue]:
    """Concrete values that no ground of their own step speaks about.

    A ground addresses a field (``expectations[0]``). Matching by value
    alone is kept for grounds that name no target, but then an ambiguity is
    reported rather than silently resolved: a step may state a turn-on and a
    turn-off threshold with the same number, and one ground must not pass
    for both.
    """
    issues: list[ValidationIssue] = []
    for label, steps in (
        ("preconditions", case.preconditions),
        ("steps", case.steps),
        ("postconditions", case.postconditions),
    ):
        for index, step in enumerate(steps):
            claims = claims_of(step)
            by_value: dict[str, list[str]] = {}
            for claim, origin in claims:
                by_value.setdefault(claim, []).append(origin)
            for claim, origin in claims:
                targeted = [
                    g
                    for g in step.grounds
                    if g.target == origin
                    and not g.is_stale
                    and _ground_covers(g, claim)
                ]
                if targeted:
                    continue
                loose = [
                    g
                    for g in step.grounds
                    if not g.target and not g.is_stale and _ground_covers(g, claim)
                ]
                if loose:
                    if len(by_value[claim]) > 1:
                        issues.append(
                            ValidationIssue(
                                "C_GROUND_AMBIGUOUS_TARGET",
                                Severity.CLARIFICATION,
                                f"{case.case_id}.{label}[{index}].{origin}",
                                f"{claim!r} appears in "
                                f"{len(by_value[claim])} places of this step "
                                f"({', '.join(by_value[claim])}) and the "
                                "ground names no target. Say which field it "
                                "belongs to - the same number can be a "
                                "turn-on and a turn-off threshold.",
                            )
                        )
                    continue
                issues.append(
                    ValidationIssue(
                        "C_CLAIM_WITHOUT_GROUND"
                        if severity is Severity.CLARIFICATION
                        else "I_CLAIM_WITHOUT_GROUND",
                        severity,
                        f"{case.case_id}.{label}[{index}].{origin}",
                        f"{claim!r} is stated as a fact but no ground of this "
                        "step says where it comes from. Name the requirement "
                        "or context that states it, show the calculation, or "
                        "turn it into an open question.",
                    )
                )
    return issues


def check_grounds(
    case: TestCase,
    requirements: Sequence[Requirement],
    *,
    context: Sequence = (),
) -> list[ValidationIssue]:
    """Every ground checked against what it names, and every claim covered.

    How a missing ground is weighed depends on the rule version the draft
    was written under. A draft from before the rule is marked as legacy and
    stays approvable; a draft written under the current rule must cover its
    concrete values or carry a reviewer's documented reason for not doing
    so. A ground that is *wrong* is an error either way.
    """
    issues: list[ValidationIssue] = []
    sources = _sources(requirements, context)
    legacy = case.grounding_rules != GROUNDING_RULES_VERSION
    issues.extend(
        missing_grounds(
            case, severity=Severity.INFO if legacy else Severity.CLARIFICATION
        )
    )

    for label, steps in (
        ("preconditions", case.preconditions),
        ("steps", case.steps),
        ("postconditions", case.postconditions),
    ):
        for index, step in enumerate(steps):
            path = f"{case.case_id}.{label}[{index}]"
            for number, ground in enumerate(step.grounds):
                where = f"{path}.grounds[{number}]"
                issues.extend(_check_one(ground, where, sources))
    return issues


def _check_one(
    ground: Ground, where: str, sources: dict[str, _Source]
) -> Iterable[ValidationIssue]:
    if ground.is_stale:
        yield ValidationIssue(
            "C_GROUND_STALE",
            Severity.CLARIFICATION,
            where,
            f"the ground for {ground.claim!r} was written for a field that "
            f"has since been edited: {ground.stale_reason}. Check that the "
            "quote still supports what the field now says, then restate it.",
        )
        return
    if ground.kind is GroundKind.UNRESOLVED:
        if not (ground.question or "").strip():
            yield ValidationIssue(
                "C_GROUND_UNRESOLVED_WITHOUT_QUESTION",
                Severity.CLARIFICATION,
                where,
                f"{ground.claim!r} is marked as unresolved but carries no "
                "open question. An unresolved value must become a question, "
                "not an expectation.",
            )
        return

    if ground.needs_a_source:
        if not (ground.source or "").strip():
            yield ValidationIssue(
                "C_GROUND_WITHOUT_SOURCE",
                Severity.CLARIFICATION,
                where,
                f"{ground.claim!r} claims to come from the "
                f"{ground.kind.value.replace('_', ' ')} but names no source.",
            )
            return
        source = sources.get(ground.source)
        if source is None:
            yield ValidationIssue(
                "C_GROUND_SOURCE_UNKNOWN",
                Severity.CLARIFICATION,
                where,
                f"{ground.claim!r} cites {ground.source!r}, which is neither "
                "a requirement under test nor a context item that was sent.",
            )
            return
        if not (ground.quote or "").strip():
            yield ValidationIssue(
                "C_GROUND_WITHOUT_QUOTE",
                Severity.CLARIFICATION,
                where,
                f"{ground.claim!r} cites {ground.source} without quoting the "
                "sentence that carries the value.",
            )
        elif not _quote_is_in(ground.quote, source.text):
            yield ValidationIssue(
                "C_GROUND_QUOTE_NOT_FOUND",
                Severity.CLARIFICATION,
                where,
                f"the quote for {ground.claim!r} does not appear in "
                f"{ground.source}: {ground.quote[:120]!r}",
            )
        return

    if ground.kind is GroundKind.CALCULATION:
        for position, item in enumerate(ground.inputs):
            if not item.source.strip():
                yield ValidationIssue(
                    "C_GROUND_WITHOUT_SOURCE",
                    Severity.CLARIFICATION,
                    f"{where}.inputs[{position}]",
                    f"the input {item.value or item.quote[:40]!r} of the "
                    "calculation names no source.",
                )
            elif item.source not in sources:
                yield ValidationIssue(
                    "C_GROUND_SOURCE_UNKNOWN",
                    Severity.CLARIFICATION,
                    f"{where}.inputs[{position}]",
                    f"the input {item.value!r} cites {item.source!r}, which "
                    "is neither a requirement under test nor a context item.",
                )
            elif item.quote and not _quote_is_in(item.quote, sources[item.source].text):
                yield ValidationIssue(
                    "C_GROUND_QUOTE_NOT_FOUND",
                    Severity.CLARIFICATION,
                    f"{where}.inputs[{position}]",
                    f"the quote for the input {item.value!r} does not appear "
                    f"in {item.source}: {item.quote[:120]!r}",
                )
        problem = check_calculation(ground)
        if problem:
            yield ValidationIssue(
                "C_GROUND_CALCULATION_NOT_REPRODUCIBLE",
                Severity.CLARIFICATION,
                where,
                f"{problem}. State the inputs and the formula so a reviewer "
                "can follow it, or raise an open question.",
            )


__all__ = ["check_calculation", "check_grounds"]
