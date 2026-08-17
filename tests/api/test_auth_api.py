"""POST /auth/dev-token - the local dev token generator (api/routers/auth.py).
Validates the requested role against api/deps.py's placeholder
_DEV_USER_ROLES directory rather than trusting whatever role the caller
asks for."""
from __future__ import annotations

import os

import jwt
import pytest

from geniebot.settings import get_settings


def _set_env(**kwargs: str | None) -> dict[str, str | None]:
    saved: dict[str, str | None] = {}
    for k, v in kwargs.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.mark.asyncio
async def test_issue_dev_token_with_matching_role(client):
    r = await client.post("/auth/dev-token", json={"username": "carol", "role": "l2_support"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "carol"
    assert body["role"] == "l2_support"

    settings = get_settings()
    payload = jwt.decode(body["token"], settings.api_dev_shared_secret, algorithms=["HS256"])
    assert payload["sub"] == "carol"
    assert payload["role"] == "l2_support"


@pytest.mark.asyncio
async def test_issue_dev_token_rejects_role_mismatch(client):
    r = await client.post("/auth/dev-token", json={"username": "carol", "role": "admin"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_issue_dev_token_rejects_unknown_username(client):
    r = await client.post("/auth/dev-token", json={"username": "someone-not-in-the-directory", "role": "end_user"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_issue_dev_token_rejects_invalid_role_literal(client):
    r = await client.post("/auth/dev-token", json={"username": "carol", "role": "superadmin"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_issue_dev_token_disabled_outside_mock_auth_backend(client):
    saved = _set_env(AUTH_BACKEND="openam")
    get_settings.cache_clear()
    try:
        r = await client.post("/auth/dev-token", json={"username": "carol", "role": "l2_support"})
        assert r.status_code == 404
    finally:
        _restore_env(saved)
        get_settings.cache_clear()
