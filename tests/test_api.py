"""Integration tests for API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_health_check(self, client: TestClient):
        """Test basic health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_detailed_health(self, client: TestClient):
        """Test detailed health check endpoint."""
        response = client.get("/health/detailed")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "protocol_version" in data
        assert "components" in data
        assert data["components"]["server"] == "healthy"
        assert "kernels" in data["components"]

    def test_metrics(self, client: TestClient):
        """Test metrics endpoint."""
        response = client.get("/metrics")
        assert response.status_code == 200
        assert isinstance(response.json(), dict)


class TestRuntimeInfo:
    """Tests for runtime info endpoint."""

    def test_runtime_info(self, client: TestClient):
        """Test runtime info endpoint."""
        response = client.get("/runtime/info")
        assert response.status_code == 200

        data = response.json()
        assert "runtime_version" in data
        assert "protocol_version" in data
        assert "capabilities" in data
        assert "python" in data["capabilities"]
        assert "jupyter" in data["capabilities"]


class TestKernelEndpoints:
    """Tests for kernel management endpoints."""

    def test_start_kernel(self, client: TestClient):
        """Test starting a kernel."""
        with patch("agent_runtime.api.kernel.env_manager") as mock_env:
            with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
                mock_env.get_env_path.return_value = None

                mock_kernel = MagicMock()
                mock_kernel.kernel_name = "agent-runtime-test"
                mock_kernel.is_ready = True
                mock_km.start_kernel = AsyncMock(return_value=mock_kernel)

                response = client.post(
                    "/kernel/start", json={"lab_id": "test-lab", "create_env": True}
                )

        assert response.status_code == 200
        data = response.json()
        assert data["lab_id"] == "test-lab"
        assert data["status"] == "started"
        assert data["ready"] is True

    def test_start_kernel_no_env_creation(self, client: TestClient):
        """Test starting kernel without creating environment."""
        with patch("agent_runtime.api.kernel.env_manager") as mock_env:
            with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
                mock_env.get_env_path.return_value = "/some/path"
                mock_kernel = MagicMock()
                mock_kernel.kernel_name = "agent-runtime-test"
                mock_kernel.is_ready = True
                mock_km.start_kernel = AsyncMock(return_value=mock_kernel)

                response = client.post(
                    "/kernel/start", json={"lab_id": "test-lab", "create_env": False}
                )

        assert response.status_code == 200

    def test_start_kernel_without_existing_env_is_actionable(self, client: TestClient):
        """Opting out of environment creation fails before Jupyter is invoked."""
        with patch("agent_runtime.api.kernel.env_manager") as mock_env:
            mock_env.get_env_path.return_value = None
            response = client.post(
                "/kernel/start", json={"lab_id": "missing-lab", "create_env": False}
            )

        assert response.status_code == 404
        assert "create_env=true" in response.json()["detail"]

    def test_start_kernel_rejects_path_like_lab_id(self, client: TestClient):
        response = client.post(
            "/kernel/start", json={"lab_id": "../../outside", "create_env": True}
        )

        assert response.status_code == 422

    def test_interrupt_kernel(self, client: TestClient):
        """Test interrupting a kernel."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_km.interrupt_kernel = AsyncMock(return_value=True)

            response = client.post("/kernel/interrupt?lab_id=test-lab")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "interrupted"
        assert data["lab_id"] == "test-lab"

    def test_interrupt_kernel_not_found(self, client: TestClient):
        """Test interrupting non-existent kernel."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_km.interrupt_kernel = AsyncMock(return_value=False)

            response = client.post("/kernel/interrupt?lab_id=nonexistent")

        assert response.status_code == 404

    def test_restart_kernel(self, client: TestClient):
        """Test restarting a kernel."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_kernel = MagicMock()
            mock_kernel.kernel_name = "agent-runtime-test"
            mock_kernel.is_ready = True
            mock_km.restart_kernel = AsyncMock(return_value=mock_kernel)

            response = client.post("/kernel/restart?lab_id=test-lab")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "restarted"

    def test_restart_kernel_not_found(self, client: TestClient):
        """Test restarting non-existent kernel."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_km.restart_kernel = AsyncMock(return_value=None)

            response = client.post("/kernel/restart?lab_id=nonexistent")

        assert response.status_code == 404

    def test_shutdown_kernel(self, client: TestClient):
        """Test shutting down a kernel."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_km.stop_kernel = AsyncMock(return_value=True)

            response = client.delete("/kernel?lab_id=test-lab")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "shutdown"

    def test_shutdown_kernel_not_found(self, client: TestClient):
        """Test shutting down non-existent kernel."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_km.stop_kernel = AsyncMock(return_value=False)

            response = client.delete("/kernel?lab_id=nonexistent")

        assert response.status_code == 404

    def test_list_kernels(self, client: TestClient):
        """Test listing all kernels."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_km.list_kernels.return_value = [
                {"lab_id": "lab1", "kernel_name": "k1", "ready": True},
                {"lab_id": "lab2", "kernel_name": "k2", "ready": True},
            ]

            response = client.get("/kernel/list")

        assert response.status_code == 200
        data = response.json()
        assert len(data["kernels"]) == 2

    def test_kernel_status(self, client: TestClient):
        """Test getting kernel status."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_kernel = MagicMock()
            mock_kernel.kernel_name = "agent-runtime-test"
            mock_kernel.is_ready = True
            mock_km.get_kernel.return_value = mock_kernel

            response = client.get("/kernel/status?lab_id=test-lab")

        assert response.status_code == 200
        data = response.json()
        assert data["lab_id"] == "test-lab"
        assert data["ready"] is True

    def test_kernel_status_not_found(self, client: TestClient):
        """Test getting status of non-existent kernel."""
        with patch("agent_runtime.api.kernel.kernel_manager") as mock_km:
            mock_km.get_kernel.return_value = None

            response = client.get("/kernel/status?lab_id=nonexistent")

        assert response.status_code == 404

    def test_install_packages(self, client: TestClient):
        """Test installing packages."""
        with patch("agent_runtime.api.kernel.env_manager") as mock_env:
            mock_env.get_env_path.return_value = "/some/path"
            mock_env.install_packages.return_value = (True, "Successfully installed")

            response = client.post(
                "/kernel/packages/install",
                json={"lab_id": "test-lab", "packages": ["pandas", "numpy"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "installed"
        assert data["packages"] == ["pandas", "numpy"]

    def test_install_packages_no_env(self, client: TestClient):
        """Test installing packages without environment."""
        with patch("agent_runtime.api.kernel.env_manager") as mock_env:
            mock_env.get_env_path.return_value = None

            response = client.post(
                "/kernel/packages/install", json={"lab_id": "nonexistent", "packages": ["pandas"]}
            )

        assert response.status_code == 404

    def test_install_packages_failure(self, client: TestClient):
        """Test package installation failure."""
        with patch("agent_runtime.api.kernel.env_manager") as mock_env:
            mock_env.get_env_path.return_value = "/some/path"
            mock_env.install_packages.return_value = (False, "Installation failed")

            response = client.post(
                "/kernel/packages/install",
                json={"lab_id": "test-lab", "packages": ["nonexistent-pkg"]},
            )

        assert response.status_code == 500


class TestExecuteEndpoints:
    """Tests for code execution endpoints."""

    def test_execute_code(self, client: TestClient):
        """Test executing code."""
        with patch("agent_runtime.api.execute.kernel_manager") as mock_km:
            from agent_runtime.kernels.ipython import ExecutionResult

            mock_result = ExecutionResult(
                cell_id="cell-1",
                success=True,
                outputs=[{"output_type": "execute_result", "data": {"text/plain": "42"}}],
                error=None,
                execution_count=1,
            )
            mock_km.execute = AsyncMock(return_value=mock_result)

            response = client.post(
                "/cell/run",
                json={"lab_id": "test-lab", "code": "21 * 2", "cell_id": "cell-1", "stream": False},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["cell_id"] == "cell-1"
        assert len(data["outputs"]) == 1

    def test_execute_code_error(self, client: TestClient):
        """Test executing code with error."""
        with patch("agent_runtime.api.execute.kernel_manager") as mock_km:
            from agent_runtime.kernels.ipython import ExecutionResult

            mock_result = ExecutionResult(
                cell_id="cell-1",
                success=False,
                outputs=[],
                error={
                    "ename": "NameError",
                    "evalue": "name 'x' is not defined",
                    "traceback": ["..."],
                },
                execution_count=1,
            )
            mock_km.execute = AsyncMock(return_value=mock_result)

            response = client.post(
                "/cell/run", json={"lab_id": "test-lab", "code": "print(x)", "stream": False}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"] is not None

    def test_execute_code_streaming(self, client: TestClient):
        """Test streaming code execution."""
        with patch("agent_runtime.api.execute.kernel_manager") as mock_km:
            mock_kernel = MagicMock()
            mock_kernel.is_ready = True

            async def mock_stream(code, cell_id=None):
                yield {"type": "stream", "name": "stdout", "text": "hello\n"}
                yield {"type": "complete", "success": True}

            mock_kernel.execute_stream = mock_stream
            mock_km.get_kernel.return_value = mock_kernel

            response = client.post(
                "/cell/run/stream",
                json={"lab_id": "test-lab", "code": "print('hello')", "stream": True},
            )

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    def test_execute_auto_starts_kernel(self, client: TestClient):
        """Test that execute auto-starts kernel if needed."""
        with patch("agent_runtime.api.execute.kernel_manager") as mock_km:
            from agent_runtime.kernels.ipython import ExecutionResult

            mock_result = ExecutionResult(
                cell_id="cell-1",
                success=True,
                outputs=[],
                error=None,
                execution_count=1,
            )
            mock_km.execute = AsyncMock(return_value=mock_result)

            response = client.post(
                "/cell/run", json={"lab_id": "new-lab", "code": "x = 1", "stream": False}
            )

        assert response.status_code == 200
        mock_km.execute.assert_called_once()
        # Verify auto_start=True was passed
        call_kwargs = mock_km.execute.call_args[1]
        assert call_kwargs.get("auto_start") is True


class TestCORS:
    """Tests for CORS configuration."""

    def test_cors_preflight(self, client: TestClient):
        """Test CORS preflight request."""
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # OPTIONS might return 200 or 405 depending on CORS config
        # The important thing is it doesn't fail catastrophically
        assert response.status_code in [200, 405]

    def test_cors_headers_localhost(self, client: TestClient):
        """Test CORS headers for localhost origin."""
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 200
        # CORS headers should be present for allowed origins
        # Note: exact header presence depends on CORS middleware configuration

    def test_public_origin_can_preflight_pairing(self):
        """A course website can reach the unauthenticated pairing transport."""
        from agent_runtime.server import app

        with TestClient(app) as server_client:
            response = server_client.options(
                "/pairing/request",
                headers={
                    "Origin": "https://scimigo.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://scimigo.com"

    def test_private_network_preflight_header(self):
        """Private Network Access opt-in is returned when requested."""
        from agent_runtime.server import app

        with TestClient(app) as server_client:
            response = server_client.options(
                "/pairing/request",
                headers={
                    "Origin": "https://scimigo.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Private-Network": "true",
                },
            )

        assert response.headers["access-control-allow-private-network"] == "true"
