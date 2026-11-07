"""Check the grounds a draft gives for its concrete expectations.

What is checked here is narrow on purpose:

* a named source exists among the requirements under test or the context
  that was sent, and the quote really appears in it;
* a calculation's inputs each have their own source, and the arithmetic
  works out;
* an expectation that carries a value has a ground at all, and an
  unresolved one is an open question rather than a binding expectation.

What is **not** checked, and cannot be: whether the value is the right one
for this test. ``HW_Comparator_Reference_Voltage`` may be quoted correctly
and still be the wrong threshold for the scenario. That stays with the
tester; this only makes the claim followable.

The model proposes the grounds. A missing or wrong one is a finding, never
a silent repair: inventing a source would defeat the purpose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .models import Ground, GroundKind, Requirement, Severity, TestCase
from .validation import ValidationIssue, normalise_math

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
    for item in ground.inputs:
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


def check_grounds(
    case: TestCase,
    requirements: Sequence[Requirement],
    *,
    context: Sequence = (),
) -> list[ValidationIssue]:
    """Every ground of this draft, checked against what it names."""
    issues: list[ValidationIssue] = []
    sources = _sources(requirements, context)

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
    if ground.kind is GroundKind.UNRESOLVED:
        if not (ground.question or "").strip():
            yield ValidationIssue(
                "E_GROUND_UNRESOLVED_WITHOUT_QUESTION",
                Severity.ERROR,
                where,
                f"{ground.claim!r} is marked as unresolved but carries no "
                "open question. An unresolved value must become a question, "
                "not an expectation.",
            )
        return

    if ground.needs_a_source:
        if not (ground.source or "").strip():
            yield ValidationIssue(
                "E_GROUND_WITHOUT_SOURCE",
                Severity.ERROR,
                where,
                f"{ground.claim!r} claims to come from the "
                f"{ground.kind.value.replace('_', ' ')} but names no source.",
            )
            return
        source = sources.get(ground.source)
        if source is None:
            yield ValidationIssue(
                "E_GROUND_SOURCE_UNKNOWN",
                Severity.ERROR,
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
                "E_GROUND_QUOTE_NOT_FOUND",
                Severity.ERROR,
                where,
                f"the quote for {ground.claim!r} does not appear in "
                f"{ground.source}: {ground.quote[:120]!r}",
            )
        return

    if ground.kind is GroundKind.CALCULATION:
        for position, item in enumerate(ground.inputs):
            if item.source not in sources:
                yield ValidationIssue(
                    "E_GROUND_SOURCE_UNKNOWN",
                    Severity.ERROR,
                    f"{where}.inputs[{position}]",
                    f"the input {item.value!r} cites {item.source!r}, which "
                    "is neither a requirement under test nor a context item.",
                )
            elif item.quote and not _quote_is_in(item.quote, sources[item.source].text):
                yield ValidationIssue(
                    "E_GROUND_QUOTE_NOT_FOUND",
                    Severity.ERROR,
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
