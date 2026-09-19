# Browser Integration

Agent Runtime exposes a vendor-neutral HTTP API at `http://127.0.0.1:9477` and,
once `agent-runtime tls setup` has run, the same API over HTTPS at
`https://127.0.0.1:9478`. It is intended for browser-based courses and labs
that execute Python on the learner's own computer.

## Connection flow

1. Call `GET /health` to detect the local runtime: `https://127.0.0.1:9478`
   first, then `http://127.0.0.1:9477`. Safari reaches only the HTTPS address
   (see below); Chrome and Firefox reach both.
2. If the page has a loopback origin, call runtime endpoints directly.
3. Otherwise, call `POST /pairing/request` and tell the learner to review the
   approval prompt in the terminal that started Agent Runtime.
4. Save the returned token for the current origin and send it as
   `Authorization: Bearer <token>` on every protected request.
5. Call `POST /cell/run`; it creates the lab environment and kernel when they
   do not exist.

The browser cannot select the origin being approved. The runtime reads the
browser-controlled `Origin` header and binds the token to that exact value.

```javascript
// HTTPS first (the only one Safari allows from an HTTPS page), then HTTP.
async function findRuntime() {
  for (const url of ["https://127.0.0.1:9478", "http://127.0.0.1:9477"]) {
    try {
      if ((await fetch(`${url}/health`)).ok) return url;
    } catch {
      // not listening, blocked by the browser, or the certificate is not trusted
    }
  }
  return null;
}

async function pairRuntime(runtimeUrl) {
  const response = await fetch(`${runtimeUrl}/pairing/request`, { method: "POST" });
  if (!response.ok) {
    throw new Error((await response.json()).detail);
  }
  return (await response.json()).token;
}

async function runCell(runtimeUrl, token, labId, code) {
  const response = await fetch(`${runtimeUrl}/cell/run`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ lab_id: labId, code }),
  });
  if (!response.ok) {
    throw new Error((await response.json()).detail);
  }
  return response.json();
}
```

## Lab pages: named actions instead of code

A course page that only needs to run a lab's prepared commands should not hold a token that can
run any Python. Pair with the `actions` scope and use `/labs`:

1. Find the runtime (HTTPS first, as above) and pair with `{"scope": "actions"}`.
2. `POST /labs/prepare` with the lab repository and the full commit SHA the page was built
   against. The first time, the learner approves the lab in the runtime's terminal, which lists
   every command. Later visits to the same version do not ask again.
3. Show the returned actions as buttons. On click, `POST /labs/runs`, then poll
   `GET /labs/runs/{id}?offset=` every half second, appending `output` and passing back
   `next_offset`, until `status` is not `running`.
4. A Stop button calls `POST /labs/runs/{id}/stop`. After a reload, `GET /labs/runs` finds a
   run that is still going.

```javascript
async function labRequest(runtimeUrl, token, path, body) {
  const response = await fetch(`${runtimeUrl}${path}`, {
    method: body ? "POST" : "GET",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error((await response.json()).detail);
  return response.json();
}

const lab = await labRequest(runtimeUrl, token, "/labs/prepare", { repo, commit });
let run = await labRequest(runtimeUrl, token, "/labs/runs",
  { lab_id: lab.lab_id, commit: lab.commit, action: "stable" });
let offset = 0;
while (run.status === "running") {
  await new Promise((resolve) => setTimeout(resolve, 500));
  run = await labRequest(runtimeUrl, token, `/labs/runs/${run.run_id}?offset=${offset}`);
  output.textContent += run.output;
  offset = run.next_offset;
}
```

## Browser security behavior

Measured on macOS, 2026-09-19, from an HTTPS page on a public origin:

| Browser | `http://127.0.0.1:9477` | `https://127.0.0.1:9478` (after `tls setup`) |
|---|---|---|
| Chrome 152 | works after the Local Network Access prompt | works |
| Firefox 155 | works | works |
| Safari 26.6 | blocked as mixed content (`TypeError: Load failed`) | works |

- **Chrome** (142 and later) asks the learner for Local Network Access
  permission the first time a public site contacts loopback. If the learner
  declines, requests fail with a CORS "Permission was denied" error. Do not pass
  `targetAddressSpace: "local"`: `127.0.0.1` is in the `loopback` address
  space, and Chrome 151 rejects the mismatch ("Request had a target IP address
  space of `local` yet the resource is in address space `loopback`"). No
  annotation is needed.
- **Safari** treats `http://127.0.0.1` as insecure content and blocks it from
  HTTPS pages. `agent-runtime tls setup` creates a certificate for 127.0.0.1,
  ::1 and localhost and trusts it in the login keychain; `agent-runtime serve`
  then also listens on `https://127.0.0.1:9478`.
- Browsers that still use Private Network Access preflights get the
  `Access-Control-Allow-Private-Network` response the runtime sends for
  compatibility.

CORS enables transport; it does not grant execution authority. Protected
endpoints still require either a loopback page origin or a bearer token issued
after explicit local approval.

Do not start the runtime with `--no-pairing` for a public course page. That flag
is only for controlled local development.

## Environment behavior

`POST /cell/run` starts a missing environment and kernel automatically.
`POST /kernel/start` does the same unless its request explicitly contains
`"create_env": false`. In that case a missing environment returns `404` with
instructions instead of exposing a Jupyter kernelspec error.

## Deployment boundary

This repository implements a local runtime. It does not provide a hosted
sandbox. If you deploy it on a server, add an isolation boundary such as a
locked-down container or virtual machine and enforce resource and network
limits outside this process.
