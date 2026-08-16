"""End-to-end orchestration pipeline tests (doc 2.2 steps 3-9 plus the
rerun loop, step 11) - the ad hoc manual verification done while building
this out, made permanent. Not one of doc section 9's named categories on
its own; it's the connective-tissue test making sure the individually
unit-tested pieces (guardrails, agents, confidence gate, retrieval) are
actually wired together correctly end to end.
"""
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from geniebot.db.models import Classification as DBClassification
from geniebot.db.models import DocType, Environment, Incident, KBChunk
from geniebot.kb.vector_store import VectorRecord
from geniebot.orchestration.pipeline import (
    PipelineContext,
    process_incident_from_log,
    process_rerun,
)
from geniebot.settings import get_guardrail_config, get_settings, get_taxonomy, get_thresholds
from geniebot.state_machine import IncidentStatus


def _make_ctx(llm_client, vector_store, *, min_similarity: float = 0.0) -> PipelineContext:
    thresholds = deepcopy(get_thresholds())
    thresholds["retrieval"]["min_similarity"] = min_similarity
    settings = get_settings()
    return PipelineContext(
        llm_client=llm_client,
        vector_store=vector_store,
        thresholds=thresholds,
        guardrail_config=get_guardrail_config(),
        taxonomy=get_taxonomy(),
        embedding_model=settings.embedding_model,
        generation_model=settings.generation_model,
    )


async def _seed_connectivity_precedent(session, vector_store, llm_client) -> None:
    # Embed text matching what retrieval will actually query with (doc 4.4:
    # normalised error signature + failing module) so the mock's bag-of-
    # words cosine similarity is unambiguously high - a loosely related
    # phrase can come out with near-zero or even negative similarity under
    # that scheme, which isn't what this test is trying to exercise.
    query_text = (
        "ConnectionError ConnectionError: could not connect to host db01 in module "
        "payments/loader.py payments/loader.py"
    )
    embedding = (await llm_client.embed([query_text], model="text-embedding-small")).vectors[0]
    row = KBChunk(
        doc_id="doc-1", doc_type=DocType.SOP, content="Confirm connectivity, then rerun.",
        embedding=embedding, error_category="CONNECTIVITY", source_ref="SOP-CONNECTIVITY-01",
        classification=DBClassification.INTERNAL, index_version="v1",
        effective_from=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    await vector_store.upsert(
        [VectorRecord(chunk_id=row.chunk_id, embedding=embedding, error_category="CONNECTIVITY", index_version="v1")]
    )
    await vector_store.set_active_index_version("v1")


@pytest.mark.asyncio
async def test_happy_path_reaches_awaiting_review_as_auto_resolve_candidate(session, vector_store, llm_client):
    await _seed_connectivity_precedent(session, vector_store, llm_client)

    incident = Incident(
        bot_id="payments-loader", job_run_id="run-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    ctx = _make_ctx(llm_client, vector_store)
    await process_incident_from_log(
        session, incident.incident_id, "ConnectionError: could not connect to host db01 in module payments/loader.py\n", ctx
    )

    await session.refresh(incident)
    assert incident.status == IncidentStatus.AWAITING_REVIEW
    assert incident.diagnosis["resolution_type"] == "controlled_rerun"
    assert incident.diagnosis["confidence"] >= 0.8
    assert incident.template_payload is not None
    assert incident.token_cost_usd > 0


@pytest.mark.asyncio
async def test_no_precedent_reaches_awaiting_review_via_escalation(session, vector_store, llm_client):
    # no KB seeded - the mock diagnostic agent should escalate
    await vector_store.set_active_index_version("v1")

    incident = Incident(
        bot_id="unknown-bot", job_run_id="run-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    ctx = _make_ctx(llm_client, vector_store)
    await process_incident_from_log(
        session, incident.incident_id, "ConnectionError: could not connect to host db01\n", ctx
    )

    await session.refresh(incident)
    assert incident.status == IncidentStatus.AWAITING_REVIEW
    assert incident.diagnosis["resolution_type"] == "escalate"
    assert incident.diagnosis["insufficient_information"] is True


@pytest.mark.asyncio
async def test_unparseable_log_routes_to_manual_fallback(session, vector_store, llm_client):
    incident = Incident(
        bot_id="b1", job_run_id="r1", user_id="alice", environment=Environment.PRODUCTION,
        log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    ctx = _make_ctx(llm_client, vector_store)
    await process_incident_from_log(session, incident.incident_id, "no recognisable signal at all", ctx)

    await session.refresh(incident)
    assert incident.status == IncidentStatus.MANUAL_FALLBACK


@pytest.mark.asyncio
async def test_rerun_loop_increments_count_and_returns_to_awaiting_review(session, vector_store, llm_client):
    await _seed_connectivity_precedent(session, vector_store, llm_client)

    incident = Incident(
        bot_id="payments-loader", job_run_id="run-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    ctx = _make_ctx(llm_client, vector_store)
    await process_incident_from_log(
        session, incident.incident_id, "ConnectionError: could not connect to host db01 in module payments/loader.py\n", ctx
    )
    await session.refresh(incident)
    assert incident.status == IncidentStatus.AWAITING_REVIEW

    # simulate the API's AWAITING_REVIEW -> RERUN_APPROVED transition that
    # normally precedes calling process_rerun
    from geniebot.state_machine import transition

    incident.status = transition(incident.status, IncidentStatus.RERUN_APPROVED)
    await session.commit()

    await process_rerun(session, incident.incident_id, ctx)
    await session.refresh(incident)
    assert incident.status == IncidentStatus.AWAITING_REVIEW
    assert incident.rerun_count == 1


@pytest.mark.asyncio
async def test_rerun_loop_cap_blocks_after_max_reruns(session, vector_store, llm_client):
    await _seed_connectivity_precedent(session, vector_store, llm_client)

    incident = Incident(
        bot_id="payments-loader", job_run_id="run-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
        rerun_count=2,  # thresholds.yaml default max_controlled_reruns_per_incident is 2
        token_cost_usd=Decimal(0),
    )
    session.add(incident)
    await session.commit()

    from geniebot.state_machine import transition

    incident.status = transition(incident.status, IncidentStatus.SCREENED)
    incident.status = transition(incident.status, IncidentStatus.PARSED)
    incident.status = transition(incident.status, IncidentStatus.DIAGNOSED)
    incident.status = transition(incident.status, IncidentStatus.AUTO_RESOLVE_CANDIDATE)
    incident.status = transition(incident.status, IncidentStatus.AWAITING_REVIEW)
    incident.status = transition(incident.status, IncidentStatus.RERUN_APPROVED)
    await session.commit()

    ctx = _make_ctx(llm_client, vector_store)
    await process_rerun(session, incident.incident_id, ctx)

    await session.refresh(incident)
    assert incident.status == IncidentStatus.MANUAL_FALLBACK
