"""Versioned, strict JSON contract for model answers.

The contract has no ambiguous mixed types ("string or list") and rejects extra
fields. It also carries no IDs and no review status: the application assigns
those itself, so a model can never approve its own output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    ValidationError,
    field_validator,
)

from ..domain.models import GroundKind, Severity, ValidationIssue

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _as_text(value: object) -> object:
    """Accept a measured value written as a number.

    A model writes ``"value": 500`` as often as ``"value": "500"``, and
    rejecting the whole answer over it costs a full repair cycle. The exact
    notation is preserved when it is given as text; a number becomes its
    plain string form. Lists and objects are still refused - they would hide
    a structural mistake.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        text = repr(value)
        return text[:-2] if text.endswith(".0") else text
    return value


#: a scalar that is stored as text, however the model wrote it
Scalar = Annotated[str, BeforeValidator(_as_text)]


# ------------------------------------------------------------- contract -----
class MeasurementContract(BaseModel):
    model_config = _STRICT

    quantity: str
    #: ``None`` means: the quantity is named, but the requirement states no
    #: target value. That is an honest answer and becomes a finding, not a
    #: reason to reject the whole draft.
    value: Scalar | None = None
    unit: str | None = None
    tolerance: Scalar | None = None
    evidence: str | None = None


class GroundInputContract(BaseModel):
    """One starting value of a calculation, with where it comes from."""

    model_config = _STRICT

    value: Scalar
    source: str
    quote: str = ""


class GroundContract(BaseModel):
    """Where one concrete expectation or measured value gets its authority.

    ``kind`` must be one of target_requirement, context, calculation,
    unresolved. Anything else is refused rather than guessed at.
    ``target`` names the field of the step the claim stands in, such as
    ``expectations[0]``: the value alone is not an address.
    """

    model_config = _STRICT

    claim: str
    kind: str
    target: str | None = None
    source: str | None = None
    quote: str | None = None
    inputs: list[GroundInputContract] = []
    formula: str | None = None
    question: str | None = None

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        allowed = {k.value for k in GroundKind}
        text = str(value).strip().lower()
        if text not in allowed:
            raise ValueError(
                f"kind must be one of {sorted(allowed)}, not {value!r}"
            )
        return text


class StepContract(BaseModel):
    model_config = _STRICT

    order: int
    action: str
    expectations: list[str] = []
    measurements: list[MeasurementContract] = []
    grounds: list[GroundContract] = []


class OpenQuestionContract(BaseModel):
    model_config = _STRICT

    question: str
    requirement_refs: list[str] = []
    suggestion: str | None = None


class TestCaseContract(BaseModel):
    model_config = _STRICT

    title: str
    objective: str
    requirement_refs: list[str]
    preconditions: list[StepContract] = []
    steps: list[StepContract] = []
    postconditions: list[StepContract] = []
    open_questions: list[OpenQuestionContract] = []


class ModelResponseContract(BaseModel):
    model_config = _STRICT

    schema_version: str
    test_cases: list[TestCaseContract] = []
    open_questions: list[OpenQuestionContract] = []


def contract_json_schema() -> dict[str, Any]:
    return ModelResponseContract.model_json_schema()


# --------------------------------------------------------------- parsing ----
class ParseOutcome(Enum):
    """An empty answer, invalid JSON and a valid answer without test cases are
    three different results and must never collapse into one empty list."""

    OK = auto()
    EMPTY = auto()
    INVALID_JSON = auto()
    SCHEMA_INVALID = auto()
    NO_TEST_CASES = auto()


@dataclass
class ParseResult:
    outcome: ParseOutcome
    response: ModelResponseContract | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    looks_truncated: bool = False
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is ParseOutcome.OK

    def error_summary(self) -> str:
        return "; ".join(f"{i.code} {i.path}: {i.message}" for i in self.issues)


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if "\n" in body:
        first_line, rest = body.split("\n", 1)
        if first_line.strip().isalpha() or not first_line.strip():
            body = rest
    end = body.rfind("```")
    if end != -1:
        body = body[:end]
    return body.strip()


def _looks_truncated(text: str) -> bool:
    """Unbalanced brackets are a strong hint that the answer was cut off."""
    opens = text.count("{") + text.count("[")
    closes = text.count("}") + text.count("]")
    return opens > closes


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code, Severity.ERROR, path, message)


def parse_model_response(raw: str) -> ParseResult:
    """Parse a model answer against the versioned contract.

    Never silently drops elements: anything unusable is reported as an issue.
    """
    cleaned = strip_code_fences(raw or "")
    if not cleaned:
        return ParseResult(
            ParseOutcome.EMPTY,
            issues=[_issue("E_EMPTY_RESPONSE", "$", "the model returned no content")],
            raw=raw or "",
        )

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        truncated = _looks_truncated(cleaned)
        return ParseResult(
            ParseOutcome.INVALID_JSON,
            issues=[
                _issue(
                    "E_INVALID_JSON",
                    "$",
                    f"not valid JSON ({exc.msg} at line {exc.lineno} column {exc.colno})"
                    + (" - the answer appears to be truncated" if truncated else ""),
                )
            ],
            looks_truncated=truncated,
            raw=raw,
        )

    if isinstance(data, list):
        # A bare array is the legacy shape, not the versioned contract.
        if not data:
            return ParseResult(
                ParseOutcome.NO_TEST_CASES,
                issues=[
                    _issue(
                        "E_NO_TEST_CASES",
                        "$",
                        "valid JSON but no test case was returned",
                    )
                ],
                raw=raw,
            )
        return ParseResult(
            ParseOutcome.SCHEMA_INVALID,
            issues=[
                _issue(
                    "E_SCHEMA",
                    "$",
                    "expected an object with 'schema_version' and 'test_cases', "
                    "got a bare JSON array (legacy shape)",
                )
            ],
            raw=raw,
        )

    if not isinstance(data, dict):
        return ParseResult(
            ParseOutcome.SCHEMA_INVALID,
            issues=[
                _issue("E_SCHEMA", "$", f"expected a JSON object, got {type(data).__name__}")
            ],
            raw=raw,
        )

    version = data.get("schema_version")
    if version is not None and version not in SUPPORTED_SCHEMA_VERSIONS:
        return ParseResult(
            ParseOutcome.SCHEMA_INVALID,
            issues=[
                _issue(
                    "E_SCHEMA_VERSION",
                    "$.schema_version",
                    f"unsupported schema_version {version!r}; "
                    f"supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
                )
            ],
            raw=raw,
        )

    try:
        response = ModelResponseContract.model_validate(data)
    except ValidationError as exc:
        issues = [
            _issue(
                "E_INVALID_TYPE",
                "$." + ".".join(str(p) for p in err["loc"]),
                err["msg"],
            )
            for err in exc.errors()
        ]
        return ParseResult(ParseOutcome.SCHEMA_INVALID, issues=issues, raw=raw)

    if not response.test_cases:
        return ParseResult(
            ParseOutcome.NO_TEST_CASES,
            response=response,
            issues=[
                _issue(
                    "E_NO_TEST_CASES",
                    "$.test_cases",
                    "valid answer, but it contains no test case",
                )
            ],
            raw=raw,
        )

    return ParseResult(ParseOutcome.OK, response=response, raw=raw)
