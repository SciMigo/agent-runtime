"""Bearer tokens for local processes, which are not browser origins.

A browser is identified by the `Origin` header, and a pairing binds a token to that exact
value (see `auth.py`). A process on this machine — an MCP server, a script — sends no origin,
so pairing has nothing to bind to and nothing to show the user in a prompt. Such a client is
identified instead by holding a token this machine issued:

    agent-runtime token create "Claude Desktop" --scope code

Tokens are stored hashed, like pairing tokens, in `<runtime dir>/local_clients.json` (0600).
The plaintext is printed once, at creation, and goes in the client's own configuration.

This is not a boundary against an attacker who already runs code as the user: they could start
a kernel themselves. What it buys is revocation, an audit trail per client, and a scope, so a
client meant only to run a lab's approved actions cannot be talked into running arbitrary
Python. See `docs/security.md`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_runtime.config import settings

NAME_MAX_LENGTH = 64


@dataclass(frozen=True)
class LocalClient:
    """A local process allowed to call the runtime, and what it may do."""

    name: str
    scope: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LocalTokenStore:
    """The local client tokens on this machine."""

    def __init__(self) -> None:
        self._clients: dict[str, dict[str, Any]] = {}
        self._stamp: tuple[int, int, int] | None = None
        self._load()

    def _stamp_of_file(self) -> tuple[int, int, int] | None:
        try:
            info = settings.local_clients_file.stat()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size, info.st_ino)

    def _load(self) -> None:
        self._stamp = self._stamp_of_file()
        path = settings.local_clients_file
        if not path.exists():
            self._clients = {}
            return
        try:
            with open(path) as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._clients = {}
            return
        self._clients = loaded if isinstance(loaded, dict) else {}

    def _refresh(self) -> None:
        """Pick up what another process wrote.

        The CLI issues and revokes tokens while the runtime is serving, in a different process.
        Without this the server would keep whatever it read at startup: a token just created
        would not work until a restart, and - the reason this is a stat on every lookup rather
        than only on a miss - a token just revoked would keep working.
        """
        if self._stamp_of_file() != self._stamp:
            self._load()

    def _save(self) -> None:
        settings.ensure_dirs()
        path = settings.local_clients_file

        # Created unreadable by anyone else, before a token hash is ever written into it.
        def private(file: str, flags: int) -> int:
            return os.open(file, flags, 0o600)

        with open(path, "w", opener=private) as f:
            json.dump(self._clients, f, indent=2)
        os.chmod(path, 0o600)  # a file from an earlier version may be more permissive
        self._stamp = self._stamp_of_file()

    def create(self, name: str, scope: str) -> str:
        """Issue a token for `name`, replacing any token it already had.

        Returns the plaintext token; only its hash is kept.
        """
        name = name.strip()
        if not name or len(name) > NAME_MAX_LENGTH:
            raise ValueError(f"a client name must be 1-{NAME_MAX_LENGTH} characters")

        # Whole-file rewrite, so start from what is on disk. Two people issuing tokens at the
        # same second is not a workload worth locking for.
        self._refresh()

        token = secrets.token_urlsafe(32)
        self._clients[name] = {
            "created_at": datetime.now(UTC).isoformat(),
            "token_hash": _hash(token),
            "scope": scope,
        }
        self._save()
        return token

    def lookup(self, token: str | None) -> LocalClient | None:
        """The client holding this token, or None."""
        if not token:
            return None

        self._refresh()
        presented = _hash(token)
        for name, record in self._clients.items():
            stored = record.get("token_hash", "")
            if stored and hmac.compare_digest(stored, presented):
                return LocalClient(name=name, scope=str(record.get("scope", "actions")))
        return None

    def revoke(self, name: str) -> bool:
        """Forget a client's token. Returns False if there was no such client."""
        self._refresh()
        if name not in self._clients:
            return False
        del self._clients[name]
        self._save()
        return True

    def list_clients(self) -> list[LocalClient]:
        self._refresh()
        return [
            LocalClient(name=name, scope=str(record.get("scope", "actions")))
            for name, record in sorted(self._clients.items())
        ]

    def created_at(self, name: str) -> str:
        self._refresh()
        return str(self._clients.get(name, {}).get("created_at", ""))


# Global local token store
local_tokens = LocalTokenStore()
