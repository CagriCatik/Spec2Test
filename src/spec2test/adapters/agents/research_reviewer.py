"""The researching reviewer: smolagents over the project's own MCP server.

The agent gets exactly two tools, ``search_requirements`` and
``get_requirement``, taken from the running MCP server rather than
reimplemented here. ``review_grounds`` is deliberately **not** offered: a
reviewer that can call a reviewer produces recursive model checks and a
bill nobody authorised.

Everything it reads is recorded and ends up in a separate review snapshot
(:mod:`spec2test.application.research_review`), never in the generation
run's context — the draft was written without those sources.

Budgets are enforced by wrapping, not by asking nicely:

* the model is wrapped in a counter that refuses the call past the limit;
* each tool is wrapped in a counter and a clock;
* the deadline is checked before every model and tool call.

Verified against smolagents 1.26: ``Model.generate(messages,
stop_sequences, response_format, tools_to_call_from, **kwargs) ->
ChatMessage``; ``ToolCallingAgent(tools, model, max_steps=...)``; tool
calls are readable afterwards from ``agent.memory.steps[*].tool_calls``.

smolagents' own ``MCPClient`` is **not** used. It reaches MCP through
``mcpadapt``, which needs an older ``mcp`` package than FastMCP 4 installs,
and the two cannot be satisfied together. Pinning one library to keep the
other's adapter happy breaks again at the next release of either, so
:mod:`.mcp_bridge` speaks the protocol directly. The server is still the
project's own, started as a child process.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ...application.research_review import (
    RESEARCH_PROMPT_VERSION,
    SYSTEM_PROMPT,
    Budget,
    ConsultedSource,
    ResearchReport,
    ToolCallRecord,
    build_task,
    interpret,
)

#: the only tools the reviewer may use
ALLOWED_TOOLS = ("search_requirements", "get_requirement")
FRAMEWORK = "smolagents"


class BudgetExhausted(RuntimeError):
    """A limit was reached. The run stops; the report says so."""


class ReviewerUnavailable(RuntimeError):
    """The agent framework or the MCP server could not be used."""


@dataclass
class _Ledger:
    """What has been spent, shared by the wrappers."""

    budget: Budget
    started: float
    model_calls: int = 0
    tool_calls: list[ToolCallRecord] = None  # type: ignore[assignment]
    consulted: list[ConsultedSource] = None  # type: ignore[assignment]
    exhausted: bool = False
    #: why the run was cut short, kept because the agent framework turns a
    #: tool failure into an observation and the reason would be lost
    reason: str | None = None

    def __post_init__(self) -> None:
        self.tool_calls = []
        self.consulted = []

    def check_clock(self) -> None:
        if time.monotonic() - self.started > self.budget.max_seconds:
            self.exhausted = True
            self.reason = f"the review exceeded {self.budget.max_seconds:g} seconds"
            raise BudgetExhausted(self.reason)

    def spend_model_call(self) -> None:
        self.check_clock()
        if self.model_calls >= self.budget.max_model_calls:
            self.exhausted = True
            self.reason = (
                f"the review reached {self.budget.max_model_calls} model call(s)"
            )
            raise BudgetExhausted(self.reason)
        self.model_calls += 1

    def spend_tool_call(self) -> None:
        self.check_clock()
        if len(self.tool_calls) >= self.budget.max_tool_calls:
            self.exhausted = True
            self.reason = f"the review reached {self.budget.max_tool_calls} lookup(s)"
            raise BudgetExhausted(self.reason)


def _record_sources(ledger: _Ledger, tool_name: str, payload: Any) -> int:
    """Note what a lookup actually returned, with version and position."""
    if not isinstance(payload, dict):
        return 0
    if tool_name == "get_requirement":
        identifier = payload.get("external_id") or payload.get("requirement_id") or ""
        if not payload.get("found"):
            ledger.consulted.append(
                ConsultedSource(
                    identifier=str(payload.get("message", ""))[:80] or "unknown",
                    found=False,
                    ambiguous=bool(payload.get("candidates")),
                    quote=str(payload.get("message", ""))[:200],
                )
            )
            return 0
        span = (payload.get("spans") or [{}])[0]
        ledger.consulted.append(
            ConsultedSource(
                identifier=identifier,
                document_id=span.get("document_id"),
                document_filename=span.get("document_filename"),
                document_version=span.get("document_version"),
                section=span.get("section"),
                char_start=span.get("char_start"),
                char_end=span.get("char_end"),
                quote=str(payload.get("text", ""))[:600],
            )
        )
        return 1
    hits = payload.get("hits") or []
    for hit in hits:
        span = hit.get("source") or {}
        ledger.consulted.append(
            ConsultedSource(
                identifier=hit.get("external_id") or hit.get("passage_id") or "?",
                document_id=span.get("document_id"),
                document_filename=span.get("document_filename"),
                document_version=span.get("document_version"),
                section=span.get("section"),
                char_start=span.get("char_start"),
                char_end=span.get("char_end"),
                quote=str(hit.get("text", ""))[:600],
            )
        )
    return len(hits)


def _wrap_tool(tool, ledger: _Ledger):
    """Count the call, record what came back, keep the tool's interface."""
    import json as _json

    original = tool.forward

    def forward(*args, **kwargs):
        ledger.spend_tool_call()
        record = ToolCallRecord(tool=tool.name, arguments=dict(kwargs))
        try:
            result = original(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the agent must see it
            ledger.tool_calls.append(
                ToolCallRecord(
                    tool=tool.name, arguments=dict(kwargs),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise
        payload = result
        if isinstance(payload, str):
            try:
                payload = _json.loads(payload)
            except _json.JSONDecodeError:
                payload = {}
        if isinstance(payload, dict) and "result" in payload:
            payload = payload["result"]
        count = _record_sources(ledger, tool.name, payload)
        ledger.tool_calls.append(
            ToolCallRecord(
                tool=tool.name, arguments=dict(kwargs), result_count=count
            )
        )
        return result

    tool.forward = forward
    return tool


def _wrap_model(model, ledger: _Ledger):
    """Count model calls and stop at the limit, keeping smolagents' API."""
    original = model.generate

    def generate(messages, **kwargs):
        ledger.spend_model_call()
        return original(messages, **kwargs)

    model.generate = generate
    return model


def build_model(model_id: str, *, api_base: str, api_key: str = "ollama"):
    """The configured Ollama endpoint, through its OpenAI-compatible API."""
    try:
        from smolagents import OpenAIModel
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise ReviewerUnavailable(
            'smolagents is not installed: pip install -e ".[agents]"'
        ) from exc
    return OpenAIModel(model_id=model_id, api_base=api_base, api_key=api_key)


def server_parameters(
    *,
    corpus: Path | str,
    index: Path | str | None = None,
    python: str | None = None,
    extra_env: dict[str, str] | None = None,
):
    """How to start this project's MCP server as a child process.

    The package directory is put on the child's PYTHONPATH explicitly. A
    parent that can import ``spec2test`` from a source checkout does not
    pass that ability on: ``pythonpath`` in pytest.ini, a ``conftest`` or a
    manually extended ``sys.path`` all stop at the process boundary, and
    the child then dies with ModuleNotFoundError, which arrives here only
    as "connection closed".
    """
    import os
    import sys

    from mcp import StdioServerParameters

    env = dict(os.environ)
    env["SPEC2TEST_CORPUS"] = str(corpus)
    if index:
        env["SPEC2TEST_INDEX"] = str(index)
    package_root = str(Path(__file__).resolve().parents[3])
    existing = env.get("PYTHONPATH", "")
    if package_root not in existing.split(os.pathsep):
        env["PYTHONPATH"] = (
            f"{package_root}{os.pathsep}{existing}" if existing else package_root
        )
    env.update(extra_env or {})
    return StdioServerParameters(
        command=python or sys.executable,
        args=["-m", "spec2test.adapters.mcp"],
        env=env,
    )


def review_with_research(
    case,
    requirement,
    *,
    model,
    tools: Sequence,
    context: Sequence = (),
    budget: Budget | None = None,
    max_steps: int = 6,
) -> ResearchReport:
    """Run the reviewer over one draft. Returns a snapshot, changes nothing.

    ``tools`` are MCP tools; anything outside :data:`ALLOWED_TOOLS` is
    dropped rather than passed on, so a server that grows a writing tool
    cannot hand it to the reviewer by accident.
    """
    try:
        from smolagents import ToolCallingAgent
    except ImportError as exc:  # pragma: no cover
        raise ReviewerUnavailable(
            'smolagents is not installed: pip install -e ".[agents]"'
        ) from exc

    budget = budget or Budget()
    ledger = _Ledger(budget=budget, started=time.monotonic())

    offered = [t for t in tools if getattr(t, "name", None) in ALLOWED_TOOLS]
    dropped = [
        getattr(t, "name", "?") for t in tools
        if getattr(t, "name", None) not in ALLOWED_TOOLS
    ]
    wrapped_tools = [_wrap_tool(t, ledger) for t in offered]
    wrapped_model = _wrap_model(model, ledger)

    agent = ToolCallingAgent(
        tools=wrapped_tools,
        model=wrapped_model,
        max_steps=max_steps,
        instructions=SYSTEM_PROMPT,
        add_base_tools=False,
        verbosity_level=0,
    )

    answer = ""
    error: str | None = None
    try:
        answer = str(agent.run(build_task(case, requirement, context)))
    except BudgetExhausted as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - an agent failure is a result
        error = f"{type(exc).__name__}: {exc}"
    if ledger.exhausted and ledger.reason:
        # the framework turns a tool failure into an observation and runs on,
        # so the budget reason has to win over whatever came out afterwards
        error = ledger.reason

    report = interpret(
        case,
        answer,
        model=getattr(model, "model_id", str(model)),
        framework=f"{FRAMEWORK} ToolCallingAgent",
        budget=budget,
        model_calls=ledger.model_calls,
        tool_calls=ledger.tool_calls,
        consulted=ledger.consulted,
        budget_exhausted=ledger.exhausted,
        error=error,
    )
    if dropped:
        report.provenance_note += (
            f" Tools not offered to the reviewer: {', '.join(sorted(dropped))}."
        )
    return report


__all__ = [
    "ALLOWED_TOOLS",
    "FRAMEWORK",
    "BudgetExhausted",
    "ReviewerUnavailable",
    "build_model",
    "review_with_research",
    "server_parameters",
]
