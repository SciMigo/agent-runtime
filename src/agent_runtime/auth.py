"""Authentication and pairing for Agent Runtime.

Handles:
- Origin allowlist management
- Pairing flow for new origins
- Token-based authentication
"""

import asyncio
import hashlib
import ipaddress
import json
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request

from agent_runtime.config import settings

# What a paired origin may do. "code" runs any Python through kernels (/kernel, /cell) and
# includes lab actions; "actions" only runs the named actions of labs the user approved (/labs).
# Origins paired before scopes existed have "code".
SCOPES = ("code", "actions")
SCOPE_DESCRIPTIONS = {
    "code": "run ANY Python code on this computer, with your permissions",
    "actions": "run only the named actions of labs you approve one by one",
}

# One approval prompt at a time in the terminal, for pairing and lab approvals alike.
terminal_prompt_lock = asyncio.Lock()


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def is_valid_web_origin(origin: str) -> bool:
    """Accept HTTPS origins, plus HTTP origins on literal loopback hosts."""
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
    except ValueError:
        return False

    structurally_valid = bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )
    if not structurally_valid:
        return False
    return parsed.scheme == "https" or _is_loopback_hostname(parsed.hostname or "")


def is_loopback_origin(origin: str) -> bool:
    """Return whether an HTTP(S) origin is a literal local origin."""
    if not is_valid_web_origin(origin):
        return False

    return _is_loopback_hostname(urlsplit(origin).hostname or "")


class PairingManager:
    """Manages origin pairing and authentication."""

    def __init__(self) -> None:
        self._paired_origins: dict[str, dict[str, Any]] = {}
        self._pending_pairings: dict[str, dict[str, str]] = {}
        self._tokens: dict[str, str] = {}  # token -> origin
        self._load_paired_origins()

    def _load_paired_origins(self) -> None:
        """Load paired origins from disk."""
        settings.ensure_dirs()
        path = settings.paired_origins_file

        if path.exists():
            try:
                with open(path) as f:
                    self._paired_origins = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._paired_origins = {}

    def _save_paired_origins(self) -> None:
        """Save paired origins to disk."""
        settings.ensure_dirs()
        path = settings.paired_origins_file

        with open(path, "w") as f:
            json.dump(self._paired_origins, f, indent=2, default=str)

    def is_origin_paired(self, origin: str) -> bool:
        """Check if an origin is already paired."""
        return origin in self._paired_origins

    def get_allowed_origins(self) -> list[str]:
        """Get explicitly paired origins.

        Loopback origins on any port are trusted dynamically and therefore are
        not represented by this finite list.
        """
        return sorted(self._paired_origins)

    def initiate_pairing(self, origin: str, scope: str = "code") -> str:
        """Start a pairing request for a new origin.

        Returns a pairing code that must be approved.
        """
        if not is_valid_web_origin(origin):
            raise ValueError("Pairing requires a valid HTTP(S) Origin header")
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {', '.join(SCOPES)}")

        pairing_code = secrets.token_urlsafe(8)
        expires_at = datetime.utcnow() + timedelta(seconds=settings.pairing_timeout)
        self._pending_pairings[pairing_code] = {
            "origin": origin,
            "scope": scope,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        return pairing_code

    def cancel_pairing(self, pairing_code: str) -> None:
        """Discard a pending pairing request."""
        self._pending_pairings.pop(pairing_code, None)

    def prompt_for_pairing(self, origin: str, pairing_code: str, scope: str = "code") -> bool:
        """Prompt user to approve pairing (CLI interaction).

        Returns True if approved.
        """
        print(f"\n{'=' * 50}")
        print("New pairing request")
        print(f"{'=' * 50}")
        print(f"Origin: {origin}")
        print(f"Access: {scope} - this site could {SCOPE_DESCRIPTIONS.get(scope, scope)}")
        print(f"Pairing code: {pairing_code}")
        print(f"{'=' * 50}")

        try:
            response = input("Approve this connection? [y/N]: ").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def approve_pairing(self, pairing_code: str) -> str | None:
        """Approve a pending pairing request.

        Returns a token for the paired origin, or None if invalid.
        """
        if pairing_code not in self._pending_pairings:
            return None

        pairing = self._pending_pairings.pop(pairing_code)
        origin = pairing["origin"]

        # Check if expired
        expires_at = datetime.fromisoformat(pairing["expires_at"])
        if datetime.utcnow() > expires_at:
            return None

        # Generate auth token for this origin
        token = secrets.token_urlsafe(32)

        # Store the pairing
        self._paired_origins[origin] = {
            "paired_at": datetime.utcnow().isoformat(),
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "scope": pairing.get("scope", "code"),
        }
        self._tokens[token] = origin
        self._save_paired_origins()

        return token

    def validate_token(self, token: str, origin: str) -> bool:
        """Validate a token for an origin."""
        if not settings.require_pairing:
            return True

        if token in self._tokens:
            return self._tokens[token] == origin

        # Check against stored hash
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if origin in self._paired_origins:
            stored_hash = self._paired_origins[origin].get("token_hash")
            if stored_hash == token_hash:
                self._tokens[token] = origin  # Cache for future lookups
                return True

        return False

    def scope_of(self, origin: str) -> str:
        """The access a paired origin was granted; "code" for pairings made before scopes."""
        return str(self._paired_origins.get(origin, {}).get("scope", "code"))

    def revoke_origin(self, origin: str) -> bool:
        """Revoke pairing for an origin."""
        if origin in self._paired_origins:
            del self._paired_origins[origin]
            # Remove any cached tokens
            self._tokens = {t: o for t, o in self._tokens.items() if o != origin}
            self._save_paired_origins()
            return True
        return False


# Global pairing manager
pairing_manager = PairingManager()


def get_allowed_origins() -> list[str]:
    """Get list of allowed origins for CORS."""
    return pairing_manager.get_allowed_origins()


async def _authenticate(request: Request, authorization: str | None) -> tuple[str, str]:
    """Return the request's origin and the scope it holds, or raise 401/403."""
    origin = request.headers.get("origin", "")

    if not settings.require_pairing:
        return origin, "code"

    if is_loopback_origin(origin):
        return origin, "code"

    # Require authorization header
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header required",
        )

    # Parse Bearer token
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization format. Use: Bearer <token>",
        )

    token = authorization[7:]

    if not pairing_manager.validate_token(token, origin):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired token",
        )

    return origin, pairing_manager.scope_of(origin)


async def require_auth(
    request: Request,
    authorization: str | None = Header(None),
) -> str:
    """Dependency for code execution (kernels, cells): requires the "code" scope.

    Returns the validated origin.
    """
    origin, scope = await _authenticate(request, authorization)
    if scope != "code":
        raise HTTPException(
            status_code=403,
            detail=f"This origin is paired for '{scope}' only; running code needs 'code'",
        )
    return origin


async def require_lab_auth(
    request: Request,
    authorization: str | None = Header(None),
) -> str:
    """Dependency for lab actions (/labs): either scope. Returns the validated origin."""
    origin, _scope = await _authenticate(request, authorization)
    return origin
