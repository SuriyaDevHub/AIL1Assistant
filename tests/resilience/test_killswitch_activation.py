"""doc 9 "Resilience" row: kill-switch activation - doc 10.3 runbook
"Activate kill-switch" scenarios must actually halt automated processing,
not just be a documented step."""
import pytest

from geniebot.db.models import Environment, Incident
from geniebot.llm.mock_client import MockLLMClient
from geniebot.orchestration.pipeline import build_pipeline_context, process_incident_from_log
from geniebot.security.killswitch import is_processing_enabled, set_processing_enabled
from geniebot.state_machine import IncidentStatus


@pytest.mark.asyncio
async def test_killswitch_defaults_to_enabled(session):
    assert await is_processing_enabled(session)


@pytest.mark.asyncio
async def test_killswitch_off_routes_new_incidents_to_manual_fallback(session, vector_store):
    await set_processing_enabled(session, enabled=False, actor="incident-responder")
    assert not await is_processing_enabled(session)

    incident = Incident(
        bot_id="b1", job_run_id="r1", user_id="alice", environment=Environment.PRODUCTION,
        log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    ctx = build_pipeline_context(MockLLMClient(), vector_store)
    await process_incident_from_log(session, incident.incident_id, "ConnectionError: db01 unreachable\n", ctx)

    await session.refresh(incident)
    assert incident.status == IncidentStatus.MANUAL_FALLBACK
    # the killswitch check happens before any agent call - parse_output must be untouched
    assert incident.parse_output is None


@pytest.mark.asyncio
async def test_killswitch_can_be_re_enabled(session):
    await set_processing_enabled(session, enabled=False, actor="responder")
    await set_processing_enabled(session, enabled=True, actor="responder")
    assert await is_processing_enabled(session)
