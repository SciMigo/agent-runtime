"""Health check endpoints."""

from fastapi import APIRouter

from agent_runtime import __protocol_version__, __version__
from agent_runtime.kernels.manager import kernel_manager
from agent_runtime.observability import metrics

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Basic health check."""
    return {"status": "ok"}


@router.get("/health/detailed")
async def detailed_health() -> dict[str, object]:
    """Detailed health check with component status."""
    return {
        "status": "ok",
        "version": __version__,
        "protocol_version": __protocol_version__,
        "components": {
            "server": "healthy",
            "kernels": {
                "active_count": kernel_manager.active_count,
                "status": "healthy",
            },
        },
    }


@router.get("/metrics")
async def get_metrics() -> dict[str, object]:
    """Get runtime metrics."""
    return metrics.get_all()
