"""Tests for the MCP server: what a client gets back, and what it is told when a call fails."""

from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_runtime.mcp import server
from agent_runtime.mcp.client import RuntimeClient

CELL_OK = {
    "cell_id": "c1",
    "success": True,
    "outputs": [{"type": "stream", "content": {"name": "stdout", "text": "hello\n"}}],
    "error": None,
    "execution_count": 1,
}


def runtime(
    handler: Callable[[httpx.Request], httpx.Response], token: str | None = "t"
) -> RuntimeClient:
    return RuntimeClient(
        base_url="http://127.0.0.1:9477",
        token=token,
        transport=httpx.MockTransport(handler),
    )


def answering(response: httpx.Response, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return response

    return handler


class TestRunPython:
    async def test_output_comes_back_as_content(self):
        ok = answering(httpx.Response(200, json=CELL_OK))
        with patch.object(server, "_runtime", runtime(ok)):
            blocks = await server.run_python("demo", "print('hello')")

        assert [b.type for b in blocks] == ["text"]
        assert blocks[0].text == "hello"

    async def test_the_code_reaches_the_runtime_without_an_origin(self):
        seen: list[httpx.Request] = []
        handler = answering(httpx.Response(200, json=CELL_OK), seen)

        with patch.object(server, "_runtime", runtime(handler)):
            await server.run_python("demo", "print('hello')")

        request = seen[0]
        assert request.url.path == "/cell/run"
        # Not a browser: the runtime tells local clients apart by the absence of this header.
        assert "origin" not in {k.lower() for k in request.headers}
        assert request.headers["authorization"] == "Bearer t"

    async def test_an_impossible_lab_id_is_refused_before_the_call(self):
        seen: list[httpx.Request] = []
        handler = answering(httpx.Response(200, json=CELL_OK), seen)

        with patch.object(server, "_runtime", runtime(handler)):
            with pytest.raises(ToolError, match="lab_id"):
                await server.run_python("../etc/passwd", "1")

        assert seen == []

    async def test_a_traceback_is_a_result_not_a_tool_failure(self):
        failed = {
            "cell_id": "c1",
            "success": False,
            "outputs": [],
            "error": {"ename": "ValueError", "evalue": "bad", "traceback": ["ValueError: bad"]},
        }

        with patch.object(server, "_runtime", runtime(answering(httpx.Response(200, json=failed)))):
            blocks = await server.run_python("demo", "raise ValueError('bad')")

        assert "ValueError: bad" in blocks[0].text


class TestFailures:
    async def test_a_runtime_that_is_not_running_says_how_to_start_it(self):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with patch.object(server, "_runtime", runtime(refuse)):
            with pytest.raises(ToolError, match="agent-runtime serve"):
                await server.run_python("demo", "1 + 1")

    async def test_an_unknown_token_says_how_to_get_one(self):
        denied = httpx.Response(401, json={"detail": "Unknown local client token."})

        with patch.object(server, "_runtime", runtime(answering(denied))):
            with pytest.raises(ToolError, match="token create"):
                await server.run_python("demo", "1 + 1")

    async def test_a_narrow_scope_explains_the_widening(self):
        detail = "This client is paired for 'actions' only; running code needs 'code'"
        denied = httpx.Response(403, json={"detail": detail})

        with patch.object(server, "_runtime", runtime(answering(denied))):
            with pytest.raises(ToolError, match="--scope code"):
                await server.run_python("demo", "1 + 1")

    async def test_other_refusals_keep_the_runtime_s_own_words(self):
        denied = httpx.Response(500, json={"detail": "kernel died"})

        with patch.object(server, "_runtime", runtime(answering(denied))):
            with pytest.raises(ToolError, match="kernel died"):
                await server.run_python("demo", "1 + 1")


class TestWhoami:
    async def test_it_reports_the_principal_and_scope(self):
        identity = httpx.Response(200, json={"principal": "local:tutor", "scope": "code"})
        client = runtime(answering(identity))

        assert await client.whoami() == {"principal": "local:tutor", "scope": "code"}

    async def test_it_stays_quiet_when_the_runtime_cannot_answer(self):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        assert await runtime(refuse).whoami() is None


class TestToolSurface:
    async def test_one_tool_is_published_whatever_the_scope(self):
        tools = await server.mcp.list_tools()

        assert [tool.name for tool in tools] == ["run_python"]
        assert "persistent" in (tools[0].description or "")
