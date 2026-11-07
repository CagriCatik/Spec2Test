"""``python -m spec2test.adapters.mcp`` starts the read-only MCP server."""
from .server import main

if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
