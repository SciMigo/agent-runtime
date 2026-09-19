"""Tests for browser-initiated pairing."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_runtime.server import app


def test_browser_pairing_returns_token_only_after_approval():
    with (
        patch("agent_runtime.api.pairing.settings.require_pairing", True),
        patch("agent_runtime.api.pairing.pairing_manager.initiate_pairing", return_value="code"),
        patch("agent_runtime.api.pairing.pairing_manager.prompt_for_pairing", return_value=True),
        patch("agent_runtime.api.pairing.pairing_manager.approve_pairing", return_value="token"),
        TestClient(app) as client,
    ):
        response = client.post(
            "/pairing/request",
            headers={"Origin": "https://scimigo.com"},
        )

    assert response.status_code == 200
    assert response.json() == {"origin": "https://scimigo.com", "token": "token", "scope": "code"}


def test_browser_pairing_rejects_missing_origin():
    with (
        patch("agent_runtime.api.pairing.settings.require_pairing", True),
        TestClient(app) as client,
    ):
        response = client.post("/pairing/request")

    assert response.status_code == 400
