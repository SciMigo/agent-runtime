# Agent Runtime

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A lightweight local execution engine for browser-based AI labs and agents.

> [!WARNING]
> Agent Runtime is **not a sandbox**. Python code runs with your user account's
> file, network, and process permissions. Start it only for browser origins you
> trust, review every non-loopback pairing prompt, and do not expose port 9477
> to a network.

## What is this?

Agent Runtime is the execution layer that powers AI agent code execution. It provides:

- **Jupyter kernel management** - persistent Python sessions with real notebook semantics
- **Per-lab Python environments** - dependencies are separated between sessions
- **Secure localhost protocol** - pairing-based authentication for browser-to-runtime communication
- **Streaming execution** - real-time stdout/stderr with interrupt support
- **MCP server** - any agent client can run code in a lab, through `agent-runtime mcp`
- **Observability hooks** - structured events for monitoring and debugging

## Trust Model

This runtime is designed with user trust as a core principle:

- Runs entirely on your machine
- No data sent to external services without your consent
- Open source and inspectable
- Binds to the loopback interface by default

## Quick Start

### Prerequisites

- Python 3.11+
- Linux or macOS (both are exercised in CI)
- Windows is not currently tested or officially supported

### Installation

On macOS, one command. It uses [uv](https://docs.astral.sh/uv/) if you have it, which also brings
the right Python, or else Python 3.11 or newer; if neither is there, it says how to get one. Run it
again to upgrade.

```bash
curl -fsSL https://raw.githubusercontent.com/SciMigo/agent-runtime/main/scripts/install-macos.sh | bash
```

On Linux, or anywhere with uv:

```bash
uv tool install --from https://github.com/SciMigo/agent-runtime/archive/refs/heads/main.tar.gz agent-runtime
```

If your shell cannot find `agent-runtime` afterwards, run `uv tool update-shell` and open a new
terminal. Do not `pip install agent-runtime` from PyPI: that name belongs to an unrelated package.

To work on the runtime itself, install it from a clone:

```bash
# Clone the repository
git clone https://github.com/SciMigo/agent-runtime.git
cd agent-runtime

# Install with uv (recommended)
uv venv
source .venv/bin/activate
uv pip install -e .

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Running the Runtime

```bash
# Start the runtime server
agent-runtime serve

# Or run directly
python -m agent_runtime.server
```

The runtime will start on `http://localhost:9477` by default.

### HTTPS for Safari

Safari blocks HTTPS pages from calling `http://127.0.0.1`, so a course page on
an HTTPS site cannot reach the runtime there. Run this once:

```bash
agent-runtime tls setup
```

It creates a certificate valid only for 127.0.0.1, ::1 and localhost (not a
certificate authority, 397 days) and, on macOS, trusts it for SSL in your login
keychain, which asks for your password. From then on `agent-runtime serve` also
listens on `https://127.0.0.1:9478`. `agent-runtime tls status` shows the
certificate and its expiry; `tls setup` renews it; `tls remove` untrusts and
deletes it. Chrome and Firefox reach `http://127.0.0.1:9477` without this step.

### Pairing

Loopback web apps (`localhost`, `127.0.0.1`, or `::1`, on any port) are trusted
automatically. A non-loopback web app starts pairing with `POST
/pairing/request`. The terminal running Agent Runtime then shows an approval
prompt:

```
New pairing request
Origin: https://app.example.com
Approve this connection? [y/N]
```

If approved, the HTTP response returns an origin-bound bearer token. The web
app must store it and include `Authorization: Bearer <token>` on runtime calls.
The request stays open while the user decides.

## Web App Integration

### Development Mode (no pairing)

For local development, you can disable the pairing requirement:

```bash
agent-runtime serve --no-pairing
```

Or set the environment variable:

```bash
export AGENT_RUNTIME_REQUIRE_PAIRING=false
agent-runtime serve
```

### JavaScript Example

```javascript
// HTTPS (after `agent-runtime tls setup`) is the only address Safari can reach from
// an HTTPS page; Chrome and Firefox reach both. No targetAddressSpace option is
// needed: 127.0.0.1 is loopback, and Chrome rejects the "local" annotation for it.
const RUNTIME_URL = 'https://127.0.0.1:9478';  // or 'http://127.0.0.1:9477'

// Required when this page is served from a non-loopback origin.
async function pair() {
  const res = await fetch(`${RUNTIME_URL}/pairing/request`, { method: 'POST' });
  if (!res.ok) throw new Error((await res.json()).detail);
  return (await res.json()).token;
}

// Check if runtime is available
async function checkRuntime() {
  const res = await fetch(`${RUNTIME_URL}/health`);
  return res.ok;
}

// Start a kernel for a session
async function startKernel(labId, token) {
  const res = await fetch(`${RUNTIME_URL}/kernel/start`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` })
    },
    body: JSON.stringify({ lab_id: labId })
  });
  return res.json();
}

