"""Tests for kernel manager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_runtime.kernels.manager import KernelManager


class TestKernelManager:
    """Tests for KernelManager class."""

    @pytest.fixture
    def manager(self):
        """Create a fresh kernel manager for each test."""
        return KernelManager()

    def test_init(self, manager):
        """Test manager initializes with empty kernel dict."""
        assert manager._kernels == {}
        assert manager.active_count == 0

    def test_get_kernel_not_found(self, manager):
        """Test get_kernel returns None for non-existent lab."""
        assert manager.get_kernel("nonexistent") is None

    def test_get_kernel_found(self, manager, mock_kernel):
        """Test get_kernel returns kernel when it exists."""
        manager._kernels["test-lab"] = mock_kernel
        assert manager.get_kernel("test-lab") == mock_kernel

    @pytest.mark.asyncio
    async def test_start_kernel_creates_new(self, manager):
        """Test starting a kernel for a new lab."""
        with patch("agent_runtime.kernels.manager.env_manager") as mock_env:
            with patch("agent_runtime.kernels.manager.IPythonKernel") as mock_kernel_class:
                mock_env.get_env_path.return_value = None
                mock_env.get_kernel_name.return_value = "agent-runtime-test"

                mock_kernel = MagicMock()
                mock_kernel.is_ready = True
                mock_kernel.start = AsyncMock()
                mock_kernel_class.return_value = mock_kernel

                kernel = await manager.start_kernel("test-lab", create_env=True)

                mock_env.create_env.assert_called_once_with("test-lab")
                mock_env.install_kernel_spec.assert_called_once_with("test-lab")
                mock_kernel.start.assert_called_once()
                assert kernel == mock_kernel
                assert manager.active_count == 1

    @pytest.mark.asyncio
    async def test_start_kernel_reuses_existing(self, manager, mock_kernel):
        """Test starting kernel returns existing ready kernel."""
        manager._kernels["test-lab"] = mock_kernel

        with patch("agent_runtime.kernels.manager.env_manager"):
            kernel = await manager.start_kernel("test-lab")

        assert kernel == mock_kernel

    @pytest.mark.asyncio
    async def test_start_kernel_replaces_unready(self, manager):
        """Test starting kernel replaces unready kernel."""
        old_kernel = MagicMock()
        old_kernel.is_ready = False
        old_kernel.shutdown = AsyncMock()
        manager._kernels["test-lab"] = old_kernel

        with patch("agent_runtime.kernels.manager.env_manager") as mock_env:
            with patch("agent_runtime.kernels.manager.IPythonKernel") as mock_kernel_class:
                mock_env.get_env_path.return_value = "/some/path"
                mock_env.get_kernel_name.return_value = "agent-runtime-test"

                new_kernel = MagicMock()
                new_kernel.is_ready = True
                new_kernel.start = AsyncMock()
                mock_kernel_class.return_value = new_kernel

                kernel = await manager.start_kernel("test-lab", create_env=False)

                old_kernel.shutdown.assert_called_once()
                assert kernel == new_kernel

    @pytest.mark.asyncio
    async def test_stop_kernel_existing(self, manager, mock_kernel):
        """Test stopping an existing kernel."""
        mock_kernel.shutdown = AsyncMock()
        manager._kernels["test-lab"] = mock_kernel

        result = await manager.stop_kernel("test-lab")

        assert result is True
        mock_kernel.shutdown.assert_called_once()
        assert "test-lab" not in manager._kernels

    @pytest.mark.asyncio
    async def test_stop_kernel_not_found(self, manager):
        """Test stopping non-existent kernel returns False."""
        result = await manager.stop_kernel("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_restart_kernel_existing(self, manager, mock_kernel):
        """Test restarting an existing kernel."""
        mock_kernel.restart = AsyncMock()
        manager._kernels["test-lab"] = mock_kernel

        result = await manager.restart_kernel("test-lab")

        assert result == mock_kernel
        mock_kernel.restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_kernel_not_found(self, manager):
        """Test restarting non-existent kernel returns None."""
        result = await manager.restart_kernel("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_interrupt_kernel_existing(self, manager, mock_kernel):
        """Test interrupting an existing kernel."""
        mock_kernel.interrupt = AsyncMock()
        manager._kernels["test-lab"] = mock_kernel

        result = await manager.interrupt_kernel("test-lab")

        assert result is True
        mock_kernel.interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_interrupt_kernel_not_found(self, manager):
        """Test interrupting non-existent kernel returns False."""
        result = await manager.interrupt_kernel("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_execute_auto_starts_kernel(self, manager):
        """Test execute auto-starts kernel if not running."""
        with patch.object(manager, "start_kernel") as mock_start:
            mock_kernel = MagicMock()
            mock_kernel.execute = AsyncMock(return_value=MagicMock(success=True))
            mock_start.return_value = mock_kernel

            await manager.execute("new-lab", "print('hello')", auto_start=True)

            mock_start.assert_called_once_with("new-lab")

    @pytest.mark.asyncio
    async def test_execute_without_auto_start_raises(self, manager):
        """Test execute raises error if kernel not running and auto_start=False."""
        with pytest.raises(RuntimeError, match="No kernel running"):
            await manager.execute("nonexistent", "print('hello')", auto_start=False)

    @pytest.mark.asyncio
    async def test_shutdown_all(self, manager):
        """Test shutting down all kernels."""
        kernel1 = MagicMock()
        kernel1.shutdown = AsyncMock()
        kernel2 = MagicMock()
        kernel2.shutdown = AsyncMock()

        manager._kernels["lab1"] = kernel1
        manager._kernels["lab2"] = kernel2

        await manager.shutdown_all()

        kernel1.shutdown.assert_called_once()
        kernel2.shutdown.assert_called_once()
        assert manager.active_count == 0

    def test_list_kernels(self, manager, mock_kernel):
        """Test listing active kernels."""
        mock_kernel.kernel_name = "agent-runtime-test"
        mock_kernel.is_ready = True
        manager._kernels["test-lab"] = mock_kernel

        kernels = manager.list_kernels()

        assert len(kernels) == 1
        assert kernels[0]["lab_id"] == "test-lab"
        assert kernels[0]["kernel_name"] == "agent-runtime-test"
        assert kernels[0]["ready"] is True
