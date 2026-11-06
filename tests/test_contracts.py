"""Contract tests: strict types, no ambiguous unions, no invented pairing."""
from __future__ import annotations

import json

import pytest

from spec2test.application.assembly import import_legacy_case
from spec2test.application.contracts import (
    SCHEMA_VERSION,
    ParseOutcome,
    contract_json_schema,
    parse_model_response,
    strip_code_fences,
)
from spec2test.application.legacy import parse_legacy_cases
from spec2test.domain.models import (
    Requirement,
    ReviewStatus,
    Severity,
    SourceSpan,
    TestCase,
)
from spec2test.domain.validation import validate_test_case


def valid_payload(**case_overrides) -> dict:
    case = {
        "title": "t",
        "objective": "o",
        "requirement_refs": ["SYS-REQ-0001"],
        "steps": [{"order": 1, "action": "a", "expectations": ["e"]}],
    }
    case.update(case_overrides)
    return {"schema_version": SCHEMA_VERSION, "test_cases": [case]}


# --------------------------------------------------------------- outcomes ----
@pytest.mark.parametrize(
    "raw,outcome",
    [
        ("", ParseOutcome.EMPTY),
        ("   \n ", ParseOutcome.EMPTY),
        ("I cannot help", ParseOutcome.INVALID_JSON),
        ("[]", ParseOutcome.NO_TEST_CASES),
        ('{"schema_version":"1.0","test_cases":[]}', ParseOutcome.NO_TEST_CASES),
        ('[{"testcase_name":"x"}]', ParseOutcome.SCHEMA_INVALID),
        ('"a string"', ParseOutcome.SCHEMA_INVALID),
        ('{"schema_version":"2.0","test_cases":[]}', ParseOutcome.SCHEMA_INVALID),
    ],
)
def test_outcomes_are_distinguished(raw, outcome):
    result = parse_model_response(raw)
    assert result.outcome is outcome
    assert result.issues, "every unusable answer carries a finding"
    assert result.raw == raw


def test_a_valid_answer_parses():
    result = parse_model_response(json.dumps(valid_payload()))
    assert result.ok
    assert result.response.test_cases[0].title == "t"


def test_code_fences_are_removed():
    fenced = "```json\n" + json.dumps(valid_payload()) + "\n```"
    assert parse_model_response(fenced).ok
    assert strip_code_fences('```json\n[1]\n```') == "[1]"
    assert strip_code_fences("plain") == "plain"


def test_truncation_is_detected():
    result = parse_model_response('{"schema_version":"1.0","test_cases":[{"title":')
    assert result.looks_truncated is True
    assert "truncated" in result.error_summary()


# -------------------------------------------------------------- strictness ---
def test_a_string_where_a_list_belongs_is_rejected():
    payload = valid_payload(requirement_refs="SYS-REQ-0001")
    result = parse_model_response(json.dumps(payload))
    assert result.outcome is ParseOutcome.SCHEMA_INVALID
    assert "E_INVALID_TYPE" in {issue.code for issue in result.issues}


def test_a_number_where_a_string_belongs_is_rejected():
    payload = valid_payload(title=17)
    assert parse_model_response(json.dumps(payload)).outcome is ParseOutcome.SCHEMA_INVALID


def test_extra_fields_are_rejected():
    payload = valid_payload(id="TC-99")
    result = parse_model_response(json.dumps(payload))
    assert "Extra inputs are not permitted" in result.error_summary()


def test_the_contract_carries_no_ids_and_no_review_status():
    schema = contract_json_schema()
    case_properties = schema["$defs"]["TestCaseContract"]["properties"]
    assert "id" not in case_properties
    assert "case_id" not in case_properties
    assert "review_status" not in case_properties
    assert set(schema["properties"]) == {
        "schema_version",
        "test_cases",
        "open_questions",
    }


def test_a_step_needs_an_action_and_an_order():
    payload = valid_payload(steps=[{"expectations": ["e"]}])
    assert parse_model_response(json.dumps(payload)).outcome is ParseOutcome.SCHEMA_INVALID


