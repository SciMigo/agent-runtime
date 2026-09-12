"""Tests for authentication and pairing system."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_runtime.auth import PairingManager, is_loopback_origin, is_valid_web_origin


class TestOriginValidation:
    def test_loopback_accepts_any_port_and_ip_family(self):
        assert is_loopback_origin("http://localhost:5173")
        assert is_loopback_origin("https://127.0.0.1:8443")
        assert is_loopback_origin("http://[::1]:3000")

    def test_loopback_rejects_lookalikes_and_non_web_origins(self):
        assert not is_loopback_origin("https://localhost.example.com")
        assert not is_loopback_origin("file:///tmp/lab.html")
        assert not is_loopback_origin("https://example.com")

    def test_valid_origin_rejects_paths_and_credentials(self):
        assert is_valid_web_origin("https://scimigo.com")
        assert not is_valid_web_origin("http://scimigo.com")
        assert not is_valid_web_origin("https://scimigo.com/course")
        assert not is_valid_web_origin("https://user@example.com")


class TestPairingManager:
    """Tests for PairingManager class."""

    @pytest.fixture
    def manager(self, temp_runtime_dir):
        """Create a fresh pairing manager for each test."""
        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.paired_origins_file = temp_runtime_dir / "paired_origins.json"
            mock_settings.require_pairing = True
            mock_settings.pairing_timeout = 300
            mock_settings.ensure_dirs = MagicMock()
            return PairingManager()

    def test_init_empty(self, manager):
        """Test manager initializes with empty paired origins."""
        assert manager._paired_origins == {}
        assert manager._pending_pairings == {}
        assert manager._tokens == {}

    def test_load_existing_origins(self, temp_runtime_dir):
        """Test loading existing paired origins from disk."""
        origins_file = temp_runtime_dir / "paired_origins.json"
        origins_file.write_text(
            json.dumps(
                {
                    "https://example.com": {
                        "paired_at": "2024-01-01T00:00:00",
                        "token_hash": "abc123",
                    }
                }
            )
        )

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.paired_origins_file = origins_file
            mock_settings.ensure_dirs = MagicMock()
            manager = PairingManager()

        assert "https://example.com" in manager._paired_origins

    def test_is_origin_paired_true(self, manager):
        """Test checking if an origin is paired."""
        manager._paired_origins["https://example.com"] = {"paired_at": "now"}
        assert manager.is_origin_paired("https://example.com") is True

    def test_is_origin_paired_false(self, manager):
        """Test checking if an origin is not paired."""
        assert manager.is_origin_paired("https://unknown.com") is False

    def test_get_allowed_origins_only_lists_persisted_pairings(self, manager):
        """Loopback trust is dynamic rather than represented as two fixed ports."""
        assert manager.get_allowed_origins() == []

    def test_get_allowed_origins_includes_paired(self, manager):
        """Test allowed origins includes paired origins."""
        manager._paired_origins["https://example.com"] = {"paired_at": "now"}
        allowed = manager.get_allowed_origins()
        assert "https://example.com" in allowed

    def test_initiate_pairing(self, manager):
        """Test initiating a pairing request."""
        pairing_code = manager.initiate_pairing("https://newsite.com")

        assert len(pairing_code) > 0
        assert pairing_code in manager._pending_pairings
        assert manager._pending_pairings[pairing_code]["origin"] == "https://newsite.com"

    def test_approve_pairing_valid(self, manager):
        """Test approving a valid pairing request."""
        pairing_code = manager.initiate_pairing("https://newsite.com")

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.paired_origins_file = Path("/tmp/test_origins.json")
            mock_settings.ensure_dirs = MagicMock()

            token = manager.approve_pairing(pairing_code)

        assert token is not None
        assert len(token) > 0
        assert "https://newsite.com" in manager._paired_origins
        assert pairing_code not in manager._pending_pairings

    def test_approve_pairing_invalid_code(self, manager):
        """Test approving with invalid pairing code."""
        token = manager.approve_pairing("invalid-code")
        assert token is None

    def test_approve_pairing_expired(self, manager):
        """Test approving an expired pairing request."""
        pairing_code = manager.initiate_pairing("https://newsite.com")

        # Manually expire the pairing
        manager._pending_pairings[pairing_code]["expires_at"] = (
            datetime.utcnow() - timedelta(seconds=10)
        ).isoformat()

        token = manager.approve_pairing(pairing_code)
        assert token is None

    def test_validate_token_valid(self, manager):
        """Test validating a correct token."""
        origin = "https://example.com"
        token = "test-token-123"
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        manager._paired_origins[origin] = {"paired_at": "now", "token_hash": token_hash}

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.require_pairing = True
            result = manager.validate_token(token, origin)

        assert result is True
        # Token should be cached
        assert token in manager._tokens

    def test_validate_token_invalid(self, manager):
        """Test validating an incorrect token."""
        origin = "https://example.com"
        manager._paired_origins[origin] = {"paired_at": "now", "token_hash": "correct-hash"}

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.require_pairing = True
            result = manager.validate_token("wrong-token", origin)

        assert result is False

    def test_validate_token_pairing_disabled(self, manager):
        """Test validation passes when pairing is disabled."""
        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.require_pairing = False
            result = manager.validate_token("any-token", "any-origin")

        assert result is True

    def test_revoke_origin(self, manager, temp_runtime_dir):
        """Test revoking a paired origin."""
        manager._paired_origins["https://example.com"] = {"paired_at": "now"}
        manager._tokens["some-token"] = "https://example.com"

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.paired_origins_file = temp_runtime_dir / "paired_origins.json"
            mock_settings.ensure_dirs = MagicMock()
            result = manager.revoke_origin("https://example.com")

        assert result is True
        assert "https://example.com" not in manager._paired_origins
        assert "some-token" not in manager._tokens

    def test_revoke_origin_not_found(self, manager):
        """Test revoking non-existent origin."""
        result = manager.revoke_origin("https://unknown.com")
        assert result is False


class TestRequireAuth:
    """Tests for require_auth dependency."""

    @pytest.mark.asyncio
    async def test_require_auth_pairing_disabled(self):
        """Test auth passes when pairing is disabled."""
        from fastapi import Request

        from agent_runtime.auth import require_auth

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"origin": "https://example.com"}

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.require_pairing = False
            origin = await require_auth(mock_request, None)

        assert origin == "https://example.com"

    @pytest.mark.asyncio
    async def test_require_auth_loopback_any_port_allowed(self):
        """Test loopback origins on arbitrary ports are always allowed."""
        from fastapi import Request

        from agent_runtime.auth import require_auth

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"origin": "http://localhost:5173"}

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.require_pairing = True
            origin = await require_auth(mock_request, None)

        assert origin == "http://localhost:5173"

    @pytest.mark.asyncio
    async def test_require_auth_missing_header(self):
        """Test auth fails without authorization header."""
        from fastapi import HTTPException, Request

        from agent_runtime.auth import require_auth

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"origin": "https://example.com"}

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.require_pairing = True
            with pytest.raises(HTTPException) as exc_info:
                await require_auth(mock_request, None)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_require_auth_invalid_format(self):
        """Test auth fails with invalid authorization format."""
        from fastapi import HTTPException, Request

        from agent_runtime.auth import require_auth

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"origin": "https://example.com"}

        with patch("agent_runtime.auth.settings") as mock_settings:
            mock_settings.require_pairing = True
            with pytest.raises(HTTPException) as exc_info:
                await require_auth(mock_request, "Basic abc123")

        assert exc_info.value.status_code == 401
        assert "Bearer" in exc_info.value.detail
