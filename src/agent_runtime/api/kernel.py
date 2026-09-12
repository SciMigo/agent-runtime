"""Kernel management endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent_runtime.auth import require_auth
from agent_runtime.envs import LAB_ID_PATTERN, env_manager
from agent_runtime.kernels.manager import kernel_manager

router = APIRouter()


class KernelStartRequest(BaseModel):
    """Request to start a kernel."""

    lab_id: str = Field(pattern=LAB_ID_PATTERN)
    create_env: bool = True
    python_version: str | None = None


class KernelResponse(BaseModel):
    """Response for kernel operations."""

    lab_id: str = Field(pattern=LAB_ID_PATTERN)
    status: str
    kernel_name: str | None = None
    ready: bool = False


class PackageInstallRequest(BaseModel):
    """Request to install packages."""

    lab_id: str
    packages: list[str]


@router.post("/start", response_model=KernelResponse)
async def start_kernel(
    request: KernelStartRequest,
    origin: str = Depends(require_auth),
) -> KernelResponse:
    """Start a kernel for a lab."""
    if not request.create_env and not env_manager.get_env_path(request.lab_id):
        raise HTTPException(
            status_code=404,
            detail=(
                f"No environment exists for lab '{request.lab_id}'. "
                "Retry with create_env=true or create it with "
                f"'agent-runtime env create {request.lab_id}'."
            ),
        )

    try:
        # Create env with specific Python version if requested
        if request.create_env and not env_manager.get_env_path(request.lab_id):
            env_manager.create_env(request.lab_id, python_version=request.python_version)
            env_manager.install_kernel_spec(request.lab_id)

        kernel = await kernel_manager.start_kernel(
            request.lab_id,
            create_env=request.create_env,
        )

        return KernelResponse(
            lab_id=request.lab_id,
            status="started",
            kernel_name=kernel.kernel_name,
            ready=kernel.is_ready,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/interrupt")
async def interrupt_kernel(
    lab_id: str,
    origin: str = Depends(require_auth),
) -> dict[str, str]:
    """Interrupt execution in a kernel."""
    success = await kernel_manager.interrupt_kernel(lab_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"No kernel found for lab {lab_id}")

    return {"status": "interrupted", "lab_id": lab_id}


@router.post("/restart", response_model=KernelResponse)
async def restart_kernel(
    lab_id: str,
    origin: str = Depends(require_auth),
) -> KernelResponse:
    """Restart a kernel."""
    kernel = await kernel_manager.restart_kernel(lab_id)
    if not kernel:
        raise HTTPException(status_code=404, detail=f"No kernel found for lab {lab_id}")

    return KernelResponse(
        lab_id=lab_id,
        status="restarted",
        kernel_name=kernel.kernel_name,
        ready=kernel.is_ready,
    )


@router.delete("")
async def shutdown_kernel(
    lab_id: str,
    origin: str = Depends(require_auth),
) -> dict[str, str]:
    """Shutdown a kernel."""
    success = await kernel_manager.stop_kernel(lab_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"No kernel found for lab {lab_id}")

    return {"status": "shutdown", "lab_id": lab_id}


@router.get("/list")
async def list_kernels(
    origin: str = Depends(require_auth),
) -> dict[str, list[dict[str, Any]]]:
    """List all active kernels."""
    return {"kernels": kernel_manager.list_kernels()}


@router.get("/status")
async def kernel_status(
    lab_id: str,
    origin: str = Depends(require_auth),
) -> KernelResponse:
    """Get status of a specific kernel."""
    kernel = kernel_manager.get_kernel(lab_id)
    if not kernel:
        raise HTTPException(status_code=404, detail=f"No kernel found for lab {lab_id}")

    return KernelResponse(
        lab_id=lab_id,
        status="running" if kernel.is_ready else "starting",
        kernel_name=kernel.kernel_name,
        ready=kernel.is_ready,
    )


@router.post("/packages/install")
async def install_packages(
    request: PackageInstallRequest,
    origin: str = Depends(require_auth),
) -> dict[str, Any]:
    """Install packages into a lab's environment."""
    if not env_manager.get_env_path(request.lab_id):
        raise HTTPException(
            status_code=404, detail=f"No environment found for lab {request.lab_id}"
        )

    success, output = env_manager.install_packages(request.lab_id, request.packages)

    if not success:
        raise HTTPException(status_code=500, detail=output)

    return {
        "status": "installed",
        "lab_id": request.lab_id,
        "packages": request.packages,
        "output": output,
    }
