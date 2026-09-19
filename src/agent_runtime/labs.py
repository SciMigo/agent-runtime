"""Named lab actions: run only the commands a lab repository declares, at an approved commit.

A site paired with the "actions" scope cannot send code. It names a lab repository and a full
commit SHA; the runtime fetches that commit, reads its `lab.toml`, and shows the user every
command it declares. After the user approves that lab version once, the site may start any of
those actions by name, stop them, and read their output. Commands run without a shell, in the
lab's checkout, with the lab's own virtual environment first on PATH.

`lab.toml`, schema 1:

    schema = 1
    id = "restate-durable-agent-demo"          # lab id: letters, digits, . _ -
    title = "Durable agents with Restate"

    [actions.setup]
    label = "Prepare the lab"
    description = "Start Restate in Docker; install the demo's packages."
    steps = [
      ["docker", "compose", "up", "-d"],
      ["python", "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
    ]
    timeout = 600                               # seconds, default 900

Each step is an argv list. Steps run in order; the first one that fails ends the action.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent_runtime.config import settings
from agent_runtime.envs import LAB_ID_PATTERN

MANIFEST_NAME = "lab.toml"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ACTION_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DEFAULT_TIMEOUT = 900
MAX_TIMEOUT = 7200
MAX_ACTIONS = 50
MAX_STEPS = 20
MAX_ARGS = 100
MAX_ARG_LENGTH = 4096
GIT_TIMEOUT = 300


class LabError(Exception):
    """A lab request that cannot be served; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    description: str
    steps: tuple[tuple[str, ...], ...]
    timeout: int


@dataclass(frozen=True)
class Manifest:
    lab_id: str
    title: str
    actions: dict[str, Action]
    sha256: str


def _string(value: Any, where: str, limit: int, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise LabError(f"{where} must be a non-empty string of at most {limit} characters")
    return value


def _unknown_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(table) - allowed)
    if extra:
        raise LabError(f"{where}: unknown key(s) {', '.join(extra)}")


def parse_manifest(data: bytes) -> Manifest:
    """Parse and validate a lab.toml. Every rule is strict: a typo fails loudly."""
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LabError(f"{MANIFEST_NAME} is not valid TOML: {error}") from error

    _unknown_keys(doc, {"schema", "id", "title", "actions"}, MANIFEST_NAME)
    if doc.get("schema") != 1:
        raise LabError(f"{MANIFEST_NAME}: schema must be 1")
    lab_id = _string(doc.get("id"), "id", 64)
    if re.fullmatch(LAB_ID_PATTERN, lab_id) is None:
        raise LabError("id must be 1-64 letters, digits, dots, underscores or hyphens")
    title = _string(doc.get("title"), "title", 200)

    raw_actions = doc.get("actions")
    if not isinstance(raw_actions, dict) or not raw_actions:
        raise LabError(f"{MANIFEST_NAME}: declare at least one [actions.<name>] table")
    if len(raw_actions) > MAX_ACTIONS:
        raise LabError(f"{MANIFEST_NAME}: at most {MAX_ACTIONS} actions")

    actions: dict[str, Action] = {}
    for name, table in raw_actions.items():
        where = f"actions.{name}"
        if ACTION_NAME_PATTERN.fullmatch(name) is None:
            raise LabError(f"{where}: action names are lowercase letters, digits, _ and -")
        if not isinstance(table, dict):
            raise LabError(f"{where} must be a table")
        _unknown_keys(table, {"label", "description", "steps", "timeout"}, where)
        steps = table.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise LabError(f"{where}.steps must list 1-{MAX_STEPS} commands")
        parsed_steps = []
        for i, argv in enumerate(steps):
            if (
                not isinstance(argv, list)
                or not 1 <= len(argv) <= MAX_ARGS
                or not all(isinstance(a, str) and 0 < len(a) <= MAX_ARG_LENGTH for a in argv)
                or any("\x00" in a for a in argv)
            ):
                raise LabError(f"{where}.steps[{i}] must be a list of non-empty strings")
            parsed_steps.append(tuple(argv))
        timeout = table.get("timeout", DEFAULT_TIMEOUT)
        valid_timeout = isinstance(timeout, int) and not isinstance(timeout, bool)
        if not valid_timeout or not 1 <= timeout <= MAX_TIMEOUT:
            raise LabError(f"{where}.timeout must be 1-{MAX_TIMEOUT} seconds")
        actions[name] = Action(
            name=name,
            label=_string(table.get("label"), f"{where}.label", 120),
            description=_string(table.get("description"), f"{where}.description", 1000, False),
            steps=tuple(parsed_steps),
            timeout=timeout,
        )
    return Manifest(lab_id, title, actions, hashlib.sha256(data).hexdigest())


