# Agent Runtime Protocol Specification

Version: `2025-01`

This document defines the localhost protocol for communication between web applications and the Agent Runtime.

## Overview

The Agent Runtime exposes a REST API on `localhost:9477` (default) that allows web applications to:

- Execute Python code in isolated Jupyter kernels
- Manage virtual environments per lab/session
- Stream execution output in real-time
- Control kernel lifecycle (start, stop, restart, interrupt)

## Authentication

### Pairing Flow

New origins must be paired before they can execute code:

1. Web app sends `POST /pairing/request` with its browser-supplied `Origin` header
2. Runtime displays that origin in the terminal where it is running
3. User approves or rejects the request
4. On approval, the pending HTTP response returns a bearer token
5. Web app stores token and includes in subsequent requests

### Scopes

A pairing grants one scope. The page asks for it in the pairing request, and the terminal prompt
shows it.

| Scope | May use | Meaning |
|---|---|---|
| `code` (default) | everything, including `/kernel/*`, `/cell/*` and `/labs/*` | run any Python on the user's machine |
| `actions` | `/labs/*` only | run the named actions of labs the user approves one by one |

A token with scope `actions` gets `403` from code endpoints. Origins paired before scopes existed
have `code`. Loopback origins, and runtimes started with `--no-pairing`, have `code`.

### Authorization Header

```
Authorization: Bearer <token>
```

### Localhost Exception

HTTP(S) origins whose host is `localhost` or a literal loopback IP address are
allowed without pairing, on any port. Lookalike domains and arbitrary hostnames
that resolve to loopback are not included.

Non-loopback pairing requires HTTPS; tokens are never issued to a remote
plain-HTTP origin.

## Endpoints

### Request Pairing

```
POST /pairing/request
Origin: https://course.example.com
Content-Type: application/json

{"scope": "actions"}
```

The body is optional; `scope` defaults to `"code"`. This request remains pending while the
runtime asks the local user to approve the exact `Origin` header and scope. On approval:

```json
{
  "origin": "https://course.example.com",
  "token": "<origin-bound bearer token>",
  "scope": "actions"
}
```

Returns `403` when rejected and `410` when the request expires.

### Runtime Info

```
GET /runtime/info
```

Returns runtime capabilities and version.

**Response:**
```json
{
  "runtime_version": "0.1.0",
  "protocol_version": "2025-01",
  "capabilities": ["python", "jupyter", "local_fs"]
}
```

### Health Check

```
GET /health
```

**Response:**
```json
{
  "status": "ok"
}
```

### Start Kernel

```
POST /kernel/start
Content-Type: application/json

{
  "lab_id": "abc123",
  "create_env": true,
  "python_version": "3.11"  // optional
}
```

Creates a virtual environment (if needed) and starts a Jupyter kernel.

If `create_env` is `false`, the environment must already exist. Otherwise this
endpoint returns `404` with instructions. `/cell/run` always auto-starts a
missing environment and kernel.

**Response:**
```json
{
  "lab_id": "abc123",
  "status": "started",
  "kernel_name": "agent-runtime-abc123",
  "ready": true
}
```

### Execute Code

```
POST /cell/run
Content-Type: application/json

{
  "lab_id": "abc123",
  "code": "print('hello')\n2 + 2",
  "cell_id": "cell-1",  // optional
  "stream": false
}
```

Executes code in the kernel. If `stream=true`, returns Server-Sent Events.

**Response (non-streaming):**
```json
{
  "cell_id": "cell-1",
  "success": true,
  "outputs": [
    {
      "output_type": "stream",
      "name": "stdout",
      "text": "hello\n"
    },
    {
      "output_type": "execute_result",
      "data": {"text/plain": "4"},
      "execution_count": 1
    }
  ],
  "error": null,
  "execution_count": 1
}
```

**Response (streaming):**
```
data: {"type": "stream", "name": "stdout", "text": "hello\n", "cell_id": "cell-1"}

data: {"type": "result", "data": {"text/plain": "4"}, "execution_count": 1, "cell_id": "cell-1"}

data: {"type": "complete", "cell_id": "cell-1"}
```

### Interrupt Execution

```
POST /kernel/interrupt?lab_id=abc123
```

Interrupts the currently running code (like Ctrl+C).

**Response:**
```json
{
  "status": "interrupted",
  "lab_id": "abc123"
}
```

### Restart Kernel

```
POST /kernel/restart?lab_id=abc123
```

Restarts the kernel, clearing all state.

**Response:**
```json
{
  "lab_id": "abc123",
  "status": "restarted",
  "kernel_name": "agent-runtime-abc123",
  "ready": true
}
```

### Shutdown Kernel

```
DELETE /kernel?lab_id=abc123
```

Shuts down the kernel. The environment is preserved.

**Response:**
```json
{
  "status": "shutdown",
  "lab_id": "abc123"
}
```

### Install Packages

```
POST /kernel/packages/install
Content-Type: application/json

{
  "lab_id": "abc123",
  "packages": ["pandas", "numpy"]
}
```

Installs packages into the lab's virtual environment.

**Response:**
```json
{
  "status": "installed",
  "lab_id": "abc123",
  "packages": ["pandas", "numpy"],
  "output": "Successfully installed pandas-2.0.0 numpy-1.24.0"
}
```

## Lab Actions

