"""An HTTP client for a runtime that is already serving on loopback.

The MCP adapter owns no kernels, environments or runs; it asks the runtime process that does.
That is the point: a second process importing `kernel_manager` would get its own kernels, so a
tutor would not see the cell the learner just ran in the browser (see `docs/mcp.md`).

This client is not a browser. It sends no `Origin` header, which is exactly how the runtime
tells it apart from a page: it authenticates with a local client token instead of a paired
origin's (see `agent_runtime.local_tokens`).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from agent_runtime.config import settings

DEFAULT_TIMEOUT = 120.0


class RuntimeUnavailableError(Exception):
    """No runtime is listening."""


class RuntimeRejectedError(Exception):
    """The runtime answered, and said no."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def default_base_url() -> str:
    """Where the runtime is expected to be listening."""
    from_env = os.environ.get("AGENT_RUNTIME_URL")
    if from_env:
        return from_env.rstrip("/")
    return f"http://{settings.host}:{settings.port}"


class RuntimeClient:
    """Calls the localhost protocol on behalf of an MCP client."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def request(self, method: str, path: str, json: Any = None) -> Any:
        """Call the runtime, or raise RuntimeUnavailableError / RuntimeRejectedError."""
        try:
            response = await self._http().request(method, path, json=json)
        except httpx.RequestError as error:
            raise RuntimeUnavailableError(str(error)) from error

        if response.status_code >= 400:
            raise RuntimeRejectedError(response.status_code, _detail(response))

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def get(self, path: str) -> Any:
        return await self.request("GET", path)

    async def post(self, path: str, json: Any = None) -> Any:
        return await self.request("POST", path, json=json)

    async def whoami(self) -> dict[str, str] | None:
        """The principal and scope this client holds, or None if the runtime cannot say."""
        try:
            identity = await self.get("/runtime/whoami")
        except (RuntimeUnavailableError, RuntimeRejectedError):
            return None
        return identity if isinstance(identity, dict) else None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _detail(response: httpx.Response) -> str:
    """The runtime's own error message, which is worth more than the status code alone."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return response.text.strip() or f"HTTP {response.status_code}"
