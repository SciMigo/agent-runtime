# Agent Runtime Security Model

This document describes the security considerations, threat model, and mitigations for the Agent Runtime.

## Trust Model

### Core Principle

The Agent Runtime is designed to run **on the user's machine** and execute code **on their behalf**. The user is the principal; they decide what origins can connect and what code runs.

### Trust Boundaries

```
┌─────────────────────────────────────────────────────┐
│                   User's Machine                     │
│  ┌─────────────────────────────────────────────┐    │
│  │            Agent Runtime                     │    │
│  │  ┌─────────────────────────────────────┐    │    │
│  │  │         Jupyter Kernel              │    │    │
│  │  │  (unsandboxed, user permissions)     │    │    │
│  │  └─────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────┘    │
│                        ▲                            │
│                        │ localhost only             │
│                        ▼                            │
│  ┌─────────────────────────────────────────────┐    │
│  │              Web Browser                     │    │
│  │         (paired origins only)               │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
                         │
                         │ HTTPS
                         ▼
              ┌─────────────────────┐
              │    Web Application   │
              │  (ai-gateway, etc)   │
              └─────────────────────┘
```

## Threat Model

### In Scope

1. **Unauthorized origin access** - Malicious websites trying to execute code
2. **Token theft** - Stealing pairing tokens from legitimate apps
3. **Code injection** - Injecting malicious code into execution requests
4. **Resource exhaustion** - Denial of service via excessive kernel/env creation

### Out of Scope

1. **Malicious user** - The user themselves running malicious code (they can do this anyway)
2. **Compromised machine** - If the user's machine is compromised, all bets are off
3. **Browser vulnerabilities** - We assume the browser's same-origin policy works

## Security Controls

### 1. Localhost-Only Binding

The runtime binds to `127.0.0.1` by default, preventing remote access.

```python
host: str = "127.0.0.1"  # Never 0.0.0.0 in production
```

**Why**: Prevents network-level attacks. Only local processes can connect.

### 2. Origin Allowlist (Pairing)

Only explicitly approved origins can execute code.

**Pairing Flow:**
1. Unknown origin sends request
2. Runtime displays the exact browser-supplied origin in its terminal
3. User must explicitly approve
4. Token issued for future requests

Non-loopback origins must use HTTPS. Plain HTTP is accepted only for literal
loopback origins.

**Storage**: Paired origins are stored in `~/.agent-runtime/paired_origins.json`

### 3. CORS Configuration

CORS allows HTTP transport from web origins so an unpaired course page can
reach the pairing endpoint. It is not the authorization boundary; protected
routes independently enforce loopback origin trust or an origin-bound token.

```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.+",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

The runtime also returns `Access-Control-Allow-Private-Network: true` when a
browser requests Private Network Access during preflight. Chrome 142 and later
uses a separate Local Network Access permission prompt instead; the learner
must grant that browser permission before pairing can reach the runtime.

### 4. Bearer Token Authentication

After pairing, requests require a bearer token:

```
Authorization: Bearer <token>
```

Tokens are:
- Generated with `secrets.token_urlsafe(32)` (256 bits of entropy)
- Stored as SHA-256 hash (not plaintext)
- Tied to specific origin

### 5. Virtual Environment Isolation

Each lab gets its own virtual environment for dependency management:

```
~/.agent-runtime/envs/lab-{id}/.venv/
```

**Benefits and boundary:**
- Package conflicts don't affect other labs
- Easy cleanup (just delete the directory)
- This is not a security boundary; lab code retains the user's permissions

### 6. Input Validation

All API inputs are validated via Pydantic models:

```python
class ExecuteRequest(BaseModel):
    lab_id: str
    code: str
    cell_id: Optional[str] = None
```

### 7. Loopback TLS Certificate

`agent-runtime tls setup` lets Safari reach the runtime from HTTPS pages (it
blocks `http://127.0.0.1` there as mixed content). The certificate is:

- self-signed and generated on the machine; the private key stays in
  `<runtime dir>/tls/` (directory `0700`, key `0600`) and is never sent anywhere;
- valid only for `127.0.0.1`, `::1` and `localhost`, for server authentication;
- not a certificate authority (`basicConstraints CA:FALSE`, no `keyCertSign`),
  so trusting it cannot make the machine accept a certificate for any other
  name, even if the key were stolen;
- valid for 397 days; `tls setup` renews it within 30 days of expiry.

On macOS it is added to the login keychain with SSL trust only. The HTTPS
listener serves the same app as the HTTP one: pairing and bearer tokens apply
unchanged. `agent-runtime tls remove` removes the trust setting, the keychain
entry and the files.

### 8. Pairing Scopes and Named Lab Actions

A pairing is either `code` (any Python, through kernels) or `actions` (only `/labs`). A page
that runs a lab's prepared commands should pair with `actions`. Then a compromised or malicious
page holding the token can run only commands the user has already approved.

An action runs only if:

