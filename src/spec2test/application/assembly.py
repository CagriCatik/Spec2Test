"""Turn validated contract objects into domain objects.

The application - not the model - assigns IDs and the review status.
"""
from __future__ import annotations

from typing import Sequence

from ..domain.models import (
    GROUNDING_RULES_VERSION,
    Ground,
    GroundInput,
    GroundKind,
    Measurement,
    OpenQuestion,
    QuestionOrigin,
    Requirement,
    ReviewStatus,
    Severity,
    TestCase,
    TestStep,
    ValidationIssue,
)
from ..domain.validation import requirement_lookup
from .contracts import (
    ModelResponseContract,
    OpenQuestionContract,
    StepContract,
    TestCaseContract,
)


def _ground(contract) -> Ground:
    """A proposed ground, taken as proposed. Checking happens in validation."""
    return Ground(
        claim=contract.claim,
        kind=GroundKind(contract.kind),
        source=contract.source,
        quote=contract.quote,
        inputs=tuple(
            GroundInput(value=i.value, source=i.source, quote=i.quote)
            for i in contract.inputs
        ),
        formula=contract.formula,
        question=contract.question,
    )


def _steps(contracts: Sequence[StepContract]) -> tuple[TestStep, ...]:
    """Renumber each list to 1..n, keeping the order the model gave.

    Models often number continuously across preconditions, steps and
    postconditions (1,2 | 3,4,5 | 6). That carries the same information, and
    rejecting a whole draft over it would be pedantry - the sequence is what
    matters, not the first number.
    """
    ordered = sorted(contracts, key=lambda step: step.order)
    return tuple(
        TestStep(
            order=position,
            action=step.action,
            expectations=tuple(step.expectations),
            measurements=tuple(
                Measurement(
                    quantity=m.quantity,
                    value=m.value,
                    unit=m.unit,
                    tolerance=m.tolerance,
                    evidence=m.evidence,
                )
                for m in step.measurements
            ),
            grounds=tuple(_ground(g) for g in step.grounds),
        )
        for position, step in enumerate(ordered, 1)
    )


def _question(
    contract: OpenQuestionContract,
    question_id: str,
    *,
    resolve: dict[str, Requirement],
) -> OpenQuestion:
    return OpenQuestion(
        question_id=question_id,
        question=contract.question,
        requirement_ids=tuple(
            resolve[ref].requirement_id if ref in resolve else ref
            for ref in contract.requirement_refs
        ),
        origin=QuestionOrigin.MODEL,
        suggestion=contract.suggestion,
    )


def build_test_case(
    contract: TestCaseContract,
    requirements: Sequence[Requirement],
    *,
    case_id: str,
) -> TestCase:
    """Map one contract case. Unresolvable references are kept verbatim so
    validation can reject them instead of silently dropping them."""
    lookup = requirement_lookup(requirements)
    requirement_ids: list[str] = []
    resolved: list[Requirement] = []
    for ref in contract.requirement_refs:
        req = lookup.get(ref)
        if req is None:
            requirement_ids.append(ref)
        else:
            requirement_ids.append(req.requirement_id)
            resolved.append(req)

    questions = tuple(
        _question(q, f"{case_id}-Q{i + 1}", resolve=lookup)
        for i, q in enumerate(contract.open_questions)
    )
    return TestCase(
        case_id=case_id,
        title=contract.title,
        objective=contract.objective,
        requirement_ids=tuple(requirement_ids),
        preconditions=_steps(contract.preconditions),
        steps=_steps(contract.steps),
        postconditions=_steps(contract.postconditions),
        open_questions=questions,
        review_status=ReviewStatus.DRAFT,  # a model answer always starts as draft
        revision=1,
        source_spans=tuple(span for req in resolved for span in req.spans),
        # written now, so it is held to the rule that applies now
        grounding_rules=GROUNDING_RULES_VERSION,
    )


def build_from_response(
    response: ModelResponseContract,
    requirements: Sequence[Requirement],
    *,
    unit_id: str,
) -> tuple[list[TestCase], list[OpenQuestion]]:
    lookup = requirement_lookup(requirements)
    cases = [
        build_test_case(contract, requirements, case_id=f"TC-{unit_id}-{i + 1:02d}")
        for i, contract in enumerate(response.test_cases)
    ]
    questions = [
        _question(q, f"OQ-{unit_id}-{i + 1:02d}", resolve=lookup)
        for i, q in enumerate(response.open_questions)
    ]
    return cases, questions


# ------------------------------------------------------------- legacy -------
def import_legacy_case(
    payload: dict, *, case_id: str, requirements: Sequence[Requirement] = ()
) -> tuple[TestCase, list[ValidationIssue]]:
    """Read the historical flat shape (``actions`` / ``expected_results``).

    Pairing by list index is never invented. Expectations are attached only
    when there is exactly one action; otherwise the original content is kept in
    ``legacy_payload`` and a manual assignment is required.
    """
    issues: list[ValidationIssue] = []
    actions = _as_list(payload.get("actions"))
    expectations = _as_list(payload.get("expected_results"))
    refs = _as_list(payload.get("linked_requirements"))
    lookup = requirement_lookup(requirements)

    legacy_payload: dict | None = None
    if len(actions) == 1:
        steps = (TestStep(order=1, action=actions[0], expectations=tuple(expectations)),)
    else:
        steps = tuple(
            TestStep(order=i + 1, action=action) for i, action in enumerate(actions)
        )
        if expectations:
            legacy_payload = {
                "actions": actions,
                "expected_results": expectations,
                "reason": "ambiguous action/expectation mapping preserved verbatim",
            }
            issues.append(
                ValidationIssue(
                    "C_LEGACY_MAPPING_REQUIRED",
                    Severity.CLARIFICATION,
                    f"test_case[{case_id}]",
                    f"{len(actions)} actions and {len(expectations)} expected results "
                    "cannot be paired without guessing; original content preserved",
                )
            )

    case = TestCase(
        case_id=case_id,
        title=str(payload.get("testcase_name") or "").strip(),
        objective=str(payload.get("objective") or "").strip(),
        requirement_ids=tuple(
            lookup[ref].requirement_id if ref in lookup else ref for ref in refs
        ),
        preconditions=tuple(
            TestStep(order=i + 1, action=text)
            for i, text in enumerate(_as_list(payload.get("preconditions")))
        ),
        steps=steps,
        postconditions=tuple(
            TestStep(order=i + 1, action=text)
            for i, text in enumerate(_as_list(payload.get("postconditions")))
        ),
        review_status=ReviewStatus.DRAFT,
        legacy_payload=legacy_payload,
    )
    return case, issues


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []
