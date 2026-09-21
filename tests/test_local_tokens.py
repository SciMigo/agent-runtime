"""Tests for local client tokens: bearer tokens for processes that have no origin."""

import json
import stat
from unittest.mock import MagicMock, patch

import pytest

from agent_runtime.local_tokens import LocalTokenStore


@pytest.fixture
def store(temp_runtime_dir):
    """A store backed by a file in the temporary runtime directory."""
    with patch("agent_runtime.local_tokens.settings") as mock_settings:
        mock_settings.local_clients_file = temp_runtime_dir / "local_clients.json"
        mock_settings.ensure_dirs = MagicMock()
        yield LocalTokenStore()


class TestIssuing:
    def test_created_token_identifies_its_client(self, store):
        token = store.create("Claude Desktop", "code")

        client = store.lookup(token)
        assert client is not None
        assert client.name == "Claude Desktop"
        assert client.scope == "code"

    def test_unknown_and_empty_tokens_identify_nobody(self, store):
        store.create("tutor", "actions")

        assert store.lookup("not-a-token") is None
        assert store.lookup("") is None
        assert store.lookup(None) is None

    def test_creating_again_replaces_the_previous_token(self, store):
        first = store.create("tutor", "actions")
        second = store.create("tutor", "code")

        assert store.lookup(first) is None
        assert store.lookup(second).scope == "code"
        assert len(store.list_clients()) == 1

    def test_names_must_be_present_and_short(self, store):
        with pytest.raises(ValueError):
            store.create("   ", "code")
        with pytest.raises(ValueError):
            store.create("x" * 65, "code")

    def test_scope_defaults_to_actions_for_a_record_without_one(self, store, temp_runtime_dir):
        path = temp_runtime_dir / "local_clients.json"
        path.write_text(json.dumps({"old": {"token_hash": "deadbeef"}}))

        with patch("agent_runtime.local_tokens.settings") as mock_settings:
            mock_settings.local_clients_file = path
            mock_settings.ensure_dirs = MagicMock()
            reloaded = LocalTokenStore()

        assert reloaded.list_clients()[0].scope == "actions"


class TestStorage:
    def test_the_plaintext_token_is_not_written_to_disk(self, store, temp_runtime_dir):
        token = store.create("tutor", "code")

        stored = (temp_runtime_dir / "local_clients.json").read_text()
        assert token not in stored
        assert json.loads(stored)["tutor"]["token_hash"]

    def test_the_file_is_readable_only_by_its_owner(self, store, temp_runtime_dir):
        store.create("tutor", "code")

        mode = (temp_runtime_dir / "local_clients.json").stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_tokens_survive_a_restart(self, store, temp_runtime_dir):
        token = store.create("tutor", "code")

        with patch("agent_runtime.local_tokens.settings") as mock_settings:
            mock_settings.local_clients_file = temp_runtime_dir / "local_clients.json"
            mock_settings.ensure_dirs = MagicMock()
            reloaded = LocalTokenStore()

        assert reloaded.lookup(token).name == "tutor"

    def test_a_damaged_file_does_not_stop_the_runtime(self, temp_runtime_dir):
        path = temp_runtime_dir / "local_clients.json"
        path.write_text("{ not json")

        with patch("agent_runtime.local_tokens.settings") as mock_settings:
            mock_settings.local_clients_file = path
            mock_settings.ensure_dirs = MagicMock()
            store = LocalTokenStore()

        assert store.list_clients() == []


class TestRevoking:
    def test_revoked_tokens_stop_working(self, store):
        token = store.create("tutor", "code")

        assert store.revoke("tutor") is True
        assert store.lookup(token) is None

    def test_revoking_an_unknown_client_reports_it(self, store):
        assert store.revoke("nobody") is False

    def test_clients_are_listed_by_name(self, store):
        store.create("b-client", "code")
        store.create("a-client", "actions")

        assert [c.name for c in store.list_clients()] == ["a-client", "b-client"]


class TestAnotherProcess:
    """The CLI issues and revokes tokens while the runtime is serving, in a separate process."""

    @pytest.fixture
    def stores(self, temp_runtime_dir):
        """A factory of stores over one file, standing in for separate processes."""
        path = temp_runtime_dir / "local_clients.json"
        with patch("agent_runtime.local_tokens.settings") as mock_settings:
            mock_settings.local_clients_file = path
            mock_settings.ensure_dirs = MagicMock()
            yield LocalTokenStore

    def test_a_token_issued_after_startup_works_without_a_restart(self, stores):
        serving = stores()  # started before the token existed
        assert serving.list_clients() == []

        token = stores().create("tutor", "code")

        assert serving.lookup(token).name == "tutor"

    def test_a_revoked_token_stops_working_without_a_restart(self, stores):
        cli = stores()
        token = cli.create("tutor", "code")
        serving = stores()
        assert serving.lookup(token) is not None

        cli.revoke("tutor")

        assert serving.lookup(token) is None

    def test_issuing_a_token_keeps_one_issued_meanwhile(self, stores):
        first = stores()
        second = stores()
        kept = first.create("kept", "actions")

        added = second.create("added", "code")

        assert {c.name for c in stores().list_clients()} == {"kept", "added"}
        assert stores().lookup(kept) is not None
        assert stores().lookup(added) is not None
