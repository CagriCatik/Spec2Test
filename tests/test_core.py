from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import core

from core import ( 
    AgentLocal,
    KeywordRetriever,
    PromptCache,
    chunk_document,
    parse_cases,
    summarise_cases,
    strip_code_fences,
)


def test_strip_code_fences_removes_wrappers():
    txt = "```json\n[{\"actions\": \"do\"}]\n```"
    assert strip_code_fences(txt) == '[{"actions": "do"}]'


def test_parse_cases_returns_empty_for_invalid_json():
    assert parse_cases("not-json") == []
    assert parse_cases(json.dumps({"foo": "bar"})) == []


def test_parse_cases_reports_why_an_answer_was_unusable():
    """AP 1: the three unusable outcomes are distinguishable."""
    assert core.parse_legacy_cases("").outcome.name == "EMPTY"
    assert core.parse_legacy_cases("not-json").outcome.name == "INVALID_JSON"
    assert core.parse_legacy_cases("[]").outcome.name == "NO_TEST_CASES"


def test_chunk_document_with_overlap():
    pages = [f"page-{i}" for i in range(5)]
    chunks = chunk_document(pages, combine=2, overlap=1)
    assert chunks == ["page-0\n\npage-1", "page-1\n\npage-2", "page-2\n\npage-3", "page-3\n\npage-4"]


def test_keyword_retriever_returns_related_segments():
    pytest.importorskip("sklearn")
    texts = [
        "Requirements for braking system include fail-safe",
        "Infotainment must reboot in 2 seconds",
        "Braking tests shall cover wet and dry scenarios",
    ]
    retriever = KeywordRetriever(texts)
    related = retriever.top_k("braking performance", k=2)
    assert len(related) == 2
    assert "braking" in " ".join(related)


def test_generate_test_cases_reports_progress(monkeypatch):
    # AP 1/AP 2: block building moved into application.chunking.build_blocks, so
    # patching core.chunk_document no longer influences the run. With three
    # pages and combine=1 there are three blocks and therefore three calls.
    monkeypatch.setattr(core, "pdf_to_text_pages", lambda _path: ["a", "b", "c"])

    class DummyAgent:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt, system_prompt=None):  # noqa: D401 - simple stub
            self.calls += 1
            return json.dumps(
                [
                    {
                        "testcase_name": f"Case {self.calls}",
                        "objective": "Ensure quality",
                        "linked_requirements": ["REQ-1"],
                        "preconditions": ["Env ready"],
                        "actions": ["Do something"],
                        "expected_results": ["It works"],
                        "postconditions": ["Cleanup"],
                    }
                ]
            )

    agent = DummyAgent()
    updates: list[int] = []

    cases = core.generate_test_cases(
        pdf_path="dummy.pdf",
        agent=agent,
        n_cases=1,
        combine=1,
        progress_cb=updates.append,
        show_progress=False,
    )

    assert updates  # at least one update emitted
    assert updates[-1] == 100
    assert len(cases) == 3
    assert agent.calls == 3


def test_agentlocal_gpu_switches_between_cpu_and_auto():
    auto = AgentLocal()
    assert auto.opt["num_gpu"] == -1

    cpu_only = AgentLocal(use_gpu=False)
    assert cpu_only.opt["num_gpu"] == 0

    explicit = AgentLocal(use_gpu=True, num_gpu=2, main_gpu=1, gpu_layers=20, num_thread=8)
    assert explicit.opt["num_gpu"] == 2
    assert explicit.opt["main_gpu"] == 1
    assert explicit.opt["gpu_layers"] == 20
    assert explicit.opt["num_thread"] == 8


def test_prompt_cache_short_circuits_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "pdf_to_text_pages", lambda _path: ["a", "b"])

    class DummyAgent:
        def __init__(self):
            self.calls = 0
            self.model = "dummy"
            self.system_prompt = "sys"
            self.opt = {"temperature": 0.0}

        def generate(self, prompt, system_prompt=None):
            self.calls += 1
            payload = [
                {
                    "testcase_name": f"Case {self.calls}",
                    "objective": "Ensure quality",
                    "linked_requirements": ["REQ-1"],
                    "preconditions": ["Env ready"],
                    "actions": ["Do something"],
                    "expected_results": ["It works"],
                    "postconditions": ["Cleanup"],
                }
            ]
            return json.dumps(payload)

    cache = PromptCache(tmp_path / "cache")

    first_agent = DummyAgent()
    first = core.generate_test_cases(
        pdf_path="dummy.pdf",
        agent=first_agent,
        n_cases=1,
        combine=1,
        cache=cache,
    )
    assert first_agent.calls == 2

    second_agent = DummyAgent()
    second = core.generate_test_cases(
        pdf_path="dummy.pdf",
        agent=second_agent,
        n_cases=1,
        combine=1,
        cache=cache,
    )
    assert second_agent.calls == 0
    assert first == second


def test_summarise_cases_reports_duplicates_and_counts():
    cases = [
        {
            "testcase_name": "Login",
            "objective": "Test login",
            "linked_requirements": ["REQ-1"],
            "preconditions": ["User exists"],
            "actions": ["Open app", "Enter password"],
            "expected_results": ["Success"],
            "postconditions": ["Session active"],
        },
        {
            "testcase_name": "Login",
            "objective": "Repeat",
            "linked_requirements": "REQ-1, REQ-2",
            "preconditions": "",
            "actions": ["Retry"],
            "expected_results": ["Success"],
            "postconditions": ["Session active"],
        },
    ]

    summary = summarise_cases(cases)
    assert summary["total_cases"] == 2
    assert summary["complete_cases"] == 2
    assert summary["unique_requirements"] == 2
    assert "Login" in summary["duplicate_names"]
    assert summary["missing_fields"]["preconditions"] == 1
