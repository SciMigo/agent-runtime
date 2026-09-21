# MCP Server

> [!NOTE]
> **Status: step 1 is implemented**, and this document doubles as its design. Shipped:
> local client tokens, `GET /runtime/whoami`, `agent-runtime token`, `agent-runtime mcp` and
> its one tool, `run_python`. Steps 2 to 4 below are still proposals. Where the implementation
> departed from the design, the section says so.

An MCP server would let any Model Context Protocol client — Claude Code, Claude Desktop, Cursor,
an agent gateway — run code in a learner's lab, start the lab's approved actions, and read what
came back. For an AI tutor this is the difference between guessing from what a page reports and
reading the learner's actual traceback, in the learner's actual environment.

MCP is also the reason not to write one integration per client. One adapter reaches all of them.

## Shape: an adapter, not a second runtime

The MCP server is a thin process that talks to a running runtime over loopback HTTP. It owns no
kernels, no environments and no runs.

```
MCP client  ──stdio──>  agent-runtime mcp  ──HTTP 127.0.0.1:9477──>  agent-runtime serve
                        (adapter, stateless)                         (kernels, labs, runs)
```

This is the load-bearing decision. `kernel_manager` and `lab_manager` are process globals, and
runs live in memory (`labs.py`). A second process that imported them would get its *own* kernels:
the tutor would not see the cell the learner just ran in the browser, two processes would race to
create the same virtual environment and install the same kernel spec, and runs started through one
would be invisible to the other. An adapter has one source of truth by construction.

When the runtime is not listening, the adapter's tools fail with a message saying to start
`agent-runtime serve`, rather than starting a rival copy.

## Transport

Ship **stdio** first. The client configuration is one line, every MCP client supports it, and it
needs no port, no CORS and no DNS-rebinding defense:

```json
{
  "mcpServers": {
    "agent-runtime": {
      "command": "agent-runtime",
      "args": ["mcp"],
      "env": { "AGENT_RUNTIME_TOKEN": "<from 'agent-runtime token create'>" }
    }
  }
}
```

`AGENT_RUNTIME_URL` overrides where the adapter looks for the runtime; `--url` and `--token`
do the same on the command line.

Streamable HTTP at `/mcp` inside the `serve` process is the natural second step. Note before
taking it that the CORS middleware allows any web origin (`server.py`), so that endpoint would be
reachable from any page; the bearer check remains the authorization boundary, but it deserves its
own review. Write the tool bodies against a small `RuntimeClient` interface so swapping transport
does not touch them.

SDK notes, checked 2026-09-21: the Python package is `mcp` (`pip install "mcp[cli]"`), the server
class is `MCPServer` from `mcp.server`, tools are declared with the `@mcp.tool()` decorator over a
typed function, and the process runs with `mcp.run(transport="stdio")`. On stdio, stdout belongs
to the protocol: log through `logging`, which writes to stderr.

## Authentication: local client tokens

A local process is not a browser origin, and today it cannot authenticate at all. `_authenticate`
in `auth.py` reads the `Origin` header; a request without one falls through to the token check,
where `validate_token(token, "")` cannot match, because every token is bound to a paired origin.
The only way in is `--no-pairing`, which is not an answer.

So there is a third principal type, with its own store beside `paired_origins.json`:

| Principal | Identified by | Granted by |
|---|---|---|
| Loopback page | `Origin` header on a literal loopback host | automatic |
| Paired site | origin-bound bearer token | terminal approval of the origin |
| **Local client** | **bearer token, no `Origin` header** | **`agent-runtime token create`** |

```python
# auth.py
async def _authenticate(request: Request, authorization: str | None) -> tuple[str, str]:
    origin = request.headers.get("origin", "")
    if not settings.require_pairing:
        return origin, "code"
    if origin:
        return await _authenticate_browser(origin, authorization)  # today's path, unchanged
    # No Origin: a local process, not a browser.
    client = local_tokens.lookup(_bearer(authorization))
    if client is None:
        raise HTTPException(status_code=401, detail="no local client token")
    return f"local:{client.name}", client.scope
```

