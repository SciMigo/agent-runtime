"""Browser-accessible pairing endpoint."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from agent_runtime.auth import is_valid_web_origin, pairing_manager
from agent_runtime.config import settings

router = APIRouter()
_prompt_lock = asyncio.Lock()


class PairingResponse(BaseModel):
    """Credentials issued after an operator approves an origin."""

    origin: str
    token: str


@router.post("/request", response_model=PairingResponse)
async def request_pairing(request: Request) -> PairingResponse:
    """Prompt the local operator to approve the browser's Origin header."""
    if not settings.require_pairing:
        raise HTTPException(status_code=409, detail="Pairing is disabled")

    origin = request.headers.get("origin", "")
    if not is_valid_web_origin(origin):
        raise HTTPException(
            status_code=400,
            detail="A valid HTTP(S) Origin header is required",
        )

    async with _prompt_lock:
        pairing_code = pairing_manager.initiate_pairing(origin)
        approved = await run_in_threadpool(
            pairing_manager.prompt_for_pairing,
            origin,
            pairing_code,
        )
        if not approved:
            pairing_manager.cancel_pairing(pairing_code)
            raise HTTPException(status_code=403, detail="Pairing was not approved")

        token = pairing_manager.approve_pairing(pairing_code)

    if token is None:
        raise HTTPException(status_code=410, detail="Pairing request expired")

    return PairingResponse(origin=origin, token=token)
