"""MCP tools as smolagents tools, without coupling the two libraries.

smolagents reaches MCP through ``mcpadapt``, which pins an older ``mcp``
than FastMCP 4 installs; the two cannot be satisfied at once. Rather than
pinning one library to keep the other's adapter happy — a constraint that
breaks again at the next release of either — this bridge talks the MCP
protocol itself and hands smolagents plain ``Tool`` objects.

It is deliberately small: connect, list the tools, call one, close. No
caching, no retries, no schema translation beyond what smolagents needs.
The server is the project's own, started as a child process, so the tools
are the real ones and the JSON crossing the boundary is the real JSON.

The client side is FastMCP's own ``Client``. The raw ``mcp`` session would
work too in principle, but mcp 2.x negotiates the protocol version in a way
that closed the connection here; FastMCP's client already handles that, and
the server is a FastMCP server anyway.
"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any, Sequence

#: smolagents wants a JSON-schema-ish type name per input
_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


class BridgeError(RuntimeError):
    """The MCP server could not be reached or spoke unexpectedly."""


@dataclass
class _Loop:
    """A background event loop, so sync smolagents can drive async MCP."""

    loop: asyncio.AbstractEventLoop
    thread: threading.Thread

    @classmethod
    def start(cls) -> "_Loop":
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        return cls(loop=loop, thread=thread)

    def run(self, coroutine, timeout: float = 120.0):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return future.result(timeout=timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


def _unwrap(payload: Any) -> Any:
    """FastMCP wraps a dict return in {"result": ...}; callers want the dict."""
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


class McpToolBridge:
    """A connection to one MCP server, exposing its tools to smolagents.

    Use as a context manager. ``tools(only=...)`` returns smolagents tools;
    anything not named in ``only`` is not built at all, so a tool the
    reviewer must not have cannot be handed over by accident.
    """

    def __init__(self, server_parameters, *, timeout: float = 120.0) -> None:
        self._parameters = server_parameters
        self._timeout = timeout
        self._loop: _Loop | None = None
        self._session = None
        self._stack = None
        self._descriptions: list[dict] = []

    # --------------------------------------------------------- lifecycle --
    def __enter__(self) -> "McpToolBridge":
        from contextlib import AsyncExitStack

        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport

        self._loop = _Loop.start()
        parameters = self._parameters
        transport = StdioTransport(
            command=parameters.command,
            args=list(parameters.args),
            env=dict(parameters.env or {}),
        )

        async def connect():
            stack = AsyncExitStack()
            client = await stack.enter_async_context(Client(transport))
            listed = await client.list_tools()
            return stack, client, listed

        try:
            self._stack, self._session, listed = self._loop.run(
                connect(), timeout=self._timeout
            )
        except Exception as exc:  # noqa: BLE001 - every transport failure is one
            self._loop.stop()
            self._loop = None
            raise BridgeError(
                f"the MCP server could not be reached: {type(exc).__name__}: {exc}"
            ) from exc
        self._descriptions = [
            {
                "name": tool.name,
                "description": (tool.description or "").strip(),
                "schema": getattr(tool, "input_schema", None)
                or getattr(tool, "inputSchema", None)
                or {},
            }
            for tool in listed
        ]
        return self

    def __exit__(self, *_exc) -> None:
        if self._loop is None:
            return
        stack = self._stack

        async def close():
            if stack is not None:
                await stack.aclose()

        try:
            self._loop.run(close(), timeout=10)
        except Exception:  # noqa: BLE001 - closing must not mask the real error
            pass
        self._loop.stop()
        self._loop = None
        self._session = None

    # ------------------------------------------------------------- tools --
    @property
    def available(self) -> list[str]:
        return [d["name"] for d in self._descriptions]

    def call(self, name: str, arguments: dict) -> Any:
        if self._session is None or self._loop is None:
            raise BridgeError("the bridge is not connected")

        async def run():
            return await self._session.call_tool(name, arguments)

        result = self._loop.run(run(), timeout=self._timeout)
        structured = getattr(result, "structured_content", None) or getattr(
            result, "structuredContent", None
        )
        if structured is not None:
            return _unwrap(structured)
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    return _unwrap(json.loads(text))
                except json.JSONDecodeError:
                    return text
        return None

    def tools(self, only: Sequence[str] | None = None) -> list:
        """smolagents tools for this server, restricted to ``only``."""
        from smolagents import Tool

        wanted = set(only) if only is not None else None
        built = []
        for description in self._descriptions:
            name = description["name"]
            if wanted is not None and name not in wanted:
                continue
            built.append(_build_tool(self, name, description, Tool))
        return built


def _build_tool(bridge: "McpToolBridge", name: str, description: dict, base):
    """One smolagents Tool that forwards to the MCP server."""
    schema = description.get("schema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    inputs = {}
    for key, spec in properties.items():
        kind = spec.get("type")
        if isinstance(kind, list):  # {"type": ["string", "null"]}
            kind = next((k for k in kind if k != "null"), "string")
        entry = {
            "type": _TYPES.get(kind, "string"),
            "description": (spec.get("description") or key),
        }
        if key not in required:
            entry["nullable"] = True
        inputs[key] = entry

    # smolagents checks that forward's parameters match the declared inputs
    # by name, so the signature is built from the server's own schema
    order = list(inputs)
    arguments = ", ".join(
        key if key in required else f"{key}=None" for key in order
    )
    source = (
        f"def forward(self, {arguments}):\n"
        f"    values = {{{', '.join(f'{k!r}: {k}' for k in order)}}}\n"
        "    cleaned = {k: v for k, v in values.items() if v is not None}\n"
        "    return _bridge.call(self.name, cleaned)\n"
    ) if order else (
        "def forward(self):\n    return _bridge.call(self.name, {})\n"
    )
    namespace: dict = {"_bridge": bridge}
    exec(source, namespace)  # noqa: S102 - the schema comes from our own server

    # smolagents validates name, description, inputs and output_type in
    # Tool.__init__, so they have to be on the class, not set afterwards
    attributes = {
        "name": name,
        "description": description.get("description") or name,
        "inputs": inputs,
        "output_type": "object",
        "forward": namespace["forward"],
    }
    return type(f"Mcp_{name}", (base,), attributes)()


__all__ = ["BridgeError", "McpToolBridge"]
