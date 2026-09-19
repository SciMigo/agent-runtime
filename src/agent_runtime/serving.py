"""Run the app on one or two loopback listeners: HTTP always, HTTPS when a certificate exists.

Both listeners serve the same app object, so pairing state and kernels are shared. uvicorn
servers each install their own signal handlers, and the last one installed would win, leaving
the other running after Ctrl-C. So the servers here skip that, and one handler stops both.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Generator, Sequence
from dataclasses import dataclass

import uvicorn
from starlette.types import ASGIApp


class _SharedShutdownServer(uvicorn.Server):
    """A uvicorn server whose shutdown is driven by `serve_all`'s signal handler."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:  # uvicorn >= 0.29
        yield

    def install_signal_handlers(self) -> None:  # uvicorn < 0.29
        return None


@dataclass(frozen=True)
class Listener:
    url: str
    server: uvicorn.Server


def build_listeners(
    app: ASGIApp,
    host: str,
    port: int,
    https_port: int | None = None,
    certfile: str | None = None,
    keyfile: str | None = None,
    log_level: str = "info",
) -> list[Listener]:
    """HTTP on `port`, plus HTTPS on `https_port` when a certificate and key are given."""
    listeners = [
        Listener(
            url=f"http://{host}:{port}",
            server=_SharedShutdownServer(
                uvicorn.Config(app, host=host, port=port, log_level=log_level)
            ),
        )
    ]
    if https_port is not None and certfile and keyfile:
        https = uvicorn.Config(
            app,
            host=host,
            port=https_port,
            ssl_certfile=certfile,
            ssl_keyfile=keyfile,
            log_level=log_level,
            # One lifespan per process: the HTTP listener starts and stops the kernels.
            lifespan="off",
        )
        listeners.append(
            Listener(url=f"https://{host}:{https_port}", server=_SharedShutdownServer(https))
        )
    return listeners


def request_shutdown(listeners: Sequence[Listener]) -> None:
    """First call: finish in-flight requests and stop. Second call: stop now."""
    for listener in listeners:
        if listener.server.should_exit:
            listener.server.force_exit = True
        listener.server.should_exit = True


async def serve_all(listeners: Sequence[Listener]) -> None:
    """Serve every listener until SIGINT or SIGTERM; a failure in one stops the others."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not available on Windows or outside the main thread; Ctrl-C then stops the process.
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, request_shutdown, listeners)
    tasks = [asyncio.ensure_future(listener.server.serve()) for listener in listeners]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        request_shutdown(listeners)
        await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            task.result()
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)