def test_measurements_are_typed_objects():
    payload = valid_payload(
        steps=[
            {
                "order": 1,
                "action": "a",
                "expectations": ["0.2 deg"],
                "measurements": [
                    {
                        "quantity": "deviation",
                        "value": "0.2",
                        "unit": "deg",
                        "evidence": "at most 0.2 deg",
                    }
                ],
            }
        ]
    )
    result = parse_model_response(json.dumps(payload))
    assert result.ok
    assert result.response.test_cases[0].steps[0].measurements[0].unit == "deg"


# ------------------------------------------------------------ legacy shape ---
def requirement():
    text = "SYS-REQ-0001: The system shall react within 100 ms."
    return Requirement(
        "REQ-1",
        text,
        (SourceSpan("doc", 0, len(text), text, page=1),),
        external_id="SYS-REQ-0001",
    )


def test_a_single_action_takes_all_expectations():
    case, issues = import_legacy_case(
        {
            "testcase_name": "legacy",
            "objective": "o",
            "linked_requirements": ["SYS-REQ-0001"],
            "actions": ["do the thing"],
            "expected_results": ["it reacts within 100 ms", "no fault is stored"],
        },
        case_id="TC-L1",
        requirements=[requirement()],
    )
    assert len(case.steps) == 1
    assert len(case.steps[0].expectations) == 2
    assert issues == []
    assert case.legacy_payload is None
    assert case.requirement_ids == ("REQ-1",)
    assert case.review_status is ReviewStatus.DRAFT


def test_an_ambiguous_mapping_is_never_invented_by_list_index():
    payload = {
        "testcase_name": "legacy",
        "objective": "o",
        "linked_requirements": ["SYS-REQ-0001"],
        "actions": ["step one", "step two", "step three"],
        "expected_results": ["result A", "result B"],
    }
    case, issues = import_legacy_case(
        payload, case_id="TC-L2", requirements=[requirement()]
    )

    assert [step.expectations for step in case.steps] == [(), (), ()]
    assert case.legacy_payload["actions"] == payload["actions"]
    assert case.legacy_payload["expected_results"] == payload["expected_results"]
    assert "C_LEGACY_MAPPING_REQUIRED" in {issue.code for issue in issues}

    report = validate_test_case(case, [requirement()])
    assert "C_LEGACY_MAPPING_REQUIRED" in report.codes()
    assert report.blocks_approval is True


def test_a_flat_string_field_is_accepted_by_the_legacy_importer():
    case, _ = import_legacy_case(
        {
            "testcase_name": "legacy",
            "objective": "o",
            "linked_requirements": "SYS-REQ-0001",
            "actions": "single action",
            "expected_results": "single result",
            "preconditions": "ready",
            "postconditions": "cleaned up",
        },
        case_id="TC-L3",
        requirements=[requirement()],
    )
    assert case.steps[0].action == "single action"
    assert case.steps[0].expectations == ("single result",)
    assert case.preconditions[0].action == "ready"
    assert case.postconditions[0].action == "cleaned up"


def test_the_legacy_parser_rejects_non_objects_instead_of_emitting_empty_cases():
    raw = json.dumps(
        [
            {
                "testcase_name": "ok",
                "objective": "o",
                "actions": ["a"],
                "expected_results": ["e"],
                "postconditions": ["p"],
            },
            "junk",
            None,
            7,
        ]
    )
    result = parse_legacy_cases(raw)
    assert len(result.cases) == 1
    assert sum(1 for i in result.issues if i.code == "E_INVALID_TYPE") == 3
    assert all(any(str(v).strip() for v in case.values()) for case in result.cases)


def test_the_legacy_parser_flags_wrong_field_types():
    raw = json.dumps([{"testcase_name": "x", "actions": {"a": 1}, "objective": 5}])
    result = parse_legacy_cases(raw)
    codes = [issue.code for issue in result.issues]
    assert codes.count("E_INVALID_TYPE") == 2


def test_the_legacy_parser_flags_empty_required_fields():
    raw = json.dumps([{"testcase_name": "x", "objective": "", "actions": []}])
    result = parse_legacy_cases(raw)
    assert "E_EMPTY_FIELD" in {issue.code for issue in result.issues}


