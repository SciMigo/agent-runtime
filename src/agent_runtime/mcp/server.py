"""The MCP server: an adapter between an MCP client and a runtime serving on loopback.

Step 1 of `docs/mcp.md`: one tool, `run_python`. The lab tools arrive with step 2.

Two decisions worth knowing when reading this:

- The tool list does not depend on the token's scope. The design considered registering only
  the tools a token can use, but a client asks for the list once, at connect time, when the
  runtime may not be running yet. A list that varies with whether the daemon happened to be up
  is worse than a stable one, so a call that the scope forbids fails with a message saying so.
- Nothing is called at import. An MCP client starts this process when it starts itself, often
  long before the user asks for anything.

On stdio, stdout carries the protocol: log through `logging`, which writes to stderr.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ContentBlock

from agent_runtime import __version__
from agent_runtime.envs import LAB_ID_PATTERN
from agent_runtime.mcp.client import RuntimeClient, RuntimeRejectedError, RuntimeUnavailableError
from agent_runtime.mcp.render import render_execution

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Runs code in a learner's lab through an Agent Runtime on this machine.

A lab is a Python environment with a persistent kernel: variables, imports and open files
survive between calls, like cells in a notebook. Labs are separate from each other.

Nothing here is sandboxed. Code runs with the user's own permissions, on their own files, so
treat it as you would treat typing into their terminal."""

mcp: MCPServer = MCPServer(
    "agent-runtime",
    title="Agent Runtime",
    version=__version__,
    instructions=INSTRUCTIONS,
)

_runtime: RuntimeClient | None = None


def _client() -> RuntimeClient:
    if _runtime is None:  # pragma: no cover - only reachable if a tool is called before serve()
        raise ToolError("This MCP server was not started by 'agent-runtime mcp'.")
    return _runtime


async def _call(method: str, path: str, json: Any = None) -> Any:
    """Call the runtime, turning its refusals into something a model can act on."""
    client = _client()
    try:
        return await client.request(method, path, json=json)
    except RuntimeUnavailableError as error:
        logger.info("runtime unavailable at %s: %s", client.base_url, error)
        raise ToolError(
            f"No Agent Runtime is listening at {client.base_url}. "
            "Ask the user to start it with 'agent-runtime serve'."
        ) from error
    except RuntimeRejectedError as error:
        logger.info("runtime rejected %s %s: %s", method, path, error.detail)
        raise ToolError(_rejection(error)) from error


def _rejection(error: RuntimeRejectedError) -> str:
    if error.status == 401:
        return (
            f"{error.detail} The user issues one with "
            "'agent-runtime token create <name> --scope code' and puts it in this client's "
            "configuration as AGENT_RUNTIME_TOKEN."
        )
    if error.status == 403:
        return (
            f"{error.detail}. The user can widen it with "
            "'agent-runtime token create <name> --scope code', which replaces the token."
        )
    return error.detail


@mcp.tool()
async def run_python(lab_id: str, code: str) -> list[ContentBlock]:
    """Run Python in a lab's kernel on the user's machine, and return what it printed.

    The kernel is persistent, so state survives between calls: variables stay bound, imports
    stay imported, and a name defined in an earlier call can be used in a later one. Figures
    come back as images.

    A traceback is a normal result, not an error of this tool - read it and say what went
    wrong. The first call for a lab can take several seconds, because its virtual environment
    and kernel are created then.

    Args:
        lab_id: which lab (session) to run in. Ask the user if you do not know it.
        code: Python source, as you would type into a notebook cell.
    """
    if re.fullmatch(LAB_ID_PATTERN, lab_id) is None:
        raise ToolError(
            "lab_id must be 1-64 letters, digits, dots, underscores or hyphens, "
            "starting with a letter or digit."
        )

    result = await _call("POST", "/cell/run", {"lab_id": lab_id, "code": code})
    if not isinstance(result, dict):
        raise ToolError("The runtime returned something other than an execution result.")
    return render_execution(result)


def serve(base_url: str | None = None, token: str | None = None) -> None:
    """Run the MCP server on stdio until the client disconnects."""
    global _runtime

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _runtime = RuntimeClient(base_url=base_url, token=token)
    logger.info("agent-runtime MCP server talking to %s", _runtime.base_url)
    if token is None:
        logger.info(
            "No token set. Loopback pages are trusted without one, but this process is not a "
            "page: set AGENT_RUNTIME_TOKEN unless the runtime runs with --no-pairing."
        )
    mcp.run(transport="stdio")