A lab repository declares the commands a page may run, in `lab.toml` at the repository root.
A page never sends a command: it names a repository and a full commit SHA, the user approves
that lab version once in the runtime's terminal, and from then on the page starts actions by
name. Commands run without a shell, in a checkout of that commit, with the lab's virtual
environment first on `PATH` (so `python` and `pip` are the lab's own).

### lab.toml (schema 1)

```toml
schema = 1
id = "restate-durable-agent-demo"   # letters, digits, . _ -; also names the lab's venv
title = "Durable agents with Restate"

[actions.setup]
label = "Prepare the lab"
description = "Start Restate in Docker and install the demo's packages."
steps = [
  ["docker", "compose", "up", "-d"],
  ["python", "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
]
timeout = 600        # seconds, 1-7200, default 900

[actions.stable]
label = "1.1 The extra model call"
steps = [["python", "demo.py", "--agent", "naive", "--model", "stable"]]
```

Every step is an argv list. Steps run in order, and the first one that exits non-zero ends the
action. Unknown keys are errors. Limits: 50 actions, 20 steps each, 100 arguments per step.

### Prepare a Lab

```
POST /labs/prepare
{"repo": "https://github.com/SciMigo/restate-durable-agent-demo",
 "commit": "<40-character SHA>"}
```

- Checks the source: `https` URLs only (git's `GIT_ALLOW_PROTOCOL`), no credentials, full SHA.
- Fetches exactly that commit (`--depth 1`, no submodules, no credential prompts) into
  `<runtime dir>/labs/<repo>/<sha[:12]>`, reusing an existing checkout.
- Reads `lab.toml`.
- If this origin has not approved this repo, commit and manifest before, it prompts in the
  terminal, listing every command. The request stays pending until the user answers, like
  pairing.
- Creates the lab's virtual environment on first use.

Response:

```json
{
  "lab_id": "restate-durable-agent-demo",
  "title": "Durable agents with Restate",
  "repo": "https://github.com/SciMigo/restate-durable-agent-demo",
  "commit": "<sha>",
  "path": "/Users/me/.agent-runtime/labs/restate-durable-agent-demo-1a2b3c4d/faf512d42fb8",
  "actions": [
    {"name": "setup", "label": "Prepare the lab", "description": "...",
     "steps": ["docker compose up -d", "python -m pip install --quiet -r requirements.txt"],
     "timeout": 600}
  ]
}
```

Errors: `400` for an invalid source or manifest, `403` when declined, `404` when the commit has no
`lab.toml`, `502` when git cannot fetch the commit.

### Start an Action

```
POST /labs/runs
{"lab_id": "restate-durable-agent-demo", "commit": "<sha>", "action": "stable"}
```

Returns a run (below) with status `running`. `409` if another action of the same lab is still
running (labs often share ports), or if the lab has not been prepared since the runtime started.

### Read a Run

```
GET /labs/runs/{run_id}?offset=0
```

```json
{
  "run_id": "...", "lab_id": "...", "commit": "...", "action": "stable",
  "status": "running",
  "exit_code": null,
  "started_at": "2026-09-19T05:00:00+00:00", "ended_at": null,
  "output": "$ python demo.py --agent naive --model stable\n== agent: naive ...\n",
  "next_offset": 123,
  "truncated": false
}
```

Poll with `offset=next_offset` to get only new output. `status` is `running`, `succeeded`,
`failed`, `stopped` or `timed_out`. Each step's command is echoed as `$ ...`. The runtime keeps
the last 1,000,000 characters of each run; `truncated` means the offset asked for was older than
that. A run is visible only to the origin that started it.

### Stop a Run

```
POST /labs/runs/{run_id}/stop
```

Sends SIGINT to the running step's process group, then SIGKILL if it has not exited after 8
seconds, and skips the remaining steps. Returns the run.

### List Runs

```
GET /labs/runs?lab_id=<optional>
```

This origin's runs, without output: a reloaded page can find a run that is still going.

## Output Types

### Stream Output

Standard output or error from the execution:

```json
{
  "output_type": "stream",
  "name": "stdout" | "stderr",
  "text": "output text"
}
```

### Execute Result

Return value of the last expression:

```json
{
  "output_type": "execute_result",
  "data": {
    "text/plain": "4",
    "text/html": "<b>4</b>"  // optional rich output
  },
  "execution_count": 1
}
```

### Display Data

Explicit display output (from `display()` or IPython rich output):

```json
{
  "output_type": "display_data",
  "data": {
    "text/plain": "<Figure>",
    "image/png": "base64-encoded-image"
  }
}
```

### Error

Execution error with traceback:

```json
{
  "output_type": "error",
  "ename": "ValueError",
  "evalue": "invalid literal",
  "traceback": [
    "Traceback (most recent call last):",
    "  File \"<stdin>\", line 1, in <module>",
    "ValueError: invalid literal"
  ]
}
```

## Error Responses

All endpoints return standard HTTP error codes:

- `400 Bad Request` - Invalid request body
- `401 Unauthorized` - Missing or invalid authorization
- `403 Forbidden` - Origin not paired
- `404 Not Found` - Lab/kernel not found
- `409 Conflict` - Pairing is disabled
- `410 Gone` - Pairing request expired
- `500 Internal Server Error` - Execution or system error

Error response body:

```json
{
  "detail": "Error message"
}
```

## WebSocket Support (Future)

A WebSocket endpoint for bidirectional streaming is planned:

```
ws://localhost:9477/ws/{lab_id}
```

This will enable:
- Real-time output streaming
- Kernel status updates
- Interactive input (`input()` function)

## Versioning

The protocol version follows a date-based scheme: `YYYY-MM`.

Breaking changes will increment the version. Clients should check `/runtime/info` and handle version mismatches gracefully.
