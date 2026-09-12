"""Code execution endpoints."""

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent_runtime.auth import require_auth
from agent_runtime.envs import LAB_ID_PATTERN
from agent_runtime.kernels.manager import kernel_manager

router = APIRouter()


class ExecuteRequest(BaseModel):
    """Request to execute code."""

    lab_id: str = Field(pattern=LAB_ID_PATTERN)
    code: str
    cell_id: str | None = None
    stream: bool = False


class ExecuteResponse(BaseModel):
    """Response from code execution."""

    cell_id: str
    success: bool
    outputs: list[dict[str, Any]]
    error: dict[str, Any] | None = None
    execution_count: int | None = None


@router.post("/cell/run", response_model=ExecuteResponse)
async def execute_code(
    request: ExecuteRequest,
    origin: str = Depends(require_auth),
) -> ExecuteResponse | StreamingResponse:
    """Execute code in a kernel.

    If stream=True, returns a streaming response with outputs as they arrive.
    Otherwise, waits for completion and returns all outputs.
    """
    if request.stream:
        return await _execute_streaming(request)

    try:
        result = await kernel_manager.execute(
            lab_id=request.lab_id,
            code=request.code,
            cell_id=request.cell_id,
            auto_start=True,
        )

        return ExecuteResponse(
            cell_id=result.cell_id,
            success=result.success,
            outputs=result.outputs,
            error=result.error,
            execution_count=result.execution_count,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _execute_streaming(request: ExecuteRequest) -> StreamingResponse:
    """Execute code with streaming output."""
    kernel = kernel_manager.get_kernel(request.lab_id)

    if kernel is None or not kernel.is_ready:
        kernel = await kernel_manager.start_kernel(request.lab_id)

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for output in kernel.execute_stream(request.code, cell_id=request.cell_id):
                yield f"data: {json.dumps(output)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )


@router.post("/cell/run/stream")
async def execute_code_stream(
    request: ExecuteRequest,
    origin: str = Depends(require_auth),
) -> StreamingResponse:
    """Execute code with Server-Sent Events streaming output."""
    return await _execute_streaming(request)
