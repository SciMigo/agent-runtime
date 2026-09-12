"""Kernel manager for Agent Runtime.

Manages multiple kernels across labs:
- Start/stop kernels
- Track kernel state
- Handle kernel lifecycle
"""

import asyncio
from typing import Any

from agent_runtime.envs import env_manager
from agent_runtime.kernels.ipython import ExecutionResult, IPythonKernel
from agent_runtime.observability import logger, metrics


class KernelManager:
    """Manages kernels for all labs."""

    def __init__(self) -> None:
        self._kernels: dict[str, IPythonKernel] = {}
        self._lock = asyncio.Lock()

    def get_kernel(self, lab_id: str) -> IPythonKernel | None:
        """Get the kernel for a lab if it exists."""
        return self._kernels.get(lab_id)

    async def start_kernel(
        self,
        lab_id: str,
        create_env: bool = True,
    ) -> IPythonKernel:
        """Start a kernel for a lab.

        Args:
            lab_id: The lab identifier
            create_env: If True, create venv if it doesn't exist

        Returns:
            The started kernel
        """
        async with self._lock:
            # Check if kernel already exists
            if lab_id in self._kernels:
                kernel = self._kernels[lab_id]
                if kernel.is_ready:
                    return kernel
                # Kernel exists but not ready, clean it up
                await kernel.shutdown()
                del self._kernels[lab_id]

            # Ensure environment exists
            if create_env and not env_manager.get_env_path(lab_id):
                logger.info(f"Creating environment for lab {lab_id}")
                env_manager.create_env(lab_id)
                env_manager.install_kernel_spec(lab_id)

            # Get kernel name
            kernel_name = env_manager.get_kernel_name(lab_id)

            # Create and start kernel
            kernel = IPythonKernel(kernel_name=kernel_name, lab_id=lab_id)
            await kernel.start()

            self._kernels[lab_id] = kernel
            metrics.gauge("kernels.active", len(self._kernels))

            return kernel

    async def stop_kernel(self, lab_id: str) -> bool:
        """Stop a kernel for a lab.

        Returns True if kernel was stopped, False if not found.
        """
        async with self._lock:
            kernel = self._kernels.pop(lab_id, None)
            if kernel:
                await kernel.shutdown()
                metrics.gauge("kernels.active", len(self._kernels))
                return True
            return False

    async def restart_kernel(self, lab_id: str) -> IPythonKernel | None:
        """Restart a kernel for a lab.

        Returns the restarted kernel, or None if not found.
        """
        kernel = self._kernels.get(lab_id)
        if kernel:
            await kernel.restart()
            return kernel
        return None

    async def interrupt_kernel(self, lab_id: str) -> bool:
        """Interrupt execution in a kernel.

        Returns True if interrupted, False if kernel not found.
        """
        kernel = self._kernels.get(lab_id)
        if kernel:
            await kernel.interrupt()
            return True
        return False

    async def execute(
        self,
        lab_id: str,
        code: str,
        cell_id: str | None = None,
        auto_start: bool = True,
    ) -> ExecutionResult:
        """Execute code in a lab's kernel.

        Args:
            lab_id: The lab identifier
            code: The code to execute
            cell_id: Optional cell identifier
            auto_start: If True, start kernel if not running

        Returns:
            The execution result
        """
        kernel = self._kernels.get(lab_id)

        if kernel is None or not kernel.is_ready:
            if auto_start:
                kernel = await self.start_kernel(lab_id)
            else:
                raise RuntimeError(f"No kernel running for lab {lab_id}")

        return await kernel.execute(code, cell_id=cell_id)

    async def shutdown_all(self) -> None:
        """Shutdown all kernels."""
        async with self._lock:
            tasks = []
            for kernel in self._kernels.values():
                tasks.append(kernel.shutdown())

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            self._kernels.clear()
            metrics.gauge("kernels.active", 0)

    def list_kernels(self) -> list[dict[str, Any]]:
        """List all active kernels."""
        return [
            {
                "lab_id": lab_id,
                "kernel_name": kernel.kernel_name,
                "ready": kernel.is_ready,
            }
            for lab_id, kernel in self._kernels.items()
        ]

    @property
    def active_count(self) -> int:
        """Get count of active kernels."""
        return len(self._kernels)


# Global kernel manager
kernel_manager = KernelManager()
