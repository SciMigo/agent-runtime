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
const RUNTIME_URL = 'http://127.0.0.1:9477';

// Required when this page is served from a non-loopback origin.
async function pair() {
  const res = await fetch(`${RUNTIME_URL}/pairing/request`, {
    method: 'POST',
    targetAddressSpace: 'local'
  });
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
| `AGENT_RUNTIME_REQUIRE_PAIRING` | `true` | Require origin approval |
| `AGENT_RUNTIME_DEBUG` | `false` | Enable debug mode with hot reload |

## API Overview

### Runtime Info

```
GET /runtime/info
```

Returns:
```json
{
  "runtime_version": "0.1.0",
  "protocol_version": "2025-01",
  "capabilities": ["python", "jupyter", "local_fs"]
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
├── config.toml         # Runtime configuration
├── paired_origins.json # Approved origins
└── envs/               # Virtual environments
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
