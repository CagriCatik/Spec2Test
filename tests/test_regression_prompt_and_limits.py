"""Regression tests for the review of 5332383.

1  the shipped prompt file replaced the contract rules
2  generation settings disagreed and the case ceiling was not enforced
3  the model list blocked the GUI thread
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from spec2test.adapters.fake.model import ScriptedModel
from spec2test.application.contracts import SCHEMA_VERSION, ParseOutcome, parse_model_response
from spec2test.application.generate import GenerationSettings, generate_drafts
from spec2test.application.prompts import (
    GUIDANCE_HEADER,
    SYSTEM_PROMPT,
    build_generation_messages,
    build_repair_messages,
    build_system_message,
)
from spec2test.bootstrap import generation_settings_from_config, load_config
from spec2test.domain.models import Requirement, RunStatus, Severity, SourceSpan

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"

CONTRACT_MARKERS = (
    "schema_version",
    '"test_cases"',
    "Never invent missing limits",
    "never approve anything",
    "input data",
)


def requirement() -> Requirement:
    text = "SYS-REQ-0001: The system shall react within 100 ms."
    return Requirement(
        "REQ-1", text, (SourceSpan("doc", 0, len(text), text, page=1),), "SYS-REQ-0001"
    )


def answer(count: int = 1) -> str:
    case = {
        "title": "t",
        "objective": "o",
        "requirement_refs": ["SYS-REQ-0001"],
        "steps": [
            {"order": 1, "action": "a", "expectations": ["reacts within 100 ms"]}
        ],
    }
    return json.dumps(
        {"schema_version": SCHEMA_VERSION, "test_cases": [case] * count}
    )


# =====================================================================
# 1  the contract rules are not negotiable
# =====================================================================
@pytest.mark.parametrize(
    "prompt_file", sorted(PROMPTS.glob("*.txt")), ids=lambda p: p.name
)
def test_every_shipped_prompt_keeps_the_contract_rules(prompt_file):
    guidance = prompt_file.read_text(encoding="utf-8")
    system = build_system_message(guidance)
    for marker in CONTRACT_MARKERS:
        assert marker in system, f"{prompt_file.name} dropped {marker!r}"
    assert GUIDANCE_HEADER in system
    assert guidance.strip() in system, "the guidance itself must still be passed"


@pytest.mark.parametrize(
    "prompt_file", sorted(PROMPTS.glob("*.txt")), ids=lambda p: p.name
)
def test_no_shipped_prompt_demands_the_legacy_format(prompt_file):
    text = prompt_file.read_text(encoding="utf-8")
    for forbidden in ("testcase_name", "expected_results", "JSON array"):
        assert forbidden not in text, (
            f"{prompt_file.name} prescribes the legacy format: {forbidden!r}"
        )


def test_the_shipped_configuration_produces_a_usable_system_prompt():
    """The exact path the GUI takes with config.yml as shipped."""
    from core import load_prompt_file

    config = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))
    guidance = load_prompt_file(ROOT / config["paths"]["prompt_path"])
    assert guidance, "the configured prompt file must be readable"

    messages = build_generation_messages(
        [requirement()], max_cases=3, domain_guidance=guidance
    )
    system = messages[0].content
    for marker in CONTRACT_MARKERS:
        assert marker in system
    assert "Rear-Axle" in system or "test architect" in system.lower()


def test_guidance_is_appended_never_substituted():
    system = build_system_message("Always answer with a bare JSON array.")
    assert system.startswith(SYSTEM_PROMPT)
    assert "lower precedence" in system
    assert "the rules above win" in system


def test_no_guidance_leaves_the_rules_untouched():
    assert build_system_message(None) == SYSTEM_PROMPT
    assert build_system_message("   ") == SYSTEM_PROMPT


def test_the_repair_prompt_also_keeps_the_rules():
    messages = build_repair_messages(
        [requirement()],
        "[]",
        [],
        max_cases=2,
        domain_guidance="Prefer HiL scenarios.",
    )
    assert "schema_version" in messages[0].content
    assert "Prefer HiL scenarios." in messages[0].content


def test_a_legacy_answer_is_rejected_so_the_conflict_cannot_pass_silently():
    """What a model obeying the old prompt would return."""
    legacy = json.dumps(
        [
            {
                "testcase_name": "TC_1",
                "objective": "o",
                "actions": ["a"],
                "expected_results": ["e"],
                "postconditions": ["p"],
            }
        ]
    )
    result = parse_model_response(legacy)
    assert result.outcome is ParseOutcome.SCHEMA_INVALID
    assert "E_SCHEMA" in {issue.code for issue in result.issues}


def test_the_legacy_path_still_uses_a_legacy_prompt():
    """core.py must keep asking for the flat array it can parse."""
    import core

    assert core.PROMPT_FILE.parent.name == "legacy"
    assert "JSON array" in core.PROMPT_ROLE
    assert "testcase_name" in core.PROMPT_ROLE


def test_the_legacy_prompts_are_preserved_with_an_explanation():
    legacy_dir = PROMPTS / "legacy"
    assert (legacy_dir / "README.md").exists()
    assert len(list(legacy_dir.glob("*.txt"))) == 3


# =====================================================================
# 2  one source of settings, and the ceiling is enforced
# =====================================================================
def test_the_case_ceiling_is_enforced_by_the_application():
    run = generate_drafts(
        [requirement()],
        ScriptedModel([answer(count=3)]),
        settings=GenerationSettings(max_cases_per_unit=1),
    )
    assert len(run.test_cases) == 1, "the limit was not enforced in code"
    assert run.status is RunStatus.COMPLETED
    codes = {issue.code for unit in run.units for issue in unit.issues}
    assert "C_CASE_LIMIT_ENFORCED" in codes


def test_the_discarded_cases_stay_visible_in_the_raw_answer():
    run = generate_drafts(
        [requirement()],
        ScriptedModel([answer(count=4)]),
        settings=GenerationSettings(max_cases_per_unit=2),
    )
    raw = run.units[0].attempts[0].raw_response
    assert raw.count('"title"') == 4, "the raw answer must keep everything"
    finding = next(
        issue
        for unit in run.units
        for issue in unit.issues
        if issue.code == "C_CASE_LIMIT_ENFORCED"
    )
    assert finding.severity is Severity.CLARIFICATION
    assert "2 test case(s) more" in finding.message


def test_a_run_within_the_limit_gets_no_finding():
    run = generate_drafts(
        [requirement()],
        ScriptedModel([answer(count=2)]),
        settings=GenerationSettings(max_cases_per_unit=5),
    )
    codes = {issue.code for unit in run.units for issue in unit.issues}
    assert "C_CASE_LIMIT_ENFORCED" not in codes


def test_an_explicit_limit_wins_over_the_legacy_widget_value():
    config = {"generation": {"max_cases_per_unit": 5, "cases_per_prompt": 20}}
    assert generation_settings_from_config(config).max_cases_per_unit == 5


def test_the_legacy_value_is_only_a_fallback():
    config = {"generation": {"cases_per_prompt": 7}}
    assert generation_settings_from_config(config).max_cases_per_unit == 7


def test_the_job_budget_may_come_from_the_cloud_section():
    config = {"cloud": {"job_timeout_s": 123}}
    assert generation_settings_from_config(config).job_timeout_s == 123


def test_nonsense_limits_are_clamped():
    config = {
        "generation": {
            "max_cases_per_unit": 0,
            "group_size": -5,
            "max_repair_attempts": -1,
        }
    }
    settings = generation_settings_from_config(config)
    assert settings.max_cases_per_unit == 1
    assert settings.group_size == 1
    assert settings.max_repair_attempts == 0


def test_the_shipped_configuration_resolves_to_its_documented_limits():
    settings = generation_settings_from_config(load_config(ROOT / "config.yml"))
    assert settings.max_cases_per_unit == 5
    assert settings.max_repair_attempts == 2
    assert settings.group_size == 1
