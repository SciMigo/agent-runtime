"""IPython/Jupyter kernel wrapper.

Provides a clean interface to jupyter_client for:
- Starting/stopping kernels
- Executing code
- Streaming output
- Interrupt handling
"""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from jupyter_client.asynchronous.client import AsyncKernelClient
from jupyter_client.manager import AsyncKernelManager

from agent_runtime.events import EventType, emit_event
from agent_runtime.observability import metrics, trace_span


@dataclass
class ExecutionResult:
    """Result of a code execution."""

    cell_id: str
    success: bool
    outputs: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    execution_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "success": self.success,
            "outputs": self.outputs,
            "error": self.error,
            "execution_count": self.execution_count,
        }


class IPythonKernel:
    """Wrapper around jupyter_client for IPython kernel management."""

    def __init__(self, kernel_name: str, lab_id: str) -> None:
        self.kernel_name = kernel_name
        self.lab_id = lab_id
        self._km: AsyncKernelManager | None = None
        self._kc: AsyncKernelClient | None = None
        self._ready = False
        self._execution_lock = asyncio.Lock()

    @property
    def is_ready(self) -> bool:
        """Check if kernel is ready for execution."""
        # Use has_kernel (sync) instead of is_alive() (async) for property access
        return self._ready and self._km is not None and self._km.has_kernel

    async def start(self) -> None:
        """Start the kernel."""
        emit_event(
            EventType.KERNEL_STARTING,
            {"kernel_name": self.kernel_name},
            lab_id=self.lab_id,
        )

        self._km = AsyncKernelManager(kernel_name=self.kernel_name)
        await self._km.start_kernel()

        self._kc = self._km.client()
        self._kc.start_channels()

        # Wait for kernel to be ready
        try:
            # The client must receive an IOPub message before we execute cells.
            # A shell-only kernel_info reply can arrive before its SUB socket is
            # subscribed, losing the first cell's output or idle notification.
            await asyncio.wait_for(self._kc.wait_for_ready(timeout=30.0), timeout=35.0)
            self._ready = True

            emit_event(
                EventType.KERNEL_STARTED,
                {"kernel_name": self.kernel_name},
                lab_id=self.lab_id,
            )
            metrics.increment("kernel.started", tags={"lab_id": self.lab_id})

        except TimeoutError:
            await self.shutdown()
            raise RuntimeError(f"Kernel {self.kernel_name} failed to start within timeout")

    async def execute(
        self,
        code: str,
        cell_id: str | None = None,
        silent: bool = False,
    ) -> ExecutionResult:
        """Execute code and return the result.

        For streaming output, use execute_stream instead.
        """
        async with self._execution_lock:
            return await self._execute_unlocked(code, cell_id, silent)

    async def _execute_unlocked(
        self,
        code: str,
        cell_id: str | None,
        silent: bool,
    ) -> ExecutionResult:
        if not self.is_ready or self._kc is None:
            raise RuntimeError("Kernel is not ready")

        cell_id = cell_id or str(uuid4())

        with trace_span("cell_execution", lab_id=self.lab_id, cell_id=cell_id) as span:
            span.set_attribute("code_length", len(code))

            emit_event(
                EventType.CELL_STARTED,
                {"code": code[:200]},  # Truncate for event
                lab_id=self.lab_id,
                cell_id=cell_id,
            )

            msg_id = self._kc.execute(code, silent=silent)
            outputs: list[dict[str, Any]] = []
            error: dict[str, Any] | None = None
            execution_count: int | None = None

            # Collect all outputs
            while True:
                try:
                    msg = await asyncio.wait_for(
                        self._kc.get_iopub_msg(),
                        timeout=60.0,
                    )
                except TimeoutError:
                    continue

                msg_type = msg["header"]["msg_type"]
                content = msg["content"]

                # Only process messages for our execution
                if msg["parent_header"].get("msg_id") != msg_id:
                    continue

                if msg_type == "status":
                    if content["execution_state"] == "idle":
                        break
                    elif content["execution_state"] == "busy":
                        emit_event(
                            EventType.KERNEL_BUSY,
                            {},
                            lab_id=self.lab_id,
                            cell_id=cell_id,
                        )

                elif msg_type == "stream":
                    stream_output = {
                        "type": "stream",
                        "content": {
                            "name": content["name"],
                            "text": content["text"],
                        },
                    }
                    outputs.append(stream_output)
                    emit_event(
                        EventType.CELL_STREAM,
                        stream_output,
                        lab_id=self.lab_id,
                        cell_id=cell_id,
                    )

                elif msg_type == "execute_result":
                    result_output = {
                        "type": "result",
                        "content": {
                            "data": content["data"],
                        },
                    }
                    outputs.append(result_output)
                    execution_count = content.get("execution_count")
                    emit_event(
                        EventType.CELL_RESULT,
                        result_output,
                        lab_id=self.lab_id,
                        cell_id=cell_id,
                    )

                elif msg_type == "display_data":
                    display_output = {
                        "type": "display_data",
                        "content": {
                            "data": content["data"],
                        },
                    }
                    outputs.append(display_output)
                    emit_event(
                        EventType.CELL_RESULT,
                        display_output,
                        lab_id=self.lab_id,
                        cell_id=cell_id,
                    )

                elif msg_type == "error":
                    error = {
                        "ename": content["ename"],
                        "evalue": content["evalue"],
                        "traceback": content["traceback"],
                    }
                    emit_event(
                        EventType.CELL_ERROR,
                        error,
                        lab_id=self.lab_id,
                        cell_id=cell_id,
                    )
                    span.set_error(f"{content['ename']}: {content['evalue']}")

            success = error is None
            span.set_attribute("success", success)
            span.set_attribute("output_count", len(outputs))

            emit_event(
                EventType.CELL_COMPLETED,
                {"success": success},
                lab_id=self.lab_id,
                cell_id=cell_id,
            )

            metrics.increment(
                "cell.executed",
                tags={"lab_id": self.lab_id, "success": str(success)},
            )

            return ExecutionResult(
                cell_id=cell_id,
                success=success,
                outputs=outputs,
                error=error,
                execution_count=execution_count,
            )

    async def execute_stream(
        self,
        code: str,
        cell_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute code and stream outputs as they arrive."""
        async with self._execution_lock:
            async for output in self._execute_stream_unlocked(code, cell_id):
                yield output

    async def _execute_stream_unlocked(
        self,
        code: str,
        cell_id: str | None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        if not self.is_ready or self._kc is None:
            raise RuntimeError("Kernel is not ready")

        cell_id = cell_id or str(uuid4())

        emit_event(
            EventType.CELL_STARTED,
            {"code": code[:200]},
            lab_id=self.lab_id,
            cell_id=cell_id,
        )

        msg_id = self._kc.execute(code)

        while True:
            try:
                msg = await asyncio.wait_for(
                    self._kc.get_iopub_msg(),
                    timeout=60.0,
                )
            except TimeoutError:
                continue

            msg_type = msg["header"]["msg_type"]
            content = msg["content"]

            if msg["parent_header"].get("msg_id") != msg_id:
                continue

            if msg_type == "status" and content["execution_state"] == "idle":
                yield {"type": "complete", "cell_id": cell_id}
                break

            elif msg_type == "stream":
                yield {
                    "type": "stream",
                    "name": content["name"],
                    "text": content["text"],
                    "cell_id": cell_id,
                }

            elif msg_type == "execute_result":
                yield {
                    "type": "result",
                    "data": content["data"],
                    "execution_count": content.get("execution_count"),
                    "cell_id": cell_id,
                }

            elif msg_type == "display_data":
                yield {
                    "type": "display",
                    "data": content["data"],
                    "cell_id": cell_id,
                }

            elif msg_type == "error":
                yield {
                    "type": "error",
                    "ename": content["ename"],
                    "evalue": content["evalue"],
                    "traceback": content["traceback"],
                    "cell_id": cell_id,
                }

    async def interrupt(self) -> None:
        """Interrupt the current execution."""
        if self._km is None:
            return

        emit_event(
            EventType.KERNEL_INTERRUPTED,
            {},
            lab_id=self.lab_id,
        )

        await self._km.interrupt_kernel()

    async def restart(self) -> None:
        """Restart the kernel."""
        if self._km is None:
            return

        emit_event(
            EventType.KERNEL_RESTARTING,
            {},
            lab_id=self.lab_id,
        )

        await self._km.restart_kernel()
        self._ready = False

        # Wait for ready again
        if self._kc is None:
            raise RuntimeError("Kernel client is not available after restart")
        await asyncio.wait_for(self._kc.wait_for_ready(timeout=30.0), timeout=35.0)
        self._ready = True

        emit_event(
            EventType.KERNEL_STARTED,
            {"kernel_name": self.kernel_name, "restarted": True},
            lab_id=self.lab_id,
        )

    async def shutdown(self) -> None:
        """Shutdown the kernel."""
        if self._kc is not None:
            self._kc.stop_channels()
            self._kc = None

        if self._km is not None:
            await self._km.shutdown_kernel()
            self._km = None

        self._ready = False

        emit_event(
            EventType.KERNEL_SHUTDOWN,
            {},
            lab_id=self.lab_id,
        )
        metrics.increment("kernel.shutdown", tags={"lab_id": self.lab_id})

    async def is_alive(self) -> bool:
        """Check if kernel is alive."""
        if self._km is None:
            return False
        return bool(await self._km.is_alive())
