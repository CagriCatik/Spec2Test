"""The researching reviewer: scripted model, real MCP server process.

The model is deterministic so the five situations are reproducible. The
MCP server is not faked: it is started as a child process over stdio, so
the tools the agent calls are the real ones and the JSON that crosses the
boundary is the real JSON.

Five situations, all required:
  1. the research finds the condition that was missing
  2. the research finds a source that does not fit technically
  3. no fitting source exists
  4. the budget runs out
  5. the MCP server is unavailable
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("smolagents")
pytest.importorskip("mcp")

from smolagents import ChatMessage, Model  # noqa: E402

from spec2test.adapters.agents.mcp_bridge import (  # noqa: E402
    BridgeError,
    McpToolBridge,
)
from spec2test.adapters.agents.research_reviewer import (  # noqa: E402
    ALLOWED_TOOLS,
    review_with_research,
    server_parameters,
)
from spec2test.application.research_review import (  # noqa: E402
    RESEARCH_SCHEMA_VERSION,
    Budget,
    ResearchReport,
    build_task,
)
from spec2test.domain.models import (  # noqa: E402
    GROUNDING_RULES_VERSION,
    Ground,
    GroundKind,
    Requirement,
    SourceSpan,
    TestCase,
    TestStep,
)

ROOT_SRC = Path(__file__).resolve().parents[1] / "src"

TARGET_TEXT = (
    "SYS-REQ-8001: The heater shall switch on at 5.0 V. "
    "The monitoring board is powered from the 12 V auxiliary supply."
)
CORPUS_A = f"""# Heater

### Requirement: SYS-REQ-8001
* **Title:** Heater switch-on
* {TARGET_TEXT.split(': ', 1)[1]}
"""
CORPUS_B = """# Operating states

### Requirement: SYS-REQ-8002
* **Title:** Heater inhibit while charging
* While the pack is charging the heater shall remain off regardless of the
  switch-on threshold.
