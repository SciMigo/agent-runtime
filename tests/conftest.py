"""Pytest configuration and fixtures for agent-runtime tests."""

import asyncio
import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Set test environment before importing app modules
os.environ["AGENT_RUNTIME_REQUIRE_PAIRING"] = "false"
os.environ["AGENT_RUNTIME_DEBUG"] = "false"


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_runtime_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary runtime directory for tests."""
    runtime_dir = tmp_path / ".agent-runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "envs").mkdir()

    with patch.dict(os.environ, {"AGENT_RUNTIME_RUNTIME_DIR": str(runtime_dir)}):
        yield runtime_dir


@pytest.fixture
def mock_settings(temp_runtime_dir: Path):
    """Create mock settings pointing to temp directory."""
    from agent_runtime.config import Settings

    test_settings = Settings(
        runtime_dir=temp_runtime_dir,
        require_pairing=False,
        debug=False,
    )

    with patch("agent_runtime.config.settings", test_settings):
        with patch("agent_runtime.auth.settings", test_settings):
            with patch("agent_runtime.envs.settings", test_settings):
                yield test_settings


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Create a synchronous test client with fresh app."""
    # Import fresh to avoid middleware issues
    from agent_runtime import __version__
    from agent_runtime.api import execute, health, kernel

    # Create a fresh FastAPI app for testing
    test_app = FastAPI(
        title="Agent Runtime Test",
        version=__version__,
    )

    # Include routers
    test_app.include_router(health.router, tags=["health"])
    test_app.include_router(kernel.router, prefix="/kernel", tags=["kernel"])
    test_app.include_router(execute.router, tags=["execute"])

    @test_app.get("/runtime/info")
    async def runtime_info() -> dict:
        from agent_runtime import __protocol_version__, __version__

        return {
            "runtime_version": __version__,
            "protocol_version": __protocol_version__,
            "capabilities": ["python", "jupyter", "local_fs"],
        }

    # Mock auth to always pass
    with patch("agent_runtime.api.kernel.require_auth", return_value="http://localhost:3000"):
        with patch("agent_runtime.api.execute.require_auth", return_value="http://localhost:3000"):
            with TestClient(test_app) as client:
                yield client


@pytest.fixture
def mock_kernel():
    """Create a mock IPython kernel."""
    kernel = MagicMock()
    kernel.is_ready = True
    kernel.kernel_name = "test-kernel"

    async def mock_execute(code, cell_id=None):
        from agent_runtime.kernels.ipython import ExecutionResult

        return ExecutionResult(
            cell_id=cell_id or "test-cell",
            success=True,
            outputs=[{"output_type": "execute_result", "data": {"text/plain": "42"}}],
            error=None,
            execution_count=1,
        )

    kernel.execute = mock_execute

    async def mock_start():
        pass

    async def mock_shutdown():
        pass

    async def mock_restart():
        pass

    async def mock_interrupt():
        pass

    kernel.start = mock_start
    kernel.shutdown = mock_shutdown
    kernel.restart = mock_restart
    kernel.interrupt = mock_interrupt

    return kernel


@pytest.fixture
def mock_kernel_manager(mock_kernel):
    """Create a mock kernel manager."""
    from agent_runtime.kernels.manager import KernelManager

    manager = KernelManager()
    manager._kernels["test-lab"] = mock_kernel

    return manager
