import pytest

from geniebot.api.deps import create_dev_token


@pytest.mark.asyncio
async def test_get_config_returns_thresholds_guardrails_taxonomy(client):
    r = await client.get("/admin/config")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"thresholds", "guardrails", "taxonomy"}


@pytest.mark.asyncio
async def test_killswitch_defaults_enabled(client):
    r = await client.get("/admin/killswitch")
    assert r.status_code == 200
    assert r.json()["automated_processing_enabled"] is True


@pytest.mark.asyncio
async def test_killswitch_requires_auth_to_change(client):
    r = await client.post("/admin/killswitch", json={"enabled": False})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_killswitch_toggle_with_auth(client):
    token = create_dev_token("admin1")
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post("/admin/killswitch", json={"enabled": False}, headers=headers)
    assert r.status_code == 200
    assert r.json()["automated_processing_enabled"] is False

    r2 = await client.get("/admin/killswitch")
    assert r2.json()["automated_processing_enabled"] is False

    # restore for any other test relying on default-enabled behaviour
    await client.post("/admin/killswitch", json={"enabled": True}, headers=headers)


@pytest.mark.asyncio
async def test_invalid_bearer_token_rejected(client):
    r = await client.post(
        "/admin/killswitch", json={"enabled": False}, headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert r.status_code == 401
