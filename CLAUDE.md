# CLAUDE.md

This file provides guidance for Claude Code when working on this repository.

## Project Overview

Agent-runtime is a lightweight execution engine for AI agents. It provides:
- Jupyter kernel management with persistent Python sessions
- Virtual environment isolation per lab/session
- Pairing-based authentication for browser-to-runtime communication
- Streaming code execution with interrupt support

## Repository Structure

```
agent-runtime/
├── src/agent_runtime/
│   ├── __init__.py
│   ├── server.py          # FastAPI application entry point
│   ├── config.py           # Configuration management
│   ├── auth.py             # Pairing and token authentication
│   ├── envs.py             # Virtual environment management
│   ├── events.py           # Observability event system
│   ├── observability.py    # Metrics and logging
│   ├── cli.py              # CLI entry point
│   ├── serving.py          # HTTP + HTTPS listeners with one shutdown
│   ├── tls.py              # Loopback certificate and macOS keychain trust (Safari)
│   ├── labs.py             # Named lab actions: lab.toml, pinned checkouts, approvals, runs
│   ├── local_tokens.py     # Tokens for local processes, which have no origin to pair
│   ├── api/
│   │   ├── health.py       # Health check endpoints
│   │   ├── kernel.py       # Kernel lifecycle endpoints
│   │   ├── labs.py         # /labs: prepare, runs, stop
│   │   └── execute.py      # Code execution endpoints
│   ├── mcp/                # MCP server (optional dependency); see docs/mcp.md
│   │   ├── server.py       # Tools, and the MCPServer instance
│   │   ├── client.py       # HTTP client of a runtime already serving
│   │   └── render.py       # Kernel outputs -> MCP content
│   └── kernels/
│       ├── manager.py      # Kernel lifecycle management
│       └── ipython.py      # IPython kernel wrapper
├── docs/
│   ├── protocol.md         # REST API specification
│   ├── security.md         # Security model and threat analysis
│   ├── mcp.md              # MCP server: design, status and what comes next
│   └── integration.md      # Browser integration guide
├── tests/
└── pyproject.toml
```

## Key Design Decisions

### Standalone Architecture

Agent-runtime uses a public localhost HTTP protocol and has no dependency on a
specific agent SDK. See `docs/integration.md` for browser integration patterns,
and `docs/mcp.md` for the MCP server, which is an adapter over that same
protocol rather than a second runtime.

### Security Model

- Localhost-only binding (never 0.0.0.0 in production)
- Origin allowlist via pairing flow
- Bearer token authentication
- Per-lab dependency isolation; code itself is not sandboxed
- Local processes (an MCP client) authenticate with an issued token, not an origin

See `docs/security.md` for the full threat model.

## Development Commands

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run the server (development)
uvicorn agent_runtime.server:app --reload --port 9477

# Run tests
pytest

# Type checking
mypy src/agent_runtime

# Linting
ruff check src/
```

## Code Style

- Python 3.11+ with type hints
- Pydantic for all API models
- Async/await for I/O operations
- Ruff for linting (configured in pyproject.toml)

## API Conventions

- REST endpoints follow `/resource/action` pattern
- Streaming uses Server-Sent Events (SSE)
- All responses are JSON with consistent error format
- Lab ID is the primary session identifier

## Common Tasks

### Adding a New Endpoint

1. Create handler in appropriate `api/` module
2. Add Pydantic request/response models
3. Register route in `server.py`
4. Add to `docs/protocol.md`

## Testing Strategy

- Unit tests for kernel management
- Integration tests for API endpoints
- Use `pytest-asyncio` for async tests
- Mock Jupyter kernels where appropriate

## Future Work

- [ ] WebSocket support for bidirectional streaming
- [ ] Container support for hosted environments
- [ ] Multi-language kernel support
