"""Push-triggered ingestion (api/routers/ingest.py) - an RPA platform that
already knows which object just failed notifies GenieBot directly instead
of GenieBot discovering it by polling. Verifies the same incident-creation
behavior as the polling path (tested indirectly via the pipeline
integration tests), reached through the trigger endpoint instead."""
from __future__ import annotations

import pytest

from geniebot.ingestion.factory import get_event_source


@pytest.mark.asyncio
async def test_rpa_trigger_creates_incident_for_failure_log(client):
    event_source = get_event_source()
    key = "working/c3-test-bot/run-trigger-01/execution.log"
    event_source.write_object(
        key,
        "# environment: production\n"
        "Unhandled Exception: System.Exception: boom\n"
        "   at Foo.Bar() in C:\\x.cs:line 1\n"
        "FATAL job failed\n",
    )

    r = await client.post("/ingest/trigger", json={"bucket": "genie-bot-logs", "key": key, "size": 42})
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] is True
    incident_id = body["incident_id"]

    detail = await client.get(f"/incidents/{incident_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["bot_id"] == "c3-test-bot"
    assert detail_body["job_run_id"] == "run-trigger-01"
    assert detail_body["status"] == "INGESTED"


@pytest.mark.asyncio
async def test_rpa_trigger_ignores_non_failure_log(client):
    event_source = get_event_source()
    key = "working/c3-test-bot/run-trigger-02/execution.log"
    event_source.write_object(key, "# environment: production\nINFO job completed successfully\n")

    r = await client.post("/ingest/trigger", json={"bucket": "genie-bot-logs", "key": key})
    assert r.status_code == 200
    assert r.json() == {"ingested": False, "reason": "not recognised as a failure log"}
