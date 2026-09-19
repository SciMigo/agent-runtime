"""Browser-accessible pairing endpoint."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from agent_runtime.auth import is_valid_web_origin, pairing_manager, terminal_prompt_lock
from agent_runtime.config import settings

router = APIRouter()


class PairingRequest(BaseModel):
    """What the page asks for. "actions" is the least privilege a lab page needs."""

    scope: Literal["code", "actions"] = "code"


class PairingResponse(BaseModel):
    """Credentials issued after an operator approves an origin."""

    origin: str
    token: str
    scope: str


@router.post("/request", response_model=PairingResponse)
async def request_pairing(request: Request, body: PairingRequest | None = None) -> PairingResponse:
    """Prompt the local operator to approve the browser's Origin header."""
    scope = (body or PairingRequest()).scope
    if not settings.require_pairing:
        raise HTTPException(status_code=409, detail="Pairing is disabled")

    origin = request.headers.get("origin", "")
    if not is_valid_web_origin(origin):
        raise HTTPException(
            status_code=400,
            detail="A valid HTTP(S) Origin header is required",
        )

    async with terminal_prompt_lock:
        pairing_code = pairing_manager.initiate_pairing(origin, scope)
        approved = await run_in_threadpool(
            pairing_manager.prompt_for_pairing,
            origin,
            pairing_code,
            scope,
        )
        if not approved:
            pairing_manager.cancel_pairing(pairing_code)
            raise HTTPException(status_code=403, detail="Pairing was not approved")

        token = pairing_manager.approve_pairing(pairing_code)

    if token is None:
        raise HTTPException(status_code=410, detail="Pairing request expired")

    return PairingResponse(origin=origin, token=token, scope=scope)
