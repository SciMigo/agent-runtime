"""An MCP server for the runtime, so agent clients can run code in a learner's lab.

See `docs/mcp.md` for the design. The adapter holds no state of its own: it is an HTTP client
of a runtime already serving on loopback. Importing `agent_runtime.mcp.server` requires the
optional `mcp` dependency; nothing else in the runtime does.
"""