# --------------------------------------------------------------------------------------------
# Fetching a pinned commit
# --------------------------------------------------------------------------------------------


def allowed_protocols() -> list[str]:
    return [p for p in settings.lab_git_protocols.split(":") if p]


def validate_source(repo: str, commit: str) -> None:
    """Accept only allowed-protocol URLs without credentials, and full commit SHAs."""
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise LabError("commit must be a full 40-character lowercase SHA")
    parts = urlsplit(repo)
    if parts.scheme not in allowed_protocols():
        raise LabError(f"repo must be a {' or '.join(allowed_protocols())} URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise LabError("repo must not contain credentials, a query or a fragment")
    if parts.scheme != "file" and not parts.hostname:
        raise LabError("repo must name a host")


def checkout_dir(lab_key: str, commit: str) -> Path:
    return settings.labs_dir / lab_key / commit[:12]


def _git(args: list[str], cwd: Path | None = None) -> str:
    env = dict(
        os.environ,
        GIT_ALLOW_PROTOCOL=settings.lab_git_protocols,  # no ext::, ssh, or file smuggling
        GIT_TERMINAL_PROMPT="0",  # public repositories only; never ask for credentials
    )
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as error:
        raise LabError("git is not installed", 500) from error
    except subprocess.TimeoutExpired as error:
        raise LabError("git timed out fetching the lab", 504) from error
    if result.returncode != 0:
        raise LabError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()}", 502)
    return result.stdout.strip()


