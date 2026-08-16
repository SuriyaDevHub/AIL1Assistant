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
from geniebot.llm.client import EmbeddingResponse, LLMClient, LLMResponse
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


async def _seed_data_validation_precedent(session, vector_store, llm_client) -> None:
    # query_text mirrors kb/retrieval.py's build_query_text(error_signature,
    # failing_module) exactly, same construction as _seed_connectivity_precedent
    # above - error_signature is "{exception_type} {exception_message}".
    query_text = (
        "SchemaValidationFailure SchemaValidationFailure: missing required field in module "
        "invoice/parser.py invoice/parser.py"
    )
    embedding = (await llm_client.embed([query_text], model="text-embedding-small")).vectors[0]
    row = KBChunk(
        doc_id="doc-2", doc_type=DocType.SOP, content="Validate and rerun once corrected.",
        embedding=embedding, error_category="DATA_VALIDATION", source_ref="SOP-DATAVAL-01",
        classification=DBClassification.INTERNAL, index_version="v1",
        effective_from=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    await vector_store.upsert(
        [VectorRecord(chunk_id=row.chunk_id, embedding=embedding, error_category="DATA_VALIDATION", index_version="v1")]
    )
    await vector_store.set_active_index_version("v1")


@pytest.mark.asyncio
async def test_disabled_category_resolution_type_forced_to_escalate(session, vector_store, llm_client):
    """The confidence gate already forces ESCALATION_DRAFTED for a
    taxonomy-disabled category (config/taxonomy.yaml: DATA_VALIDATION is
    rollout_status disabled) regardless of confidence - agents/confidence_gate.py.
    With KB precedent found, the mock diagnostic agent would normally answer
    resolution_type="controlled_rerun" at confidence 0.82 (see
    llm/mock_client.py's _mock_diagnose has_precedent branch) - this confirms
    the stored diagnosis is overridden to agree with the gate's verdict,
    since the review UI reads resolution_type (not the gate result) to
    decide whether to offer a self-service "try it" flow."""
    await _seed_data_validation_precedent(session, vector_store, llm_client)

    incident = Incident(
        bot_id="invoice-bot", job_run_id="run-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    ctx = _make_ctx(llm_client, vector_store)
    await process_incident_from_log(
        session, incident.incident_id,
        "SchemaValidationFailure: missing required field in module invoice/parser.py\n", ctx,
    )

    await session.refresh(incident)
    assert incident.status == IncidentStatus.AWAITING_REVIEW
    assert incident.diagnosis["resolution_type"] == "escalate"
    assert incident.diagnosis["confidence"] >= 0.8  # underlying model confidence left untouched
    assert incident.template_payload is not None


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
    # doc step 8: Template Generator only runs "where escalation is
    # required" - a self-heal candidate skips it entirely.
    assert incident.template_payload is None
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
    # doc step 8: Template Generator runs "where escalation is required".
    assert incident.template_payload is not None


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


class _BrokenTemplateLLMClient(LLMClient):
    """Wraps a real LLMClient but returns schema-invalid JSON for the
    Template Generator Agent specifically - reproduces a real failure a
    live model (not MockLLMClient, which always emits valid output by
    construction) actually produced: `evidence_refs` as bare strings
    instead of {type, ref} objects, and `error_category` omitted."""

    def __init__(self, delegate: LLMClient):
        self._delegate = delegate

    async def chat_completion(self, *, system: str, user: str, model: str, temperature: float = 0.0, max_output_tokens: int = 1500) -> LLMResponse:
        if "Template Generator Agent" in system:
            import json

            bad_payload = json.dumps(
                {
                    "incident_id": "x", "bot_id": "x", "job_run_id": "x", "environment": "production",
                    "summary": "s", "root_cause": "x", "recommended_action": "y",
                    "evidence_refs": ["line_no:4", "line_no:5"],  # should be [{type, ref}, ...]
                    # error_category deliberately omitted (required field)
                }
            )
            return LLMResponse(content=bad_payload, model=model, prompt_tokens=1, completion_tokens=1, latency_ms=0.0)
        return await self._delegate.chat_completion(
            system=system, user=user, model=model, temperature=temperature, max_output_tokens=max_output_tokens
        )

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResponse:
        return await self._delegate.embed(texts, model=model)


@pytest.mark.asyncio
async def test_template_generator_schema_failure_after_escalation_fails_closed(session, vector_store, llm_client):
    """Regression test: Template Generator Agent (step 8) runs *after* the
    confidence gate has already transitioned the incident to
    ESCALATION_DRAFTED. If it then fails schema validation, the incident
    must reach MANUAL_FALLBACK, not crash-loop on an illegal transition
    (ESCALATION_DRAFTED had no fail-closed route before this fix)."""
    broken_client = _BrokenTemplateLLMClient(llm_client)
    ctx = _make_ctx(broken_client, vector_store)

    incident = Incident(
        bot_id="unknown-bot", job_run_id="run-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()

    # no KB seeded -> escalates -> Template Generator runs -> its broken
    # output fails schema validation
    await process_incident_from_log(
        session, incident.incident_id, "ConnectionError: could not connect to host db01\n", ctx
    )

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