- the page named a repository over `https` (no `ssh`, `file` or `ext::` transports, via
  `GIT_ALLOW_PROTOCOL`), without credentials, and a full commit SHA;
- that commit's `lab.toml` declares the action. Commands are argv lists and never go through a
  shell;
- the user approved that origin, repository, commit and manifest, in the terminal, after seeing
  every command;
- no other action of the same lab is running. Actions have a timeout, and Stop kills the whole
  process group.

What approval does and does not mean: it pins **which** commands run and **which code** they
run, down to the commit. It does not sandbox them. Approving `python demo.py` trusts
`demo.py` at that commit, with your permissions, like cloning the repository and running it
yourself. A new commit needs a new approval.

### 9. Local Client Tokens

A process on this machine has no origin to pair, so it authenticates with a token issued by
`agent-runtime token create <name>` and sent without an `Origin` header. Tokens are hashed
before storage in `<runtime dir>/local_clients.json` (mode `0600`), and `agent-runtime token
list` and `token revoke` work against a running runtime, which re-reads the file whenever it
changes.

**What this is not.** A `code`-scope local token is not a boundary against an attacker who can
already run code as the user: they could start a kernel themselves. What it buys is revocation,
a name per client in the logs, and a scope.

**What the scope is for.** The new exposure is prompt injection, not token theft. An MCP client
is a model reading untrusted text - course pages, lab output, tracebacks from third-party
packages - and then deciding what to run. A client holding an `actions`-scope token can be
talked into starting a lab action the user already approved, and nothing else. That is why
`agent-runtime token create` defaults to `actions` and `--scope code` has to be typed.

See [mcp.md](mcp.md).

## Known Limitations

### 1. No Code Sandboxing

The runtime executes arbitrary Python code. There is **no sandbox** preventing:
- File system access
- Network access
- System command execution

**Mitigation**: This is intentional for a local lab runtime. The origin and
pairing controls decide who may request execution; they do not constrain what
approved code can do. Hosted deployments require an external container or VM
security boundary.

### 2. Shared User Permissions

Kernels run with the user's permissions. Code can access anything the user can access.

**Mitigation**: For hosted environments, the runtime runs in a container with restricted permissions.

### 3. Token Storage

Tokens are stored by the web application. If the web app is compromised, tokens could be stolen.

**Mitigation**:
- Tokens are origin-bound (can't be used from other origins)
- Users can revoke pairings: `agent-runtime pairing revoke <origin>`

### 4. Agent Clients Read Untrusted Text

A local client with the `code` scope runs whatever its model decides to run, and that model
reads course pages, lab output and library tracebacks. Treat a `code`-scope token as equivalent
to letting that client type into your terminal, and prefer `actions` where it is enough.

## Hardening Recommendations

### For Local Development

1. **Review pairing requests carefully** - Only approve origins you trust
2. **Revoke unused pairings** - `agent-runtime pairing list` then revoke old ones
3. **Use separate labs** - Don't reuse labs across different purposes

### For Hosted Environments

1. **Run in containers** - Use Docker/Kubernetes with:
   - Read-only root filesystem
   - No privileged mode
   - Resource limits (CPU, memory)
   - Network policies

2. **Drop capabilities** - Minimal Linux capabilities:
   ```
   --cap-drop=ALL --cap-add=NET_BIND_SERVICE
   ```

3. **Use seccomp/AppArmor** - Restrict system calls

4. **Ephemeral environments** - Destroy environments after use

## Incident Response

### Revoking Access

```bash
# List all paired origins
agent-runtime pairing list

# Revoke specific origin
agent-runtime pairing revoke https://suspicious-origin.com

# Nuclear option - delete all pairing data
rm ~/.agent-runtime/paired_origins.json

# Untrust and delete the loopback HTTPS certificate
agent-runtime tls remove

# Forget every approved lab version (the next prepare asks again)
rm ~/.agent-runtime/labs/approvals.json

# List and revoke local clients (MCP servers and other local processes)
agent-runtime token list
agent-runtime token revoke "Claude Desktop"
```

### Cleaning Up Environments

```bash
# List environments
agent-runtime env list

# Delete specific environment
agent-runtime env delete <lab_id>

# Nuclear option - delete all environments
rm -rf ~/.agent-runtime/envs/
```

## Security Checklist

- [ ] Runtime binds to localhost only
- [ ] CORS preflight and endpoint authorization tests pass
- [ ] Pairing requires user approval
- [ ] Tokens are hashed before storage
- [ ] Input validation on all endpoints
- [ ] Environments isolated per lab
- [ ] User can revoke pairings
- [ ] User can revoke local client tokens
- [ ] Local client tokens are hashed, 0600, and re-read while serving
- [ ] User can delete environments

## Reporting Security Issues

If you discover a security vulnerability, please report it privately. Do not open a public issue.

Use [GitHub private vulnerability reporting](https://github.com/SciMigo/agent-runtime/security/advisories/new).