// Execute Python code
async function executeCode(labId, code, token) {
  const res = await fetch(`${RUNTIME_URL}/cell/run`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` })
    },
    body: JSON.stringify({
      lab_id: labId,
      code: code,
      stream: false
    })
  });
  return res.json();
}

// Example usage
const kernel = await startKernel('my-session');
const result = await executeCode('my-session', 'print("Hello from Python!")');
console.log(result.outputs);
```

### Streaming Execution

For real-time output, use Server-Sent Events:

```javascript
async function executeStreaming(labId, code, onOutput) {
  const res = await fetch(`${RUNTIME_URL}/cell/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lab_id: labId, code: code })
  });

  const reader = res.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const text = decoder.decode(value);
    for (const line of text.split('\n')) {
      if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));
        onOutput(data);
      }
    }
  }
}

// Usage
await executeStreaming('my-session', 'for i in range(5): print(i)', (output) => {
  console.log('Output:', output);
});
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_RUNTIME_HOST` | `127.0.0.1` | Server bind address |
| `AGENT_RUNTIME_PORT` | `9477` | Server port |
| `AGENT_RUNTIME_HTTPS_PORT` | `9478` | HTTPS port, used once `agent-runtime tls setup` has run |
| `AGENT_RUNTIME_REQUIRE_PAIRING` | `true` | Require origin approval |
| `AGENT_RUNTIME_DEBUG` | `false` | Enable debug mode with hot reload |

## Agent Clients (MCP)

Agent Runtime speaks the Model Context Protocol, so an agent client - Claude Code, Claude
Desktop, Cursor - can run code in a learner's lab and read what came back: the real traceback,
in the real environment. Install the extra and issue that client a token:

```bash
uv pip install 'agent-runtime[mcp]'
agent-runtime token create "Claude Desktop" --scope code
```

The token is shown once. Put it in the client's own configuration:

```json
{
  "mcpServers": {
    "agent-runtime": {
      "command": "agent-runtime",
      "args": ["mcp"],
      "env": { "AGENT_RUNTIME_TOKEN": "<the token>" }
    }
  }
}
```

With `agent-runtime serve` running, the client gets a `run_python` tool: a persistent kernel per
lab, figures returned as images, tracebacks returned as text to read rather than as errors.

`agent-runtime token list` and `agent-runtime token revoke <name>` manage clients, and take
effect without restarting the runtime. The default scope is `actions`, which can start only the
named actions of labs you have approved; `--scope code` is what runs arbitrary Python, and is
worth the same thought as letting that client type into your terminal.

See [docs/mcp.md](docs/mcp.md) for the design and what comes next.

## API Overview

### Runtime Info

```
GET /runtime/info    - version and capabilities
GET /runtime/whoami  - who the runtime takes the caller to be, and its scope
```

`/runtime/info` returns:
```json
{
  "runtime_version": "0.1.0",
  "protocol_version": "2025-01",
  "capabilities": ["python", "jupyter", "local_fs", "lab_actions", "pairing_scopes",
                   "local_clients"]
}
```

### Kernel Management

```
POST /kernel/start     - Start a kernel for a lab
POST /kernel/interrupt - Interrupt running execution
POST /kernel/restart   - Restart the kernel
DELETE /kernel         - Shutdown the kernel
```

### Code Execution

```
POST /cell/run         - Execute code in the kernel
```

### Lab Actions

A lab repository can declare, in `lab.toml`, the commands a course page may run. The page pairs
with the narrower `actions` scope, which cannot reach `/kernel` or `/cell`. The learner approves
each lab version once, after seeing every command. See
[docs/protocol.md](docs/protocol.md#lab-actions).

```
POST /labs/prepare             - Fetch a repo at a full commit SHA, read lab.toml, ask to approve
POST /labs/runs                - Start a named action
GET  /labs/runs/{id}?offset=N  - Status and output since N
POST /labs/runs/{id}/stop      - Stop it (SIGINT, then SIGKILL)
GET  /labs/runs                - This site's runs
```

## Architecture

```
Browser/Web App
      │
      │  HTTP / WebSocket (localhost protocol)
      ▼
Agent Runtime (FastAPI)
      │
      ├─ Auth Layer (pairing, tokens, origin allowlist)
      ├─ Env Manager (venvs, kernel specs)
      │
      │  Jupyter Protocol (internal)
      ▼
Jupyter Kernel (IPython)
```

## Configuration

Configuration is stored in `~/.agent-runtime/`:

```
~/.agent-runtime/
├── config.toml          # Runtime configuration
├── paired_origins.json  # Approved origins
├── local_clients.json   # Tokens for local processes, such as an MCP client
└── envs/                # Virtual environments
    └── lab-{id}/
        └── .venv/
```

## Security

See [docs/security.md](docs/security.md) for the threat model and security considerations.

## Protocol

See [docs/protocol.md](docs/protocol.md) for the full localhost protocol specification.

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Run with hot reload
uvicorn agent_runtime.server:app --reload --port 9477
```

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT - see [LICENSE](LICENSE) for details.
