import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_metrics_returns_prometheus_text(client):
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "genie_" in r.text