Two invariants keep the browser model intact:

- a paired-origin token never validates as a local client token, and vice versa — separate stores,
  looked up on separate branches;
- a local token presented *with* an `Origin` header is rejected. It is not origin-bound, so it must
  never be usable from a page.

Tokens are hashed before storage, like pairing tokens, in `<runtime dir>/local_clients.json`
(`0600`). Unlike pairing, which happens through the running server, tokens are issued by a
separate process, so the server re-reads that file whenever it changes: a token issued while
the runtime is serving works immediately, and - the reason the check is on every lookup rather
than only on a miss - a revoked one stops working immediately.

The CLI mirrors `agent-runtime pairing`:

```bash
agent-runtime token create "Claude Desktop"    # prints the token once; defaults to scope actions
agent-runtime token create "tutor" --scope code
agent-runtime token list
agent-runtime token revoke "Claude Desktop"
```

`GET /runtime/whoami` returns the caller's principal and scope, for a client to check its token
with.

The design had the adapter register only the tools its token can use. It does not: a client asks
for the tool list once, at connect time, when the runtime may not be running yet, and a list that
varies with whether the daemon happened to be up is worse than a stable one. A call the scope
forbids fails with the runtime's own message and the command that widens it.

## Tools

Ten tools, each a verb a model already understands. Deliberately not a mirror of the REST surface:
an agent should not have to orchestrate prepare → run → poll by hand.

`run_python` is implemented; the rest arrive with steps 2 and 3. `list_labs` waits for step 2
because environments are not exposed over HTTP at all yet.

| Tool | Arguments | Scope | Notes |
|---|---|---|---|
| `list_labs` | — | both | environments, live kernels, prepared commits |
| `run_python` | `lab_id`, `code` | code | **shipped.** Creates the environment and kernel if missing |
| `interrupt_kernel` | `lab_id` | code | |
| `restart_kernel` | `lab_id` | code | |
| `install_packages` | `lab_id`, `packages` | code | returns the tail of pip's output |
| `prepare_lab` | `repo`, `commit` | both | blocks while the terminal asks for approval |
| `run_action` | `lab_id`, `commit`, `action`, `wait_s` | both | returns a run handle and output so far |
| `read_run` | `run_id`, `offset` | both | |
| `stop_run` | `run_id` | both | |
| `get_history` | `lab_id`, `n` | both | see below |

The SDK is `mcp` (`MCPServer` from `mcp.server`), an optional dependency: install it with
`pip install 'agent-runtime[mcp]'`. Nothing else in the runtime needs it, and a learner who only
opens a course page never runs it.

`run_action` must not block until the action finishes. Actions may run up to 7200 seconds
(`labs.py`), and a tool call held open that long will time out somewhere in the client. It returns
after `wait_s` (20 by default) with the run id, the status and the output so far; the model polls
`read_run` with `next_offset`. That is the same shape the course page already uses.

## Rendering what comes back

Three conversions decide whether any of this is usable, and all three belong in the adapter.

**Strip ANSI.** IPython colors its tracebacks, and lab actions run with `FORCE_COLOR=1` and
`CLICOLOR_FORCE=1` on purpose, because the page that started them renders color (`labs.py`). A
model receives escape codes as noise it pays tokens for. Strip them in the adapter rather than
changing what the browser gets.

**Pass images through.** `display_data` messages are captured with their whole `data` dictionary
(`kernels/ipython.py`), so a matplotlib figure already arrives from `/cell/run` as base64
`image/png`. Turning those into MCP image content lets a tutor see the learner's plot, which for a
course is a feature rather than a detail.

Note while reading that code that `/cell/run` returns outputs shaped `{"type": ..., "content":
{...}}`, while `docs/protocol.md` and the streaming endpoint document the flatter nbformat-like
`{"output_type": ..., "text": ...}`. The renderer accepts both, but the two should be reconciled.

