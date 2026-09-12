# Browser Integration

Agent Runtime exposes a vendor-neutral HTTP API at `http://127.0.0.1:9477`.
It is intended for browser-based courses and labs that execute Python on the
learner's own computer.

## Connection flow

1. Call `GET /health` to detect the local runtime.
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
const runtimeUrl = "http://127.0.0.1:9477";

async function pairRuntime() {
  const response = await fetch(`${runtimeUrl}/pairing/request`, {
    method: "POST",
    targetAddressSpace: "local",
  });
  if (!response.ok) {
    throw new Error((await response.json()).detail);
  }
  return (await response.json()).token;
}

async function runCell(token, labId, code) {
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

## Browser security behavior

Chrome 142 and later asks the learner for Local Network Access permission when
a public site first contacts loopback. The `targetAddressSpace: "local"`
annotation declares that intent. Other browsers may still use Private Network
Access preflights, which the runtime answers for compatibility.

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