def test_the_legacy_parser_reports_unknown_fields_without_dropping_the_case():
    raw = json.dumps(
        [
            {
                "testcase_name": "x",
                "objective": "o",
                "actions": ["a"],
                "expected_results": ["e"],
                "postconditions": ["p"],
                "severity": "high",
            }
        ]
    )
    result = parse_legacy_cases(raw)
    unknown = [i for i in result.issues if i.code == "C_UNKNOWN_FIELD"]
    assert unknown and unknown[0].severity is Severity.CLARIFICATION
    assert len(result.cases) == 1


def test_the_legacy_parser_extracts_an_array_from_surrounding_prose():
    raw = 'Here you go:\n[{"testcase_name":"x","objective":"o","actions":["a"],' \
          '"expected_results":["e"],"postconditions":["p"]}]\nHope that helps.'
    result = parse_legacy_cases(raw)
    assert result.ok and len(result.cases) == 1


# ------------------------------------------- measured values as numbers ------
@pytest.mark.parametrize(
    "written,stored",
    [
        ("500", "500"),
        (500, "500"),
        (0.2, "0.2"),
        (-10.5, "-10.5"),
        (500.0, "500"),
    ],
)
def test_a_measured_value_may_be_written_as_a_number(written, stored):
    """Half the units of a real run needed a repair over exactly this."""
    payload = valid_payload(
        steps=[
            {
                "order": 1,
                "action": "measure",
                "expectations": ["e"],
                "measurements": [
                    {"quantity": "q", "value": written, "unit": "ms"}
                ],
            }
        ]
    )
    result = parse_model_response(json.dumps(payload))
    assert result.ok, result.error_summary()
    assert result.response.test_cases[0].steps[0].measurements[0].value == stored


def test_a_tolerance_may_be_written_as_a_number():
    payload = valid_payload(
        steps=[
            {
                "order": 1,
                "action": "measure",
                "expectations": ["e"],
                "measurements": [
                    {"quantity": "q", "value": "500", "tolerance": 5, "unit": "ms"}
                ],
            }
        ]
    )
    result = parse_model_response(json.dumps(payload))
    assert result.ok
    assert result.response.test_cases[0].steps[0].measurements[0].tolerance == "5"


@pytest.mark.parametrize("written", [[1, 2], {"a": 1}])
def test_a_structure_where_a_value_belongs_is_still_refused(written):
    payload = valid_payload(
        steps=[
            {
                "order": 1,
                "action": "measure",
                "expectations": ["e"],
                "measurements": [{"quantity": "q", "value": written}],
            }
        ]
    )
    assert parse_model_response(json.dumps(payload)).outcome is ParseOutcome.SCHEMA_INVALID


def test_other_fields_still_reject_a_number():
    """Only measured values are lenient; a numeric title is a real mistake."""
    assert parse_model_response(
        json.dumps(valid_payload(title=17))
    ).outcome is ParseOutcome.SCHEMA_INVALID


def test_a_measurement_without_a_value_is_accepted_and_flagged():
    """The model naming a quantity without claiming a value is honest.

    Rejecting the draft over it would punish exactly the behaviour the rules
    ask for; it becomes a clarification instead.
    """
    from spec2test.domain.models import Measurement, TestStep
    from spec2test.domain.validation import validate_test_case

    payload = valid_payload(
        steps=[
            {
                "order": 1,
                "action": "measure the latency",
                "expectations": ["the latency is within the limit"],
                "measurements": [
                    {"quantity": "response latency", "value": None, "unit": "ms"}
                ],
            }
        ]
    )
    result = parse_model_response(json.dumps(payload))
    assert result.ok, result.error_summary()
    assert result.response.test_cases[0].steps[0].measurements[0].value is None

    drafted = TestCase(
        case_id="TC-1",
        title="t",
        objective="o",
        requirement_ids=("REQ-1",),
        steps=(
            TestStep(
                1,
                "measure",
                ("the latency is within the limit",),
                (Measurement(quantity="response latency", value=None, unit="ms"),),
            ),
        ),
    )
    report = validate_test_case(drafted, [requirement()])
    assert "C_MEASUREMENT_WITHOUT_VALUE" in report.codes()
    assert report.blocks_draft is False, "it is a clarification, not a rejection"
    assert report.blocks_approval is True
