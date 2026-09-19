"""FastAPI server entrypoint for Agent Runtime."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from agent_runtime import __protocol_version__, __version__
from agent_runtime import labs as lab_runtime
from agent_runtime.api import execute, health, kernel, labs, pairing
from agent_runtime.config import settings
from agent_runtime.kernels.manager import kernel_manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle."""
    # Startup
    yield
    # Shutdown - stop running lab actions, then clean up all kernels
    await lab_runtime.lab_manager.shutdown()
    await kernel_manager.shutdown_all()


app = FastAPI(
    title="Agent Runtime",
    description="Lightweight execution engine for AI agents",
    version=__version__,
    lifespan=lifespan,
)


# Permit browser transport; protected routes still enforce origin authorization.
app.add_middleware(
    CORSMiddleware,
    # Pairing must be reachable before an origin is trusted. CORS permits the
    # HTTP transport; require_auth remains the authorization boundary.
    allow_origin_regex=r"https?://.+",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def private_network_access(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Support browsers that still enforce PNA response preflights."""
    response = await call_next(request)
    if request.headers.get("access-control-request-private-network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


# Include API routers
app.include_router(health.router, tags=["health"])
app.include_router(pairing.router, prefix="/pairing", tags=["pairing"])
app.include_router(kernel.router, prefix="/kernel", tags=["kernel"])
app.include_router(execute.router, tags=["execute"])
app.include_router(labs.router, prefix="/labs", tags=["labs"])


@app.get("/runtime/info")
async def runtime_info() -> dict[str, object]:
    """Return runtime version and capabilities."""
    return {
        "runtime_version": __version__,
        "protocol_version": __protocol_version__,
        "capabilities": ["python", "jupyter", "local_fs", "lab_actions", "pairing_scopes"],
    }


def main() -> None:
    """Run the server."""
    import uvicorn

    uvicorn.run(
        "agent_runtime.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