def fetch_commit(repo: str, commit: str, dest: Path) -> Path:
    """Check out exactly `commit` of `repo` into `dest`, reusing a finished checkout."""
    if (dest / ".git").is_dir():
        with contextlib.suppress(LabError):
            if _git(["rev-parse", "HEAD"], cwd=dest) == commit:
                return dest
    partial = dest.with_name(dest.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    _git(["init", "--quiet"], cwd=partial)
    _git(["fetch", "--quiet", "--depth", "1", "--no-tags", repo, commit], cwd=partial)
    _git(
        ["-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        cwd=partial,
    )
    if _git(["rev-parse", "HEAD"], cwd=partial) != commit:
        raise LabError("the fetched commit does not match the requested SHA", 502)
    shutil.rmtree(dest, ignore_errors=True)
    partial.rename(dest)
    return dest


def repo_key(repo: str) -> str:
    """A directory name for a repository URL: readable, and unique per URL."""
    last = urlsplit(repo).path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "lab"
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", last)[:40]
    return f"{safe}-{hashlib.sha256(repo.encode()).hexdigest()[:8]}"


# --------------------------------------------------------------------------------------------
# Approvals
# --------------------------------------------------------------------------------------------


def _approvals_file() -> Path:
    return settings.labs_dir / "approvals.json"


def _approval_key(origin: str, repo: str, commit: str) -> str:
    return f"{origin} {repo} {commit}"


def is_approved(origin: str, repo: str, commit: str, manifest_sha256: str) -> bool:
    try:
        approvals = json.loads(_approvals_file().read_text())
    except (OSError, ValueError):
        return False
    entry = approvals.get(_approval_key(origin, repo, commit))
    return isinstance(entry, dict) and entry.get("manifest_sha256") == manifest_sha256


def record_approval(origin: str, repo: str, commit: str, manifest_sha256: str) -> None:
    path = _approvals_file()
    try:
        approvals = json.loads(path.read_text())
    except (OSError, ValueError):
        approvals = {}
    approvals[_approval_key(origin, repo, commit)] = {
        "manifest_sha256": manifest_sha256,
        "approved_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(approvals, indent=2))


def describe_for_approval(origin: str, repo: str, commit: str, lab: Manifest, path: Path) -> str:
    lines = [
        "",
        "=" * 60,
        "Lab approval request",
        "=" * 60,
        f"Site:   {origin}",
        f"Lab:    {lab.lab_id} - {lab.title}",
        f"Source: {repo}",
        f"        commit {commit}",
        f"Files:  {path}",
        "It may run these commands, each only when you start that action on the page:",
    ]
    for action in lab.actions.values():
        lines.append(f"  {action.name}: {action.label}")
        lines += [f"      $ {shlex.join(argv)}" for argv in action.steps]
    lines.append("=" * 60)
    return "\n".join(lines)


def prompt_for_approval(description: str) -> bool:
    print(description)
    try:
        return input("Approve this lab? [y/N]: ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# --------------------------------------------------------------------------------------------
# Prepared labs and runs
# --------------------------------------------------------------------------------------------


@dataclass
class PreparedLab:
    repo: str
    commit: str
    path: Path
    manifest: Manifest
    bin_dir: Path  # the lab environment's executables, first on PATH


def ensure_lab_env(lab_id: str) -> Path:
    """The lab's virtual environment (shared with its kernels), created on first use."""
    from agent_runtime.envs import env_manager

    venv = env_manager.get_env_path(lab_id) or env_manager.create_env(lab_id)
    return venv / "bin"


@dataclass
class Run:
    run_id: str
    origin: str
    lab_id: str
    commit: str
    action: str
    started_at: str
    status: str = "running"  # running | succeeded | failed | stopped | timed_out
    exit_code: int | None = None
    ended_at: str | None = None
    _chunks: list[str] = field(default_factory=list)
    _base: int = 0  # characters dropped from the front of the output
    _length: int = 0
    _process: asyncio.subprocess.Process | None = None
    _stop_reason: str | None = None
    _task: asyncio.Task[None] | None = None

    def append(self, text: str) -> None:
        self._chunks.append(text)
        self._length += len(text)
        limit = settings.lab_output_limit
        if self._length > limit:
            joined = "".join(self._chunks)
            drop = len(joined) - limit
            self._chunks = [joined[drop:]]
            self._base += drop
            self._length = limit

    def read(self, offset: int) -> tuple[str, int, bool]:
        """Output from `offset` on, the next offset, and whether earlier output was dropped."""
        text = "".join(self._chunks)
        truncated = offset < self._base
        start = max(offset - self._base, 0)
        return text[start:], self._base + len(text), truncated


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _signal_group(process: asyncio.subprocess.Process, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)


class LabManager:
    """Prepared labs, and the runs of their actions. One run at a time per lab."""

    def __init__(self) -> None:
        self._labs: dict[tuple[str, str], PreparedLab] = {}  # (lab_id, commit)
        self._runs: dict[str, Run] = {}
        self._active: dict[str, str] = {}  # lab_id -> run_id

    def register(self, lab: PreparedLab) -> None:
        self._labs[(lab.manifest.lab_id, lab.commit)] = lab

    def lab(self, lab_id: str, commit: str) -> PreparedLab:
        prepared = self._labs.get((lab_id, commit))
        if prepared is None:
            raise LabError("prepare this lab and commit first (POST /labs/prepare)", 409)
        return prepared

    def get_run(self, run_id: str, origin: str) -> Run:
        run = self._runs.get(run_id)
        if run is None or run.origin != origin:
            raise LabError("no such run", 404)
        return run

    def runs_for(self, origin: str, lab_id: str | None = None) -> list[Run]:
        return [r for r in self._runs.values() if r.origin == origin and lab_id in (None, r.lab_id)]

    async def start(self, origin: str, lab: PreparedLab, action_name: str) -> Run:
        action = lab.manifest.actions.get(action_name)
        if action is None:
            raise LabError(f"lab {lab.manifest.lab_id} has no action '{action_name}'", 404)
        lab_id = lab.manifest.lab_id
        active = self._runs.get(self._active.get(lab_id, ""))
        if active and active.status == "running":
            raise LabError(
                f"'{active.action}' is still running in this lab; stop it or wait for it", 409
            )
        run = Run(
            run_id=secrets.token_urlsafe(12),
            origin=origin,
            lab_id=lab_id,
            commit=lab.commit,
            action=action.name,
            started_at=_now(),
        )
        self._runs[run.run_id] = run
        self._active[lab_id] = run.run_id
        run._task = asyncio.create_task(self._execute(run, lab, action))
        return run

    async def stop(self, run: Run, reason: str = "stopped") -> None:
        """SIGINT to the step's process group, then SIGKILL after the grace period."""
        if run.status != "running":
            return
        run._stop_reason = run._stop_reason or reason
        process = run._process
        if process is None or process.returncode is not None:
            return
        _signal_group(process, signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=settings.lab_stop_grace)
        except TimeoutError:
            _signal_group(process, signal.SIGKILL)

    async def _execute(self, run: Run, lab: PreparedLab, action: Action) -> None:
        env = dict(os.environ)
        env.pop("PYTHONHOME", None)
        env["PATH"] = f"{lab.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(lab.bin_dir.parent)
        env["PYTHONUNBUFFERED"] = "1"
        deadline = time.monotonic() + action.timeout
        watchdog = asyncio.create_task(self._watch(run, deadline))
        try:
            for argv in action.steps:
                if run._stop_reason:
                    break
                run.append(f"$ {shlex.join(argv)}\n")
                code = await self._step(run, lab, argv, env)
                if run._stop_reason or code != 0:
                    run.exit_code = code
                    break
            else:
                run.exit_code = 0
        except Exception as error:  # noqa: BLE001 - a run must always end with a status
            run.append(f"\n[runtime] {error}\n")
            run.exit_code = run.exit_code if run.exit_code is not None else 1
        finally:
            watchdog.cancel()
            if run._stop_reason:
                run.status = run._stop_reason
            else:
                run.status = "succeeded" if run.exit_code == 0 else "failed"
            run.ended_at = _now()
            run._process = None

    async def _step(
        self, run: Run, lab: PreparedLab, argv: tuple[str, ...], env: dict[str, str]
    ) -> int:
        executable = shutil.which(argv[0], path=env["PATH"]) if "/" not in argv[0] else argv[0]
        if executable is None:
            run.append(f"[runtime] command not found: {argv[0]}\n")
            return 127
        process = await asyncio.create_subprocess_exec(
            executable,
            *argv[1:],
            cwd=lab.path,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # its own process group, so stop reaches its children
        )
        run._process = process
        assert process.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")  # chunks split characters
        while chunk := await process.stdout.read(4096):
            run.append(decoder.decode(chunk))
        run.append(decoder.decode(b"", final=True))
        return await process.wait()

    async def _watch(self, run: Run, deadline: float) -> None:
        await asyncio.sleep(max(deadline - time.monotonic(), 0))
        run.append("\n[runtime] timed out\n")
        await self.stop(run, reason="timed_out")

    async def shutdown(self) -> None:
        """Stop every running action and wait for it to finish, so no process outlives us."""
        running = [run for run in self._runs.values() if run.status == "running"]
        for run in running:
            await self.stop(run)
        tasks = [run._task for run in running if run._task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=settings.lab_stop_grace + 2)


lab_manager = LabManager()

# Seams for tests: the prompt and the environment can be replaced.
approval_prompt: Callable[[str], bool] = prompt_for_approval
lab_env: Callable[[str], Path] = ensure_lab_env
