"""Turn a kernel's outputs into content an MCP client can read.

Three conversions decide whether any of this is usable, and all three belong here rather than
in the runtime, which serves a browser (see `docs/mcp.md`):

- **ANSI.** IPython colors its tracebacks, and lab actions run with `FORCE_COLOR=1` because the
  page that started them renders color. A model reads escape codes as noise it pays for.
- **Images.** A figure arrives as base64 `image/png` in a `display_data` output. Passed through
  as image content, a tutor can see the learner's plot.
- **Size.** A tool result is capped, keeping the head and tail, because an accidental
  `print(range(10**6))` should not fill the client's context.

Kernel outputs come in two shapes: `{"type": ..., "content": {...}}` from `/cell/run`, and the
flatter `{"output_type": ..., "text": ...}` of the streaming endpoint and `docs/protocol.md`.
Both are accepted.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.types import ContentBlock, ImageContent, TextContent

# CSI sequences, which is what colored terminal output and IPython tracebacks contain.
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

MAX_TEXT_CHARS = 12_000
MAX_IMAGES = 4
IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def strip_ansi(text: str) -> str:
    """Remove terminal color codes."""
    return ANSI_PATTERN.sub("", text)


def truncate(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Keep the head and the tail, which is where the answer usually is."""
    if len(text) <= limit:
        return text
    head = (limit * 2) // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n[... {omitted:,} characters omitted ...]\n\n{text[-tail:]}"


def render_execution(result: dict[str, Any]) -> list[ContentBlock]:
    """Render a `/cell/run` response.

    A traceback is not a failure of this tool: the learner's code raising is the thing a tutor
    exists to read, so it comes back as ordinary content.
    """
    texts: list[str] = []
    images: list[ImageContent] = []

    for output in result.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        kind = str(output.get("type") or output.get("output_type") or "")
        content = output.get("content")
        body: dict[str, Any] = content if isinstance(content, dict) else output

        if kind == "stream":
            texts.append(str(body.get("text", "")))
        elif kind in ("result", "execute_result", "display_data"):
            data = body.get("data")
            if not isinstance(data, dict):
                continue
            image = _image(data)
            if image is not None:
                images.append(image)
            else:
                texts.append(_plain_text(data))
        elif kind == "error":
            texts.append(_error_text(body))

    error = result.get("error")
    if isinstance(error, dict):
        texts.append(_error_text(error))

    blocks: list[ContentBlock] = []
    text = strip_ansi("".join(texts)).strip()
    if text:
        blocks.append(TextContent(type="text", text=truncate(text)))

    dropped = max(0, len(images) - MAX_IMAGES)
    blocks.extend(images[:MAX_IMAGES])
    if dropped:
        blocks.append(TextContent(type="text", text=f"[{dropped} further image(s) not shown]"))

    if not blocks:
        # Content of some kind, always: an empty result reads like a broken tool.
        count = result.get("execution_count")
        ran = f" (execution count {count})" if count is not None else ""
        blocks.append(TextContent(type="text", text=f"The code ran and produced no output{ran}."))

    return blocks


def _image(data: dict[str, Any]) -> ImageContent | None:
    for mime in IMAGE_MIME_TYPES:
        payload = data.get(mime)
        if isinstance(payload, str) and payload.strip():
            # Jupyter wraps base64 payloads; a client wants them unwrapped.
            return ImageContent(
                type="image",
                data="".join(payload.split()),
                mime_type=mime,
            )
    return None


def _plain_text(data: dict[str, Any]) -> str:
    """The text of a result, preferring the representation meant for reading."""
    for mime in ("text/plain", "text/markdown"):
        value = data.get(mime)
        if isinstance(value, str):
            return value if value.endswith("\n") else value + "\n"
    for mime, value in data.items():
        if isinstance(value, str) and mime.startswith("text/"):
            return value if value.endswith("\n") else value + "\n"
    return ""


def _error_text(error: dict[str, Any]) -> str:
    """A traceback if the kernel sent one, else the exception line."""
    traceback = error.get("traceback")
    if isinstance(traceback, list) and traceback:
        return "\n".join(str(line) for line in traceback) + "\n"
    ename = str(error.get("ename", "Error"))
    evalue = str(error.get("evalue", ""))
    return f"{ename}: {evalue}\n" if evalue else f"{ename}\n"
