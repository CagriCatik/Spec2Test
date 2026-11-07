"""The MCP adapter, driven by a real MCP client over the real protocol.

Not a mock: fastmcp's in-memory client speaks the protocol against the
server object, so the tool list, the schemas and the JSON that crosses the
boundary are the ones a foreign agent would see.

Three properties are asserted throughout: nothing is written, no model is
called except by review_grounds, and the product's own findings come
through unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402

#: the src directory, so a child process can import the package
ROOT_SRC = Path(__file__).resolve().parents[1] / "src"

from spec2test.adapters.mcp.server import (  # noqa: E402
    API_VERSION,
    ConfigurationError,
    Workspace,
    build_server,
    tool_check_grounds,
    tool_get_requirement,
    tool_review_grounds,
    tool_search_requirements,
)

SPEC_A = """# Pyrofuse

### Requirement: SYS-REQ-7001
* **Title:** Ultra-fast trip
* The comparator is referenced against 250 mV, which corresponds to 2500 A.
* The latch shall assert within 2.0 us. The shunt is 100 uOhm.
"""

SPEC_B = """# Signals

## Signals
`HW_Trip_Latch_Output` is a discrete output driving the firing gate.
"""


@pytest.fixture
def workspace(tmp_path):
    corpus = tmp_path / "requirements"
    corpus.mkdir()
    (corpus / "a_pyrofuse.md").write_text(SPEC_A, encoding="utf-8")
    (corpus / "b_signals.md").write_text(SPEC_B, encoding="utf-8")
    return Workspace(
        project=None,
        corpus=corpus,
        index=tmp_path / "index.db",
        review_model=None,
        review_mode="fake",
    )


@pytest.fixture
def project_workspace(workspace, tmp_path):
    """A stored project with two identical numbers in different fields."""
    from dataclasses import replace

    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.contracts import SCHEMA_VERSION
    from spec2test.application.ingest import import_documents
    from spec2test.adapters.documents import DEFAULT_IMPORTERS
    from spec2test.application.review import project_from_run
    from spec2test.domain.models import (
        GROUNDING_RULES_VERSION,
        GenerationRun,
        GenerationUnitResult,
        Ground,
        GroundKind,
        RunStatus,
        TestCase,
        TestStep,
        UnitStatus,
    )

    results = import_documents(sorted(workspace.corpus.glob("*.md")), DEFAULT_IMPORTERS)
    requirements = [r for x in results for r in x.requirements]
    documents = [x.document for x in results]
    target = next(r for r in requirements if r.external_id == "SYS-REQ-7001")

    step = TestStep(
        1,
        "sweep the shunt voltage",
        (
            "the comparator trips at 250 mV",
            "the comparator releases at 250 mV",
        ),
        grounds=(
            Ground(
                claim="250 mV", kind=GroundKind.TARGET_REQUIREMENT,
                target="expectations[0]", source="SYS-REQ-7001",
                quote="The comparator is referenced against 250 mV",
            ),
            Ground(
                claim="250 mV", kind=GroundKind.TARGET_REQUIREMENT,
                target="expectations[1]", source="SYS-REQ-7001",
                quote="The comparator is referenced against 250 mV",
            ),
        ),
    )
    case = TestCase(
        case_id="TC-MCP-1", title="Trip threshold", objective="Check the threshold.",
        requirement_ids=(target.requirement_id,),
        grounding_rules=GROUNDING_RULES_VERSION,
        steps=(step,),
    )
    run = GenerationRun(
        run_id="r1", model="m", endpoint="e", mode="fake", prompt_version="p",
        schema_version=SCHEMA_VERSION, source_hash="h", status=RunStatus.COMPLETED,
        units=[GenerationUnitResult(
            unit_id="u1", requirement_ids=(target.requirement_id,),
            status=UnitStatus.ACCEPTED, test_cases=[case],
        )],
    )
    path = tmp_path / "project.s2t.json"
    ProjectStore(path).save(project_from_run(run, requirements, documents, name="mcp"))
    return replace(workspace, project=path)


async def call(server, name, **arguments) -> dict:
    """One tool call over the real protocol."""
    async with Client(server) as client:
        result = await client.call_tool(name, arguments)
    payload = result.structured_content or {}
    return payload.get("result", payload)


# ======================================================= the protocol itself
@pytest.mark.asyncio
async def test_the_server_offers_exactly_the_four_read_only_tools(workspace):
    async with Client(build_server(workspace)) as client:
        tools = {t.name for t in await client.list_tools()}
    assert tools == {
        "search_requirements",
        "get_requirement",
        "check_grounds",
        "review_grounds",
    }


@pytest.mark.asyncio
async def test_every_tool_describes_what_it_costs(workspace):
    async with Client(build_server(workspace)) as client:
        tools = {t.name: (t.description or "") for t in await client.list_tools()}
    for name in ("search_requirements", "get_requirement", "check_grounds"):
        assert "ffline" in tools[name] or "no model" in tools[name], name
    assert "calls a model" in tools["review_grounds"]
    assert "approved" in tools["review_grounds"] or "written" in tools["review_grounds"]


# ============================================================ search + get ==
@pytest.mark.asyncio
async def test_search_returns_id_version_and_position(workspace):
    answer = await call(build_server(workspace), "search_requirements",
                        query="comparator reference voltage")
    assert answer["api_version"] == API_VERSION
    assert answer["model_calls"] == 0
    assert answer["hits"], answer
    hit = answer["hits"][0]
    for key in ("passage_id", "kind", "text", "source"):
        assert key in hit
    source = hit["source"]
    assert source["document_id"] and source["document_filename"]
    assert source["char_start"] < source["char_end"]


@pytest.mark.asyncio
async def test_search_can_be_narrowed_to_one_kind(workspace):
    answer = await call(build_server(workspace), "search_requirements",
                        query="HW_Trip_Latch_Output", kind="signal")
    assert all(h["kind"] == "signal" for h in answer["hits"]), answer["hits"]


@pytest.mark.asyncio
async def test_get_requirement_resolves_an_external_id(workspace):
    answer = await call(build_server(workspace), "get_requirement",
                        requirement_id="SYS-REQ-7001")
    assert answer["found"] and answer["model_calls"] == 0
    assert "250 mV" in answer["text"]
    assert answer["spans"] and answer["spans"][0]["document_filename"]


@pytest.mark.asyncio
async def test_an_unknown_requirement_is_reported_not_invented(workspace):
    answer = await call(build_server(workspace), "get_requirement",
                        requirement_id="SYS-REQ-9999")
    assert answer["found"] is False
    assert "not in the configured sources" in answer["message"]


# ================================================= the deterministic check ==
@pytest.mark.asyncio
async def test_check_grounds_reports_the_products_own_findings(project_workspace):
    answer = await call(build_server(project_workspace), "check_grounds")
    assert answer["model_calls"] == 0
    case = answer["cases"][0]
    assert case["case_id"] == "TC-MCP-1"
    assert case["grounding_rules"] == "1" and case["legacy"] is False
    assert len(case["grounds"]) == 2
    assert {g["target"] for g in case["grounds"]} == {
        "expectations[0]", "expectations[1]"
    }


@pytest.mark.asyncio
async def test_check_grounds_can_name_one_draft(project_workspace):
    answer = await call(build_server(project_workspace), "check_grounds",
                        case_id="TC-MCP-1")
    assert len(answer["cases"]) == 1
    missing = await call(build_server(project_workspace), "check_grounds",
                         case_id="TC-nope")
    assert missing["cases"] == [] and "not a draft" in missing["message"]


# ====================================================== the review, asked ===
@pytest.mark.asyncio
async def test_review_grounds_reports_its_model_calls_and_what_it_missed(
    project_workspace, monkeypatch
):
    """Two identical values, two claim_ids, one judged and one not."""
    import json as _json

    from spec2test.adapters.fake.model import ScriptedModel
    from spec2test.application.ground_review import REVIEW_SCHEMA_VERSION

    reply = _json.dumps(
        {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "findings": [
                {"claim_id": "step0-g0", "claim": "250 mV",
                 "verdict": "mismatched_condition", "confidence": "high",
                 "explanation": "the quote is the trip reference, not a release point"}
            ],
        }
    )
    monkeypatch.setattr(
        "spec2test.bootstrap.build_model", lambda *a, **k: ScriptedModel([reply])
    )
    answer = await call(build_server(project_workspace), "review_grounds",
                        case_id="TC-MCP-1")

    assert answer["model_calls"] == 1
    assert answer["ok"] is True
    assert answer["complete"] is False, "one claim came back unjudged"
    assert answer["not_checked"] == ["step0-g1"]
    outcomes = {v["claim_id"]: v["outcome"] for v in answer["verdicts"]}
    assert outcomes == {"step0-g0": "finding", "step0-g1": "not_checked"}
    assert any(f["code"] == "C_GROUND_REVIEW_CONDITION" for f in answer["findings"])
    assert any(
        f["code"] == "C_GROUND_REVIEW_NOT_CHECKED" for f in answer["findings"]
    )
    assert "approves nothing" in answer["note"]


@pytest.mark.asyncio
async def test_a_failing_review_is_not_a_pass(project_workspace, monkeypatch):
    from spec2test.adapters.fake.model import ScriptedModel

    monkeypatch.setattr(
        "spec2test.bootstrap.build_model", lambda *a, **k: ScriptedModel(["sorry"])
    )
    answer = await call(build_server(project_workspace), "review_grounds",
                        case_id="TC-MCP-1")
    assert answer["ok"] is False and answer["complete"] is False
    assert sorted(answer["not_checked"]) == ["step0-g0", "step0-g1"]


# ====================================================== nothing is written ==
@pytest.mark.asyncio
async def test_no_tool_changes_the_project_file(project_workspace, monkeypatch):
    from spec2test.adapters.fake.model import ScriptedModel

    monkeypatch.setattr(
        "spec2test.bootstrap.build_model", lambda *a, **k: ScriptedModel(["{}"])
    )
    before = project_workspace.project.read_bytes()
    server = build_server(project_workspace)
    await call(server, "search_requirements", query="comparator")
    await call(server, "get_requirement", requirement_id="SYS-REQ-7001")
    await call(server, "check_grounds")
    await call(server, "review_grounds", case_id="TC-MCP-1")
    assert project_workspace.project.read_bytes() == before


@pytest.mark.asyncio
async def test_only_the_review_tool_touches_a_model(project_workspace, monkeypatch):
    calls: list[str] = []

    def refuse(*_a, **_k):
        calls.append("built")
        from spec2test.adapters.fake.model import ScriptedModel

        return ScriptedModel(["{}"])

    monkeypatch.setattr("spec2test.bootstrap.build_model", refuse)
    server = build_server(project_workspace)
    await call(server, "search_requirements", query="comparator")
    await call(server, "get_requirement", requirement_id="SYS-REQ-7001")
    await call(server, "check_grounds")
    assert calls == [], "a read-only tool built a model"
    await call(server, "review_grounds", case_id="TC-MCP-1")
    assert calls == ["built"]


# ======================================================== configuration ====
def test_a_missing_source_directory_says_so(tmp_path):
    space = Workspace(project=None, corpus=None, index=tmp_path / "i.db",
                      review_model=None, review_mode="fake")
    with pytest.raises(ConfigurationError, match="SPEC2TEST_CORPUS"):
        tool_search_requirements(space, "anything")


def test_a_missing_project_says_so(workspace):
    with pytest.raises(ConfigurationError, match="SPEC2TEST_PROJECT"):
        tool_check_grounds(workspace)


def test_a_review_without_a_model_says_so(project_workspace):
    from dataclasses import replace

    space = replace(project_workspace, review_mode="ollama_local", review_model=None)
    with pytest.raises(ConfigurationError, match="SPEC2TEST_REVIEW_MODEL"):
        tool_review_grounds(space, "TC-MCP-1")


def test_the_workspace_reads_the_environment():
    space = Workspace.from_env(
        {
            "SPEC2TEST_PROJECT": "/tmp/p.s2t.json",
            "SPEC2TEST_CORPUS": "/tmp/reqs",
            "SPEC2TEST_REVIEW_MODEL": "some:model",
        }
    )
    assert space.project.name == "p.s2t.json"
    assert space.corpus.name == "reqs"
    assert space.review_model == "some:model"
    assert space.review_mode == "ollama_local"


# ============================== a separately started process, over stdio ====
@pytest.mark.asyncio
async def test_a_separately_started_server_answers_over_stdio(workspace, tmp_path):
    """Not in-process: a real child process, the real transport.

    This is what a foreign agent gets. If the module has no entry point or
    the environment is not read, this fails and the in-memory tests do not.
    """
    import os
    import sys

    from fastmcp.client.transports import StdioTransport

    env = dict(os.environ)
    env.update(
        {
            "SPEC2TEST_CORPUS": str(workspace.corpus),
            "SPEC2TEST_INDEX": str(tmp_path / "stdio.db"),
            "PYTHONPATH": str(ROOT_SRC),
        }
    )
    transport = StdioTransport(
        command=sys.executable, args=["-m", "spec2test.adapters.mcp"], env=env
    )
    async with Client(transport) as client:
        names = sorted(t.name for t in await client.list_tools())
        assert names == [
            "check_grounds", "get_requirement", "review_grounds",
            "search_requirements",
        ]
        result = await client.call_tool(
            "get_requirement", {"requirement_id": "SYS-REQ-7001"}
        )
        payload = result.structured_content
        answer = payload.get("result", payload)
        assert answer["found"] is True
        assert answer["model_calls"] == 0
        assert answer["spans"][0]["document_filename"] == "a_pyrofuse.md"
