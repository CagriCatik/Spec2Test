"""AP 0 regression tests: pin down the defects of the baseline (59eabc7).

Every test in this module asserts the *required* behaviour. Against the
baseline implementation they fail; they are the acceptance net for AP 1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import core

FIXTURES = Path(__file__).parent / "fixtures"


def _pages() -> list[str]:
    return [
        (FIXTURES / "pages" / f"page_{i}.txt").read_text(encoding="utf-8")
        for i in range(1, 6)
    ]


# --------------------------------------------------------------------------
# Defect 1: pages are silently dropped by the block builder
# --------------------------------------------------------------------------
def test_five_pages_block_size_three_keeps_every_page():
    """The concrete example from AP 0: 5 pages, combine=3 loses pages 4 and 5."""
    pages = _pages()
    chunks = core.chunk_document(pages, combine=3, overlap=0)
    joined = "\n".join(chunks)
    missing = [i for i in range(1, 6) if f"PAGE-MARKER-{i}" not in joined]
    assert missing == [], f"pages lost by chunking: {missing}"


@pytest.mark.parametrize(
    "page_count,combine,overlap",
    [
        (5, 3, 0),
        (5, 3, 1),
        (7, 3, 1),
        (7, 4, 2),
        (10, 3, 0),
        (10, 4, 1),
        (1, 3, 0),
        (4, 2, 1),
    ],
)
def test_every_page_appears_in_at_least_one_block(page_count, combine, overlap):
    pages = [f"PAGE-MARKER-{i}" for i in range(1, page_count + 1)]
    chunks = core.chunk_document(pages, combine=combine, overlap=overlap)
    joined = "\n".join(chunks)
    missing = [i for i in range(1, page_count + 1) if f"PAGE-MARKER-{i}" not in joined]
    assert missing == [], (
        f"combine={combine} overlap={overlap}: pages {missing} never reach the model"
    )


def test_overlap_does_not_emit_fully_covered_remainder_block():
    """A trailing block that adds no new page must not be emitted."""
    pages = [f"PAGE-MARKER-{i}" for i in range(1, 6)]
    chunks = core.chunk_document(pages, combine=2, overlap=1)
    assert len(chunks) == 4
    assert "PAGE-MARKER-5" in chunks[-1]


# --------------------------------------------------------------------------
# Defect 2: the parser turns unusable answers into empty test cases
# --------------------------------------------------------------------------
def test_mixed_json_array_does_not_produce_empty_cases():
    raw = json.dumps(
        [
            {
                "testcase_name": "TC_1",
                "objective": "check",
                "linked_requirements": ["RAS-SW-REQ-0001"],
                "preconditions": ["ready"],
                "actions": ["do"],
                "expected_results": ["ok"],
                "postconditions": ["clean"],
            },
            "this is not a test case",
            42,
            None,
        ]
    )
    cases = core.parse_cases(raw)
    assert all(any(str(v).strip() for v in c.values()) for c in cases), (
        f"parser produced empty placeholder cases: {cases}"
    )
    assert len(cases) == 1


def test_invalid_json_is_distinguishable_from_valid_answer_without_cases():
    broken = core.parse_model_response("not json at all")
    empty = core.parse_model_response("")
    no_cases = core.parse_model_response("[]")

    assert broken.outcome.name == "INVALID_JSON"
    assert empty.outcome.name == "EMPTY"
    assert no_cases.outcome.name == "NO_TEST_CASES"
    assert broken.issues and empty.issues and no_cases.issues


def test_wrong_field_types_are_reported_not_coerced():
    raw = json.dumps(
        [
            {
                "testcase_name": "TC_1",
                "objective": "check",
                "linked_requirements": 17,
                "preconditions": ["ready"],
                "actions": {"step": "do"},
                "expected_results": ["ok"],
                "postconditions": ["clean"],
            }
        ]
    )
    result = core.parse_model_response(raw)
    codes = {issue.code for issue in result.issues}
    assert codes, "invalid field types were accepted silently"


# --------------------------------------------------------------------------
# Defect 3: a network error silently starts the local Ollama CLI
# --------------------------------------------------------------------------
def test_http_failure_does_not_silently_invoke_the_local_cli(monkeypatch):
    called = {"cli": False}

    def fake_cli(self, prompt):  # pragma: no cover - must never run
        called["cli"] = True
        return "[]"

    def boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(core.AgentLocal, "_cli_generate", fake_cli, raising=False)
    monkeypatch.setattr(core.requests, "post", boom)

    agent = core.AgentLocal(model="dummy", ollama_url="http://localhost:11434")
    with pytest.raises(Exception):
        agent.generate("hello")
    assert called["cli"] is False, "network error triggered the local CLI fallback"
