"""Tests for running the HTTP and HTTPS listeners together (agent_runtime.serving, `serve`)."""

import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agent_runtime import tls
from agent_runtime.serving import build_listeners, request_shutdown


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(port: int, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"nothing listening on {port}")


class TestBuildListeners:
    def test_http_only_without_a_certificate(self):
        listeners = build_listeners(FastAPI(), "127.0.0.1", 9477, https_port=9478)
        assert [listener.url for listener in listeners] == ["http://127.0.0.1:9477"]

    def test_https_shares_the_app_but_not_the_lifespan(self, tmp_path: Path):
        info = tls.generate(tmp_path / "c.pem", tmp_path / "k.pem")
        app = FastAPI()
        listeners = build_listeners(
            app,
            "127.0.0.1",
            9477,
            https_port=9478,
            certfile=str(info.cert_path),
            keyfile=str(info.key_path),
        )
        http, https = (listener.server.config for listener in listeners)
        assert [listener.url for listener in listeners] == [
            "http://127.0.0.1:9477",
            "https://127.0.0.1:9478",
        ]
        assert http.app is app and https.app is app
        assert https.ssl_certfile == str(info.cert_path) and https.lifespan == "off"
        assert http.lifespan != "off"

    def test_second_shutdown_request_forces_exit(self):
        listeners = build_listeners(FastAPI(), "127.0.0.1", 9477)
        request_shutdown(listeners)
        assert listeners[0].server.should_exit and not listeners[0].server.force_exit
        request_shutdown(listeners)
        assert listeners[0].server.force_exit


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_serve_answers_on_http_and_verified_https_and_stops_on_ctrl_c(tmp_path: Path):
    """The real command: both listeners up, HTTPS verifies for 127.0.0.1, one SIGINT stops both."""
    runtime_dir = tmp_path / "runtime"
    tls_dir = runtime_dir / "tls"
    info = tls.generate(tls_dir / "localhost.pem", tls_dir / "localhost-key.pem")
    http_port, https_port = _free_port(), _free_port()
    env = dict(
        os.environ,
        AGENT_RUNTIME_RUNTIME_DIR=str(runtime_dir),
        AGENT_RUNTIME_REQUIRE_PAIRING="true",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_runtime.cli", "serve"]
        + ["--port", str(http_port), "--https-port", str(https_port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for(http_port)
        _wait_for(https_port)
        assert httpx.get(f"http://127.0.0.1:{http_port}/health").status_code == 200
        trusted = ssl.create_default_context(cafile=str(info.cert_path))
        for host in ("127.0.0.1", "localhost"):
            response = httpx.get(f"https://{host}:{https_port}/health", verify=trusted)
            assert response.status_code == 200
        info_response = httpx.get(f"https://127.0.0.1:{https_port}/runtime/info", verify=trusted)
        assert info_response.json()["runtime_version"]
        # Pairing is enforced on the HTTPS listener exactly as on HTTP.
        denied = httpx.post(
            f"https://127.0.0.1:{https_port}/kernel/start",
            json={"lab_id": "x"},
            headers={"Origin": "https://example.com"},
            verify=trusted,
        )
        assert denied.status_code == 401
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            output, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
            pytest.fail(f"serve did not stop on SIGINT:\n{output}")
    assert proc.returncode == 0, output
    assert f"https://127.0.0.1:{https_port}" in output
