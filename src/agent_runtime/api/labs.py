"""Lab action endpoints: prepare an approved lab version, then start, read and stop its actions.

These accept tokens of either scope. A site paired with "actions" can use only these; it
cannot reach /kernel or /cell. See agent_runtime.labs for the manifest and the approval rules.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent_runtime import labs
from agent_runtime.auth import require_lab_auth, terminal_prompt_lock
from agent_runtime.config import settings
from agent_runtime.envs import LAB_ID_PATTERN

router = APIRouter()


class PrepareRequest(BaseModel):
    repo: str = Field(max_length=500, description="Public repository URL (https)")
    commit: str = Field(description="Full 40-character commit SHA")


class ActionInfo(BaseModel):
    name: str
    label: str
    description: str
    steps: list[str]
    timeout: int


class PrepareResponse(BaseModel):
    lab_id: str
    title: str
    repo: str
    commit: str
    path: str
    actions: list[ActionInfo]


class RunRequest(BaseModel):
    lab_id: str = Field(pattern=LAB_ID_PATTERN)
    commit: str
    action: str = Field(max_length=40)


class RunResponse(BaseModel):
    run_id: str
    lab_id: str
    commit: str
    action: str
    status: str
    exit_code: int | None
    started_at: str
    ended_at: str | None
    output: str = ""
    next_offset: int = 0
    truncated: bool = False


def _error(error: labs.LabError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=str(error))


def _snapshot(run: labs.Run, offset: int | None) -> RunResponse:
    output, next_offset, truncated = run.read(offset) if offset is not None else ("", 0, False)
    return RunResponse(
        run_id=run.run_id,
        lab_id=run.lab_id,
        commit=run.commit,
        action=run.action,
        status=run.status,
        exit_code=run.exit_code,
        started_at=run.started_at,
        ended_at=run.ended_at,
        output=output,
        next_offset=next_offset,
        truncated=truncated,
    )


@router.post("/prepare", response_model=PrepareResponse)
async def prepare_lab(
    body: PrepareRequest, origin: str = Depends(require_lab_auth)
) -> PrepareResponse:
    """Fetch the commit, read lab.toml, and ask the user to approve this lab version once.

    Like pairing, the request stays open while the terminal prompt waits for an answer.
    """
    try:
        labs.validate_source(body.repo, body.commit)
        dest = labs.checkout_dir(labs.repo_key(body.repo), body.commit)
        path = await run_in_threadpool(labs.fetch_commit, body.repo, body.commit, dest)
        manifest_file = path / labs.MANIFEST_NAME
        if not manifest_file.is_file():
            raise labs.LabError(f"no {labs.MANIFEST_NAME} at that commit", 404)
        manifest = labs.parse_manifest(manifest_file.read_bytes())

        if settings.require_pairing:
            async with terminal_prompt_lock:
                if not labs.is_approved(origin, body.repo, body.commit, manifest.sha256):
                    description = labs.describe_for_approval(
                        origin, body.repo, body.commit, manifest, path
                    )
                    if not await run_in_threadpool(labs.approval_prompt, description):
                        raise labs.LabError("the lab was not approved", 403)
                    labs.record_approval(origin, body.repo, body.commit, manifest.sha256)

        bin_dir = await run_in_threadpool(labs.lab_env, manifest.lab_id)
    except labs.LabError as error:
        raise _error(error) from error

    labs.lab_manager.register(
        labs.PreparedLab(
            repo=body.repo, commit=body.commit, path=path, manifest=manifest, bin_dir=bin_dir
        )
    )
    return PrepareResponse(
        lab_id=manifest.lab_id,
        title=manifest.title,
        repo=body.repo,
        commit=body.commit,
        path=str(path),
        actions=[
            ActionInfo(
                name=a.name,
                label=a.label,
                description=a.description,
                steps=[shlex.join(argv) for argv in a.steps],
                timeout=a.timeout,
            )
            for a in manifest.actions.values()
        ],
    )


@router.post("/runs", response_model=RunResponse)
async def start_run(body: RunRequest, origin: str = Depends(require_lab_auth)) -> RunResponse:
    """Start one of a prepared lab's actions. One run at a time per lab (409 otherwise)."""
    try:
        prepared = labs.lab_manager.lab(body.lab_id, body.commit)
        if settings.require_pairing and not labs.is_approved(
            origin, prepared.repo, prepared.commit, prepared.manifest.sha256
        ):
            raise labs.LabError("this site has not been approved for this lab version", 403)
        run = await labs.lab_manager.start(origin, prepared, body.action)
    except labs.LabError as error:
        raise _error(error) from error
    return _snapshot(run, 0)


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(
    lab_id: str | None = None, origin: str = Depends(require_lab_auth)
) -> list[RunResponse]:
    """This site's runs, without output: lets a reloaded page find a run still going."""
    return [_snapshot(run, None) for run in labs.lab_manager.runs_for(origin, lab_id)]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def read_run(
    run_id: str,
    offset: int = Query(0, ge=0, description="Return output from this character on"),
    origin: str = Depends(require_lab_auth),
) -> RunResponse:
    """Status plus output since `offset`; poll with the returned `next_offset`."""
    try:
        run = labs.lab_manager.get_run(run_id, origin)
    except labs.LabError as error:
        raise _error(error) from error
    return _snapshot(run, offset)


@router.post("/runs/{run_id}/stop", response_model=RunResponse)
async def stop_run(
    run_id: str,
    offset: int = Query(0, ge=0),
    origin: str = Depends(require_lab_auth),
) -> RunResponse:
    """SIGINT to the running step's process group; SIGKILL if it has not exited after a grace."""
    try:
        run = labs.lab_manager.get_run(run_id, origin)
    except labs.LabError as error:
        raise _error(error) from error
    await labs.lab_manager.stop(run)
    if run._task is not None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(run._task), timeout=2)
    return _snapshot(run, offset)
