"""Tests for named lab actions (agent_runtime.labs, /labs) and pairing scopes."""

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent_runtime import labs
from agent_runtime.auth import PairingManager, require_auth, require_lab_auth
from agent_runtime.config import Settings
from agent_runtime.server import app

SITE = "https://scimigo.com"

MANIFEST = b"""
schema = 1
id = "demo-lab"
title = "Demo lab"

[actions.hello]
label = "Say hello"
description = "Prints one line."
steps = [["python", "-c", "print('hello from the lab')"]]

[actions.two]
label = "Second step fails"
steps = [
  ["python", "-c", "print('first')"],
  ["python", "-c", "import sys; sys.exit(3)"],
  ["python", "-c", "print('never')"],
]

[actions.slow]
label = "Counts slowly"
steps = [[
  "python", "-c",
  "import time\\nfor i in range(400):\\n    print(i, flush=True)\\n    time.sleep(0.05)",
]]

[actions.hangs]
label = "Times out"
timeout = 1
steps = [["python", "-c", "import time; time.sleep(30)"]]
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def lab_repo(tmp_path: Path) -> tuple[str, str]:
    """A repository with a lab.toml, served over file:// (allowed only in these tests)."""
    repo = tmp_path / "lab-repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "uploadpack.allowReachableSHA1InWant", "true")
    (repo / "lab.toml").write_bytes(MANIFEST)
    _git(repo, "add", "lab.toml")
    _git(repo, "commit", "--quiet", "-m", "lab")
    return repo.as_uri(), _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def lab_settings(tmp_path: Path):
    test_settings = Settings(
        runtime_dir=tmp_path / "runtime",
        require_pairing=True,
        lab_git_protocols="file",
        lab_stop_grace=2.0,
    )
    with (
        patch("agent_runtime.labs.settings", test_settings),
        patch("agent_runtime.api.labs.settings", test_settings),
    ):
        yield test_settings


@pytest.fixture
def client(lab_settings):
    """The real app, with /labs authenticated as SITE and the lab env = this Python."""
    prompts: list[str] = []

    def approve(description: str) -> bool:
        prompts.append(description)
        return True

    app.dependency_overrides[require_lab_auth] = lambda: SITE
    with (
        patch.object(labs, "lab_env", lambda lab_id: Path(sys.executable).parent),
        patch.object(labs, "approval_prompt", approve),
        patch.object(labs, "lab_manager", labs.LabManager()),
        TestClient(app) as test_client,
    ):
        test_client.prompts = prompts  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