**Bound the result.** Cap a tool result at roughly 8–16 KB, keeping the head and tail with a marker
between them. Nothing is lost: the runtime keeps the last 1,000,000 characters of each run and
serves them by offset, so a model that needs more can page through it.

One rule that is easy to get backwards: **a traceback is a successful tool call.** The learner's
code raising is the signal the tutor exists to read, so it comes back as ordinary content with
`success: false`. MCP tool errors are reserved for the runtime being unreachable, a `403`, or a lab
that does not exist.

## Session history

`get_history` has nothing behind it today — the transcript of cells and outputs lives only in the
browser. Add a bounded ring buffer per lab to `KernelManager` (the last ~50 cells: code, outputs,
timestamps) and `GET /labs/{lab_id}/history`. It is the one non-trivial addition on the server
side, and it is what separates a tutor that can run code from a tutor that knows what the learner
has been doing.

Expose it as a tool *and* as a `runtime://labs/{lab_id}/history` resource. A resource is the
semantically correct shape, but many clients surface resources only when the user attaches one by
hand, while tools are called on the model's own initiative. The tool is the one that will be used.

## Layout

```
src/agent_runtime/mcp/
├── __init__.py
├── server.py       # the MCPServer instance and the tool definitions
├── client.py       # RuntimeClient: httpx against 127.0.0.1:9477
└── render.py       # outputs -> MCP content: ANSI stripping, truncation, images
src/agent_runtime/local_tokens.py  # the token store
src/agent_runtime/auth.py          # the local client principal
src/agent_runtime/server.py        # GET /runtime/whoami
src/agent_runtime/cli.py           # the `mcp` and `token` subcommands
```

`mcp` goes in an optional dependency group so the base install stays small. The tool functions take
a client, so they are tested against an `httpx.MockTransport` rather than a live server - including
the invariant that the adapter sends no `Origin` header.

## Security

Stated plainly in [security.md](security.md).

A local client token with the `code` scope **is not a new authority boundary**. Anyone who can read
the token file can already run Python as the user. What the token buys is revocation and an audit
trail, not defense against someone who is already on the machine.

The genuinely new exposure is **prompt injection**. An MCP client is a model reading untrusted
text — course pages, lab output, tracebacks from third-party packages — and then deciding what to
execute. The `actions` scope is a real mitigation here, not a formality: a client holding an
actions-scope token can be talked into running a lab action the user already approved, and nothing
else. So `agent-runtime token create` defaults to `--scope actions`, and `--scope code` is typed
deliberately, against the same blunt warning the pairing prompt prints.

Route every MCP tool call through `events.py` with the principal attached, so that executions by
`local:claude-desktop` are distinguishable from a page's in the log.

## Milestones

1. ~~**Adapter, `run_python`, local client tokens.**~~ Done.
2. **Lab tools and rendering** — the lab tools, `list_labs` and the environments it needs.
3. **History** — the buffer, the endpoint, the tool and the resource.
4. **Streamable HTTP**, if clients ask for it.

`/runtime/info` advertises `local_clients` rather than the `mcp` the design named: the capability
list tells a *page* what the runtime offers, and the MCP server is a separate process rather than
something a page can reach.

## Open questions

- **Elicitation.** MCP can ask the client's user to confirm an action. Approvals live in the
  runtime's terminal today, which is the stronger position — the terminal is the user's, the client
  may be remote. Worth revisiting only if the terminal proves unreachable in practice.
- **Streaming.** `/cell/run/stream` exists; the MCP equivalent is progress notifications. Polling is
  enough for a first version.
- **Whether the adapter may start the runtime.** Convenient, and it puts a process launch behind a
  tool call. Still no: an unreachable runtime returns a message naming `agent-runtime serve`.
- **Audit events.** MCP calls reach the runtime as ordinary HTTP requests and are logged as such,
  with `local:<name>` as the principal, but they do not yet emit their own events.