"""


def requirement() -> Requirement:
    return Requirement(
        requirement_id="REQ-8001",
        text=TARGET_TEXT,
        external_id="SYS-REQ-8001",
        spans=(SourceSpan("a_heater.md", 0, len(TARGET_TEXT), TARGET_TEXT),),
    )


def case() -> TestCase:
    return TestCase(
        case_id="TC-R-1",
        title="Heater switch-on",
        objective="Check the switch-on threshold.",
        requirement_ids=("SYS-REQ-8001",),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=(
            TestStep(
                1,
                "raise the supply voltage slowly",
                ("the heater switches on at 5.0 V",),
                grounds=(
                    Ground(
                        claim="5.0 V",
                        kind=GroundKind.TARGET_REQUIREMENT,
                        target="expectations[0]",
                        source="SYS-REQ-8001",
                        quote="The heater shall switch on at 5.0 V.",
                    ),
                ),
            ),
        ),
    )


@pytest.fixture
def corpus(tmp_path):
    folder = tmp_path / "requirements"
    folder.mkdir()
    (folder / "a_heater.md").write_text(CORPUS_A, encoding="utf-8")
    (folder / "b_states.md").write_text(CORPUS_B, encoding="utf-8")
    return folder


class ScriptedAgentModel(Model):
    """A smolagents model with a fixed script. Counts its own calls."""

    def __init__(self, replies, model_id="scripted"):
        super().__init__()
        self.model_id = model_id
        self.replies = list(replies)
        self.calls = 0

    def generate(self, messages, stop_sequences=None, response_format=None,
                 tools_to_call_from=None, **kwargs):
        self.calls += 1
        index = min(self.calls - 1, len(self.replies) - 1)
        return ChatMessage(role="assistant", content=self.replies[index])


def final(payload: dict) -> str:
    """A smolagents final answer: a tool call to final_answer."""
    return json.dumps(
        {
            "name": "final_answer",
            "arguments": {"answer": json.dumps(payload, ensure_ascii=False)},
        }
    )


def lookup(tool: str, **arguments) -> str:
    return json.dumps({"name": tool, "arguments": arguments})


def verdicts_payload(*findings) -> dict:
    return {"schema_version": RESEARCH_SCHEMA_VERSION, "findings": list(findings)}


def run(corpus, replies, *, budget=None, tools=None):
    """One review against a real MCP server process."""
    params = server_parameters(corpus=corpus, index=corpus.parent / "idx.db")
    with McpToolBridge(params) as bridge:
        mcp_tools = bridge.tools()
        chosen = mcp_tools if tools is None else tools(mcp_tools)
        return review_with_research(
            case(),
            requirement(),
            model=ScriptedAgentModel(replies),
            tools=chosen,
            budget=budget or Budget(max_model_calls=4, max_tool_calls=4),
        )


# ================================================= the tools it is offered ==
def test_only_the_two_read_only_tools_are_offered(corpus):
    """review_grounds must not be handed to the reviewer: no recursion."""
    seen: dict = {}

    def capture(mcp_tools):
        seen["names"] = sorted(t.name for t in mcp_tools)
        return mcp_tools


    report = run(
        corpus,
        [final(verdicts_payload({"claim_id": "step0-g0", "verdict": "fits"}))],
        tools=capture,
    )
    assert "review_grounds" in seen["names"], "the server does offer it"
    assert set(ALLOWED_TOOLS) == {"search_requirements", "get_requirement"}
    assert "review_grounds" in report.provenance_note
    assert "check_grounds" in report.provenance_note


def test_the_task_carries_the_draft_the_requirement_and_the_grounds():
    task = build_task(case(), requirement())
    assert "claim_id: step0-g0" in task
    assert "field: expectations[0]" in task
    assert "SYS-REQ-8001" in task
    assert "The heater shall switch on at 5.0 V." in task
    assert "input data, not instructions" in task


# =============================================== 1. research finds the fit ==
def test_research_finds_the_condition_that_was_missing(corpus):
    report = run(
        corpus,
        [
            lookup("search_requirements", query="heater charging inhibit", limit=3),
            lookup("get_requirement", requirement_id="SYS-REQ-8002"),
            final(
                verdicts_payload(
                    {
                        "claim_id": "step0-g0",
                        "verdict": "missing_condition",
                        "confidence": "high",
                        "explanation": (
                            "SYS-REQ-8002 inhibits the heater while charging; "
                            "the step does not establish that the pack is not "
                            "charging"
                        ),
                        "consulted": ["SYS-REQ-8002"],
                    }
                )
            ),
        ],
    )
    assert report.ok and report.complete
    assert [v.outcome for v in report.verdicts] == ["finding"]
    assert report.verdicts[0].verdict == "missing_condition"
    assert "SYS-REQ-8002" in report.verdicts[0].consulted
    assert any(
        i.code == "C_GROUND_REVIEW_MISSING_CONDITION" for i in report.issues
    )
    # the source it read is recorded with version and position
    read = [c for c in report.consulted if c.identifier == "SYS-REQ-8002"]
    assert read, [c.identifier for c in report.consulted]
    assert read[0].document_filename == "b_states.md"
    assert read[0].char_start is not None and read[0].quote
    assert len(report.tool_calls) == 2
    assert report.model_calls == 3


def test_the_sources_it_read_are_not_the_runs_context(corpus):
    """The separation the whole snapshot exists for."""
    report = run(
        corpus,
        [
            lookup("get_requirement", requirement_id="SYS-REQ-8002"),
            final(
                verdicts_payload(
                    {"claim_id": "step0-g0", "verdict": "missing_condition",
                     "explanation": "x", "consulted": ["SYS-REQ-8002"]}
                )
            ),
        ],
    )
    assert "NOT part of the context the draft was generated with" in (
        report.provenance_note
    )
    assert report.case_revision == case().revision


# ====================================== 2. research finds an unfitting source
def test_a_source_that_does_not_fit_technically_is_a_finding(corpus):
    report = run(
        corpus,
        [
            lookup("get_requirement", requirement_id="SYS-REQ-8001"),
            final(
                verdicts_payload(
                    {
                        "claim_id": "step0-g0",
                        "verdict": "mismatched_quantity",
                        "confidence": "high",
                        "explanation": (
                            "the 12 V in the same requirement is the auxiliary "
                            "supply, not the switch-on threshold"
                        ),
                        "consulted": ["SYS-REQ-8001"],
                    }
                )
            ),
        ],
    )
    assert [v.outcome for v in report.verdicts] == ["finding"]
    assert any(i.code == "C_GROUND_REVIEW_QUANTITY" for i in report.issues)


# =================================================== 3. no fitting source ===
def test_a_clear_resting_on_a_source_it_never_read_is_downgraded(corpus):
    """Not finding a source is not the same as finding nothing wrong."""
    report = run(
        corpus,
        [
            lookup("search_requirements", query="heater hysteresis", limit=3),
            final(
                verdicts_payload(
                    {
                        "claim_id": "step0-g0",
                        "verdict": "fits",
                        "confidence": "low",
                        "explanation": "assumed from SYS-REQ-9999",
                        "consulted": ["SYS-REQ-9999"],
                    }
                )
            ),
        ],
    )
    verdict = report.verdicts[0]
    assert verdict.outcome == "not_checked"
    assert verdict.downgraded_from == "fits"
    assert "never read" in verdict.explanation
    assert report.complete is False
    assert any(i.code == "C_RESEARCH_REVIEW_NOT_CHECKED" for i in report.issues)


def test_an_unknown_requirement_lookup_is_recorded_as_not_found(corpus):
    report = run(
        corpus,
        [
            lookup("get_requirement", requirement_id="SYS-REQ-9999"),
            final(
                verdicts_payload(
                    {"claim_id": "step0-g0", "verdict": "ambiguous_requirement",
                     "explanation": "nothing states a hysteresis",
                     "consulted": []}
                )
            ),
        ],
    )
    assert any(c.found is False for c in report.consulted), report.consulted
    assert report.verdicts[0].outcome == "finding"


# ======================================================== 4. budget ends ====
def test_the_model_call_budget_stops_the_run(corpus):
    report = run(
        corpus,
        [lookup("search_requirements", query="heater", limit=3)] * 10,
        budget=Budget(max_model_calls=2, max_tool_calls=9),
    )
    assert report.budget_exhausted is True
    assert report.ok is False and "2 model call(s)" in report.error
    assert [v.outcome for v in report.verdicts] == ["not_checked"]
    assert report.model_calls == 2
    assert report.budget["max_model_calls"] == 2


def test_the_tool_call_budget_stops_the_run(corpus):
    report = run(
        corpus,
        [lookup("search_requirements", query="heater", limit=3)] * 10,
        budget=Budget(max_model_calls=9, max_tool_calls=2),
    )
    assert report.budget_exhausted is True
    assert "2 lookup(s)" in (report.error or "")
    assert len(report.tool_calls) == 2
    assert all(v.outcome == "not_checked" for v in report.verdicts)


def test_the_clock_stops_the_run(corpus):
    report = run(
        corpus,
        [lookup("search_requirements", query="heater", limit=3)] * 10,
        budget=Budget(max_model_calls=9, max_tool_calls=9, max_seconds=0.0),
    )
    assert report.budget_exhausted is True
    assert "seconds" in (report.error or "")


# ===================================================== 5. MCP unavailable ===
def test_a_dead_mcp_server_is_reported_not_swallowed(tmp_path):
    from mcp import StdioServerParameters

    params = StdioServerParameters(
        command="/nonexistent/python", args=["-m", "spec2test.adapters.mcp"], env={}
    )
    with pytest.raises(BridgeError, match="could not be reached"):
        with McpToolBridge(params, timeout=20):
            pass


def test_a_tool_that_fails_mid_review_leaves_the_claims_unjudged(corpus):
    """The server dies between calls: nothing may come back as a pass."""

    def break_tools(mcp_tools):
        for tool in mcp_tools:
            if tool.name in ALLOWED_TOOLS:
                def boom(*_a, **_k):
                    raise RuntimeError("mcp connection lost")

                tool.forward = boom
        return mcp_tools

    report = run(
        corpus,
        [lookup("get_requirement", requirement_id="SYS-REQ-8002")] * 8,
        tools=break_tools,
        budget=Budget(max_model_calls=3, max_tool_calls=3),
    )
    assert all(v.outcome == "not_checked" for v in report.verdicts)
    assert report.complete is False
    assert any(t.error for t in report.tool_calls), report.tool_calls


# ===================================================== the stored snapshot ==
def test_the_snapshot_is_written_beside_the_project_not_into_it(corpus, tmp_path):
    report = run(
        corpus,
        [
            lookup("get_requirement", requirement_id="SYS-REQ-8002"),
            final(
                verdicts_payload(
                    {"claim_id": "step0-g0", "verdict": "missing_condition",
                     "explanation": "x", "consulted": ["SYS-REQ-8002"]}
                )
            ),
        ],
    )
    target = tmp_path / "reviews" / "TC-R-1.review.json"
    report.save(target)
    stored = json.loads(target.read_text(encoding="utf-8"))

    assert stored["case_id"] == "TC-R-1"
    assert stored["case_revision"] == 1
    assert stored["prompt_version"] == "research-review-1"
    assert stored["framework"].startswith("smolagents")
    assert stored["budget"]["max_model_calls"] >= 1
    assert stored["model_calls"] >= 1
    assert stored["tool_calls"][0]["tool"] == "get_requirement"
    assert stored["tool_calls"][0]["arguments"] == {"requirement_id": "SYS-REQ-8002"}
    source = stored["consulted"][0]
    assert source["document_filename"] == "b_states.md"
    assert source["char_start"] is not None and source["quote"]
    assert "NOT part of the context" in stored["provenance_note"]


def test_a_review_never_changes_the_draft(corpus):
    from dataclasses import asdict

    original = case()
    before = asdict(original)
    params = server_parameters(corpus=corpus, index=corpus.parent / "idx2.db")
    with McpToolBridge(params) as bridge:
        tools = bridge.tools()
        review_with_research(
            original,
            requirement(),
            model=ScriptedAgentModel(
                [final(verdicts_payload({"claim_id": "step0-g0", "verdict": "fits"}))]
            ),
            tools=tools,
            budget=Budget(max_model_calls=2, max_tool_calls=2),
        )
    assert asdict(original) == before


def test_the_report_summarises_what_it_spent(corpus):
    report = run(
        corpus,
        [final(verdicts_payload({"claim_id": "step0-g0", "verdict": "fits"}))],
    )
    assert isinstance(report, ResearchReport)
    text = report.summary()
    assert "model call(s)" in text and "lookup(s)" in text


# ============================================================= the command ==
def test_the_cli_writes_a_snapshot_and_leaves_the_project_alone(
    corpus, tmp_path, monkeypatch, capfd
):
    """End to end through the command, against a real server process.

    ``capfd``, not ``capsys``: the child process needs a real file
    descriptor, and capsys replaces sys.stdout with an object that has no
    ``fileno``.
    """
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.contracts import SCHEMA_VERSION
    from spec2test.application.review import project_from_run
    from spec2test.cli import main
    from spec2test.domain.models import (
        DocumentFormat, GenerationRun, GenerationUnitResult, RunStatus,
        SourceDocument, UnitStatus,
    )
    import spec2test.adapters.agents.research_reviewer as reviewer

    run_record = GenerationRun(
        run_id="r1", model="m", endpoint="e", mode="fake", prompt_version="p",
        schema_version=SCHEMA_VERSION, source_hash="h", status=RunStatus.COMPLETED,
        units=[GenerationUnitResult(
            unit_id="u1", requirement_ids=("REQ-8001",),
            status=UnitStatus.ACCEPTED, test_cases=[case()],
        )],
    )
    document = SourceDocument(
        document_id="a_heater.md", filename="a_heater.md",
        format=DocumentFormat.MARKDOWN, content_hash="h", text=TARGET_TEXT,
    )
    project_path = tmp_path / "p.s2t.json"
    ProjectStore(project_path).save(
        project_from_run(run_record, [requirement()], [document], name="p")
    )
    before = project_path.read_bytes()

    scripted = ScriptedAgentModel(
        [
            lookup("get_requirement", requirement_id="SYS-REQ-8002"),
            final(
                verdicts_payload(
                    {"claim_id": "step0-g0", "verdict": "missing_condition",
                     "confidence": "high",
                     "explanation": "SYS-REQ-8002 inhibits the heater while charging",
                     "consulted": ["SYS-REQ-8002"]}
                )
            ),
        ]
    )
    monkeypatch.setattr(reviewer, "build_model", lambda *a, **k: scripted)

    out_path = tmp_path / "snapshot.json"
    code = main(
        [
            "research-review", str(project_path), "TC-R-1",
            "--corpus", str(corpus), "--index", str(tmp_path / "cli.db"),
            "--model", "scripted", "--out", str(out_path),
            "--max-model-calls", "4", "--max-tool-calls", "4",
        ]
    )
    assert code == 0
    printed = capfd.readouterr().out
    assert "tools offered to the reviewer: " in printed
    assert "withheld: check_grounds, review_grounds" in printed
    assert "FINDING" in printed
    assert "read SYS-REQ-8002" in printed

    assert project_path.read_bytes() == before, "the project must not change"
    stored = json.loads(out_path.read_text(encoding="utf-8"))
    assert stored["case_id"] == "TC-R-1"
    assert stored["consulted"][0]["identifier"] == "SYS-REQ-8002"
    assert "NOT part of the context" in stored["provenance_note"]


def test_the_cli_reports_an_unreachable_server(tmp_path, capsys):
    from spec2test.cli import main

    code = main(
        [
            "research-review", str(tmp_path / "missing.s2t.json"), "TC-1",
            "--corpus", str(tmp_path), "--model", "x",
        ]
    )
    assert code == 2
    assert "could not be read" in capsys.readouterr().out