def _wait(client: TestClient, run_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/labs/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still running")


def _prepare(client: TestClient, lab_repo: tuple[str, str]) -> dict:
    repo, commit = lab_repo
    response = client.post("/labs/prepare", json={"repo": repo, "commit": commit})
    assert response.status_code == 200, response.text
    return response.json()


def _start(client: TestClient, lab: dict, action: str):
    return client.post(
        "/labs/runs", json={"lab_id": lab["lab_id"], "commit": lab["commit"], "action": action}
    )


HEAD = 'schema = 1\nid = "a"\ntitle = "t"\n'
ACTION = '[actions.x]\nlabel = "x"\n'
STEPS = 'steps = [["a"]]'


class TestManifest:
    def test_valid(self):
        manifest = labs.parse_manifest(MANIFEST)
        assert manifest.lab_id == "demo-lab"
        assert list(manifest.actions) == ["hello", "two", "slow", "hangs"]
        assert manifest.actions["two"].steps[1] == ("python", "-c", "import sys; sys.exit(3)")
        assert manifest.actions["hello"].timeout == labs.DEFAULT_TIMEOUT
        assert manifest.actions["hangs"].timeout == 1

    @pytest.mark.parametrize(
        "text, message",
        [
            (HEAD.replace("schema = 1", "schema = 2") + ACTION + STEPS, "schema"),
            (HEAD.replace('id = "a"', 'id = "../x"') + ACTION + STEPS, "id"),
            (HEAD, "at least one"),
            (HEAD + ACTION.replace("actions.x", "actions.Bad") + STEPS, "action names"),
            (HEAD + ACTION + "steps = []", "steps"),
            (HEAD + ACTION + 'steps = [["a", 1]]', "steps[0]"),
            (HEAD + ACTION + 'command = "ls"\n' + STEPS, "unknown key"),
            (HEAD + ACTION + STEPS + "\ntimeout = 0", "timeout"),
            (HEAD + ACTION + STEPS + "\ntimeout = true", "timeout"),
            (HEAD + "shell = true\n" + ACTION + STEPS, "unknown key"),
            ("not = toml = at all", "not valid TOML"),
        ],
    )
    def test_rejects(self, text: str, message: str):
        with pytest.raises(labs.LabError, match=message.replace("[", r"\[")):
            labs.parse_manifest(text.encode())


class TestSource:
    SHA = "0123456789abcdef0123456789abcdef01234567"

    @pytest.mark.parametrize(
        "repo",
        [
            "ssh://github.com/SciMigo/x.git",
            "git@github.com:SciMigo/x.git",
            "ext::sh -c touch% /tmp/pwned",
            "file:///tmp/x",
            "http://github.com/SciMigo/x",
            "https://user:secret@github.com/SciMigo/x",
            "https://github.com/SciMigo/x?ref=main",
        ],
    )
    def test_rejects_other_protocols_and_credentials(self, repo: str):
        with pytest.raises(labs.LabError):
            labs.validate_source(repo, self.SHA)

    def test_accepts_https_and_requires_a_full_sha(self):
        labs.validate_source("https://github.com/SciMigo/restate-durable-agent-demo", self.SHA)
        with pytest.raises(labs.LabError, match="full 40-character"):
            labs.validate_source("https://github.com/SciMigo/x", "faf512d")

    def test_fetches_exactly_the_commit_and_reuses_it(self, lab_settings, lab_repo, tmp_path):
        repo, commit = lab_repo
        dest = tmp_path / "checkout"
        labs.fetch_commit(repo, commit, dest)
        assert _git(dest, "rev-parse", "HEAD") == commit
        assert (dest / "lab.toml").read_bytes() == MANIFEST
        marker = dest / "learner-notes.txt"
        marker.write_text("kept")
        labs.fetch_commit(repo, commit, dest)  # already there: not fetched again
        assert marker.read_text() == "kept"

    def test_unknown_commit_fails(self, lab_settings, lab_repo, tmp_path):
        repo, _ = lab_repo
        with pytest.raises(labs.LabError) as error:
            labs.fetch_commit(repo, "f" * 40, tmp_path / "checkout")
        assert error.value.status == 502


class TestPrepare:
    def test_asks_once_per_lab_version(self, client, lab_repo):
        lab = _prepare(client, lab_repo)
        assert lab["lab_id"] == "demo-lab" and lab["commit"] == lab_repo[1]
        assert [a["name"] for a in lab["actions"]] == ["hello", "two", "slow", "hangs"]
        assert len(client.prompts) == 1
        prompt = client.prompts[0]
        assert SITE in prompt and lab_repo[1] in prompt
        assert "$ python -c 'print('\"'\"'hello from the lab'\"'\"')'" in prompt

        _prepare(client, lab_repo)
        assert len(client.prompts) == 1  # approved already: no second prompt

    def test_declined_lab_cannot_run(self, client, lab_repo):
        repo, commit = lab_repo
        with patch.object(labs, "approval_prompt", lambda description: False):
            declined = client.post("/labs/prepare", json={"repo": repo, "commit": commit})
        assert declined.status_code == 403
        started = client.post(
            "/labs/runs", json={"lab_id": "demo-lab", "commit": commit, "action": "hello"}
        )
        assert started.status_code == 409  # never prepared

    def test_missing_manifest(self, client, lab_settings, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        _git(repo, "init", "--quiet")
        _git(repo, "config", "uploadpack.allowReachableSHA1InWant", "true")
        (repo / "README").write_text("no lab here")
        _git(repo, "add", "README")
        _git(repo, "commit", "--quiet", "-m", "x")
        response = client.post(
            "/labs/prepare", json={"repo": repo.as_uri(), "commit": _git(repo, "rev-parse", "HEAD")}
        )
        assert response.status_code == 404


class TestRuns:
    def test_runs_steps_in_order_and_reads_output_by_offset(self, client, lab_repo):
        lab = _prepare(client, lab_repo)
        run = _start(client, lab, "hello").json()
        done = _wait(client, run["run_id"])
        assert done["status"] == "succeeded" and done["exit_code"] == 0
        assert done["output"].startswith("$ python -c")
        assert "hello from the lab\n" in done["output"]
        later = client.get(f"/labs/runs/{run['run_id']}", params={"offset": done["next_offset"]})
        assert later.json()["output"] == ""

    def test_first_failing_step_ends_the_action(self, client, lab_repo):
        lab = _prepare(client, lab_repo)
        done = _wait(client, _start(client, lab, "two").json()["run_id"])
        assert done["status"] == "failed" and done["exit_code"] == 3
        assert "first" in done["output"] and "never" not in done["output"]

    def test_stop_and_one_run_at_a_time(self, client, lab_repo):
        lab = _prepare(client, lab_repo)
        run = _start(client, lab, "slow").json()
        busy = _start(client, lab, "hello")
        assert busy.status_code == 409
        time.sleep(0.5)
        stopped = client.post(f"/labs/runs/{run['run_id']}/stop").json()
        assert stopped["status"] == "stopped"
        assert "399" not in stopped["output"]
        assert _start(client, lab, "hello").status_code == 200  # the lab is free again

    def test_timeout(self, client, lab_repo):
        lab = _prepare(client, lab_repo)
        done = _wait(client, _start(client, lab, "hangs").json()["run_id"])
        assert done["status"] == "timed_out" and "timed out" in done["output"]

    def test_unknown_action(self, client, lab_repo):
        lab = _prepare(client, lab_repo)
        assert _start(client, lab, "rm-rf").status_code == 404

    def test_other_sites_cannot_see_or_stop_a_run(self, client, lab_repo):
        lab = _prepare(client, lab_repo)
        run = _start(client, lab, "hello").json()
        app.dependency_overrides[require_lab_auth] = lambda: "https://evil.example"
        assert client.get(f"/labs/runs/{run['run_id']}").status_code == 404
        assert client.post(f"/labs/runs/{run['run_id']}/stop").status_code == 404
        assert client.get("/labs/runs").json() == []
        app.dependency_overrides[require_lab_auth] = lambda: SITE
        assert [r["run_id"] for r in client.get("/labs/runs").json()] == [run["run_id"]]


class TestScopes:
    def _request(self, origin: str) -> MagicMock:
        request = MagicMock()
        request.headers = {"origin": origin}
        return request

    def _paired(self, mock_settings, scope: str) -> tuple[PairingManager, str]:
        manager = PairingManager()
        code = manager.initiate_pairing(SITE, scope)
        token = manager.approve_pairing(code)
        assert token is not None
        return manager, token

    async def test_actions_scope_cannot_run_code(self, mock_settings):
        mock_settings.require_pairing = True
        manager, token = self._paired(mock_settings, "actions")
        with patch("agent_runtime.auth.pairing_manager", manager):
            assert await require_lab_auth(self._request(SITE), f"Bearer {token}") == SITE
            with pytest.raises(HTTPException) as error:
                await require_auth(self._request(SITE), f"Bearer {token}")
        assert error.value.status_code == 403 and "actions" in error.value.detail

    async def test_code_scope_and_old_pairings_can_do_both(self, mock_settings):
        mock_settings.require_pairing = True
        manager, token = self._paired(mock_settings, "code")
        del manager._paired_origins[SITE]["scope"]  # as saved before scopes existed
        with patch("agent_runtime.auth.pairing_manager", manager):
            assert await require_auth(self._request(SITE), f"Bearer {token}") == SITE
            assert await require_lab_auth(self._request(SITE), f"Bearer {token}") == SITE

    def test_unknown_scope_is_refused(self, mock_settings):
        with pytest.raises(ValueError, match="scope"):
            PairingManager().initiate_pairing(SITE, "root")

    def test_pairing_request_passes_the_scope_to_the_prompt(self):
        with (
            patch("agent_runtime.api.pairing.settings.require_pairing", True),
            patch(
                "agent_runtime.api.pairing.pairing_manager.initiate_pairing", return_value="c"
            ) as initiate,
            patch(
                "agent_runtime.api.pairing.pairing_manager.prompt_for_pairing", return_value=True
            ) as prompt,
            patch("agent_runtime.api.pairing.pairing_manager.approve_pairing", return_value="t"),
            TestClient(app) as test_client,
        ):
            ok = test_client.post(
                "/pairing/request", json={"scope": "actions"}, headers={"Origin": SITE}
            )
            bad = test_client.post(
                "/pairing/request", json={"scope": "root"}, headers={"Origin": SITE}
            )
        assert ok.json() == {"origin": SITE, "token": "t", "scope": "actions"}
        assert initiate.call_args.args == (SITE, "actions")
        assert prompt.call_args.args[2] == "actions"
        assert bad.status_code == 422
