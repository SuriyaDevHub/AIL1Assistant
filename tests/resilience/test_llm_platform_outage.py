"""doc 9 "Resilience" row: AI platform outage -> fail closed to
PLATFORM_UNAVAILABLE -> MANUAL_FALLBACK (doc 1.4)."""
import pytest

from geniebot.db.models import Environment, Incident
from geniebot.llm.client import EmbeddingResponse, LLMClient, LLMPlatformError, LLMResponse
from geniebot.orchestration.pipeline import build_pipeline_context, process_incident_from_log
from geniebot.state_machine import IncidentStatus


class OutageLLMClient(LLMClient):
    async def chat_completion(self, **kwargs) -> LLMResponse:
        raise LLMPlatformError("simulated internal AI platform outage")

    async def embed(self, texts, *, model) -> EmbeddingResponse:
        raise LLMPlatformError("simulated internal AI platform outage")


@pytest.mark.asyncio
async def test_llm_outage_routes_to_manual_fallback(session, vector_store):
    incident = Incident(
        bot_id="b1", job_run_id="r1", user_id="alice", environment=Environment.PRODUCTION,
        log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    ctx = build_pipeline_context(OutageLLMClient(), vector_store)
    await process_incident_from_log(
        session, incident.incident_id, "ConnectionError: could not connect to host db01\n", ctx
    )

    await session.refresh(incident)
    assert incident.status == IncidentStatus.MANUAL_FALLBACK
