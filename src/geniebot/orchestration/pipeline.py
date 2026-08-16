"""Orchestration pipeline - doc section 2.2, steps 3-9 (steps 1-2 are
ingestion; step 10 is the Review UI; steps 11-13 are handled by the API,
integrations and feedback modules once a human has acted). Also implements
the controlled-rerun loop (step 11: "approves a controlled rerun (which
returns to step 6)").

Every failure mode routes the incident to one of the three terminal
failure states (BLOCKED_BY_GUARDRAIL, UNPARSEABLE, PLATFORM_UNAVAILABLE),
each of which the state machine only allows to continue on to
MANUAL_FALLBACK - doc 1.4 "fail closed". Every step's outcome is written to
the audit ledger - doc 1.4 "everything auditable".

Per doc step 8, the Template Generator Agent only runs "where escalation is
required" - AUTO_RESOLVE_CANDIDATE incidents (confidence gate passed) skip
it and reach the reviewer with just the diagnosis, matching the design
doc's self-healing/controlled-rerun path. incident.template_payload stays
None on that path; the Integration Agent (integrations/integration_agent.py)
already falls back to diagnosis fields when building the Jira/email content
if a reviewer approves such an incident straight to L2 instead of a rerun.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import jsonschema
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.agents.base import AgentInvocationRecord, AgentSchemaValidationError
from geniebot.agents.confidence_gate import evaluate_confidence_gate
from geniebot.agents.diagnostic_agent import DiagnosticAgent
from geniebot.agents.log_parser_agent import LogParserAgent
from geniebot.agents.template_generator_agent import TemplateGeneratorAgent
from geniebot.audit import ledger
from geniebot.db.models import Incident
from geniebot.guardrails.input_guardrails import evaluate_input_guardrails
from geniebot.guardrails.output_guardrails import evaluate_output_guardrails
from geniebot.integrations.dedup import compute_error_signature_id
from geniebot.kb.retrieval import retrieve
from geniebot.kb.vector_store import VectorStore
from geniebot.llm.client import LLMClient, LLMPlatformError
from geniebot.observability.metrics import (
    record_agent_error,
    record_agent_invocation,
    record_guardrail_outcome,
    record_incident_outcome,
    record_no_precedent,
)
from geniebot.schemas.diagnosis import DiagnosisOutput, DiagnosticAgentInput
from geniebot.schemas.parser import ParseOutput, ParserAgentInput
from geniebot.schemas.template import TemplateAgentInput, validate_against_template_schema
from geniebot.security.killswitch import is_processing_enabled
from geniebot.settings import (
    get_guardrail_config,
    get_prompt_config,
    get_settings,
    get_taxonomy,
    get_thresholds,
)
from geniebot.state_machine import IncidentStatus, transition
from geniebot.taxonomy_matching import match_error_category


@dataclass(frozen=True)
class PipelineContext:
    llm_client: LLMClient
    vector_store: VectorStore
    thresholds: dict
    guardrail_config: dict
    taxonomy: dict
    embedding_model: str
    generation_model: str


def build_pipeline_context(llm_client: LLMClient, vector_store: VectorStore) -> PipelineContext:
    settings = get_settings()
    return PipelineContext(
        llm_client=llm_client,
        vector_store=vector_store,
        thresholds=get_thresholds(),
        guardrail_config=get_guardrail_config(),
        taxonomy=get_taxonomy(),
        embedding_model=settings.embedding_model,
        generation_model=settings.generation_model,
    )


async def _transition(session: AsyncSession, incident: Incident, target: IncidentStatus) -> None:
    incident.status = transition(incident.status, target)
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="STATE_TRANSITION",
        payload={"target_status": incident.status.value},
    )


async def _fail_closed(
    session: AsyncSession, incident: Incident, terminal_failure: IncidentStatus, reason: str, rule_id: str
) -> None:
    await _transition(session, incident, terminal_failure)
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="FAIL_CLOSED",
        payload={"rule_id": rule_id, "reason": reason, "terminal_status": terminal_failure.value},
    )
    await _transition(session, incident, IncidentStatus.MANUAL_FALLBACK)
    record_incident_outcome(terminal_failure.value)
    await session.commit()


async def _record_agent_records(
    session: AsyncSession, incident: Incident, records: list[AgentInvocationRecord], thresholds: dict
) -> None:
    rate = float(thresholds["budget"]["cost_per_1k_tokens_usd"])
    for r in records:
        await ledger.record(session, incident_id=incident.incident_id, event_type="AGENT_INVOCATION", payload=r.audit_payload())
        if r.success:
            record_agent_invocation(
                agent=r.agent_name,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                latency_ms=r.latency_ms,
                cost_usd=(r.prompt_tokens + r.completion_tokens) / 1000 * rate,
            )
        else:
            record_agent_error(agent=r.agent_name, error_type="schema_validation")
    if records:
        latest = records[-1]
        incident.active_prompt_versions = {
            **(incident.active_prompt_versions or {}),
            latest.agent_name: latest.prompt_version,
        }


def _accumulate_cost(incident: Incident, records: list[AgentInvocationRecord], thresholds: dict) -> None:
    rate = Decimal(str(thresholds["budget"]["cost_per_1k_tokens_usd"]))
    total_tokens = sum(r.prompt_tokens + r.completion_tokens for r in records)
    incident.token_cost_usd = (incident.token_cost_usd or Decimal(0)) + (
        Decimal(total_tokens) / Decimal(1000)
    ) * rate


def _cumulative_tokens(incident: Incident, thresholds: dict) -> int:
    rate = Decimal(str(thresholds["budget"]["cost_per_1k_tokens_usd"]))
    if rate == 0:
        return 0
    return int((incident.token_cost_usd / rate) * 1000)


async def process_incident_from_log(
    session: AsyncSession, incident_id: str, raw_log_text: str, ctx: PipelineContext
) -> None:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id}")

    if not await is_processing_enabled(session):
        await _fail_closed(
            session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, "automated processing disabled by killswitch", "KILLSWITCH"
        )
        return

    thresholds = ctx.thresholds
    guardrail_cfg = ctx.guardrail_config["input_guardrails"]

    # --- Step 3: input guardrails ---
    input_result = evaluate_input_guardrails(
        raw_log_text,
        max_chars=thresholds["payload"]["max_log_chars"],
        block_above=guardrail_cfg["data_classification"]["block_above"],
        injection_action=guardrail_cfg["injection_screening"]["action_on_detection"],
        redaction_enabled=guardrail_cfg["redaction"]["enabled"],
        classification_enabled=guardrail_cfg["data_classification"]["enabled"],
        injection_screening_enabled=guardrail_cfg["injection_screening"]["enabled"],
        payload_capping_enabled=guardrail_cfg["payload_capping"]["enabled"],
    )
    for outcome in input_result.outcomes:
        await ledger.record(
            session, incident_id=incident.incident_id, event_type="GUARDRAIL_EVALUATION", payload=vars(outcome)
        )
        record_guardrail_outcome(rule_id=outcome.rule_id, passed=outcome.passed)
    if input_result.blocked:
        await _fail_closed(
            session,
            incident,
            IncidentStatus.BLOCKED_BY_GUARDRAIL,
            input_result.block_reason or "input guardrail blocked",
            input_result.block_reason or "INPUT_GUARDRAIL",
        )
        return

    await _transition(session, incident, IncidentStatus.SCREENED)
    await session.commit()

    redacted_log = input_result.redacted_text

    # --- Step 4: Log Parser Agent ---
    parser_agent = LogParserAgent(ctx.llm_client, get_prompt_config("log_parser.v1.yaml"))
    try:
        parse_output, records = await parser_agent.run(
            ParserAgentInput(
                incident_id=incident.incident_id,
                bot_id=incident.bot_id,
                job_run_id=incident.job_run_id,
                environment=incident.environment.value,
                redacted_log=redacted_log,
            )
        )
    except AgentSchemaValidationError as exc:
        await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc), "AGENT_SCHEMA_VALIDATION")
        return
    except LLMPlatformError as exc:
        await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc), "LLM_PLATFORM_ERROR")
        return

    await _record_agent_records(session, incident, records, thresholds)
    _accumulate_cost(incident, records, thresholds)
    incident.parse_output = parse_output.model_dump(mode="json")

    if parse_output.parse_status == "unparseable":
        await session.commit()
        await _fail_closed(session, incident, IncidentStatus.UNPARSEABLE, "log parser returned unparseable", "PARSE_UNPARSEABLE")
        return

    await _transition(session, incident, IncidentStatus.PARSED)
    await session.commit()

    await _run_diagnosis_onwards(session, incident, parse_output, ctx)


async def process_rerun(session: AsyncSession, incident_id: str, ctx: PipelineContext) -> None:
    """doc 2.2 step 11: reviewer-approved controlled rerun, returns to step
    6. The caller (API review endpoint) has already transitioned the
    incident AWAITING_REVIEW -> RERUN_APPROVED before invoking this."""
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id}")

    thresholds = ctx.thresholds
    max_reruns = thresholds["loop"]["max_controlled_reruns_per_incident"]
    if incident.rerun_count >= max_reruns:
        await _fail_closed(session, incident, IncidentStatus.BLOCKED_BY_GUARDRAIL, "loop cap exceeded", "LOOP_CAP_EXCEEDED")
        return

    if not await is_processing_enabled(session):
        await _fail_closed(
            session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, "automated processing disabled by killswitch", "KILLSWITCH"
        )
        return

    incident.rerun_count += 1
    await session.commit()

    # RERUN_APPROVED -> DIAGNOSED happens inside _run_diagnosis_onwards
    # once a fresh diagnosis actually exists - don't transition here too,
    # or the DIAGNOSED -> DIAGNOSED self-transition below is rejected by
    # the state machine.
    parse_output = ParseOutput.model_validate(incident.parse_output)
    await _run_diagnosis_onwards(session, incident, parse_output, ctx)


async def ensure_template_payload(session: AsyncSession, incident: Incident, ctx: PipelineContext) -> None:
    """Lazily runs the Template Generator Agent for an incident that reached
    AWAITING_REVIEW without one - an AUTO_RESOLVE_CANDIDATE, per doc step 8
    only generates a template "where escalation is required", so a self-heal
    candidate has none. Called when the end user then reports the L1 fix
    didn't work after all (api/routers/incidents.py's "escalated" decision),
    so escalation is now required and a template is needed for the Jira
    ticket. No-op if a template already exists (the normal escalation path,
    where the pipeline already generated one up front).

    Doesn't re-run retrieval - the original diagnosis (with its own
    citations) is reused as-is; template_precedents is just extra generation
    context, not required for a schema-valid template.
    """
    if incident.template_payload is not None:
        return

    parse_output = ParseOutput.model_validate(incident.parse_output)
    diagnosis = DiagnosisOutput.model_validate(incident.diagnosis)
    template_agent = TemplateGeneratorAgent(ctx.llm_client, get_prompt_config("template_generator.v1.yaml"))
    template_payload, records = await template_agent.run(
        TemplateAgentInput(
            incident_id=incident.incident_id,
            bot_id=incident.bot_id,
            job_run_id=incident.job_run_id,
            environment=incident.environment.value,
            log_s3_uri=incident.log_s3_uri,
            parse_output=parse_output,
            diagnosis=diagnosis,
            template_precedents=[],
        )
    )
    # exclude_none: optional fields (log_s3_uri, priority) the model leaves
    # unset serialize to `null` under Pydantic's default dump, but
    # config/template_schema.json types them as plain "string" with no
    # null variant - a present-but-null value fails that check even though
    # the field is correctly not in the schema's "required" list. Omitting
    # unset optional fields entirely satisfies both.
    template_payload_json = template_payload.model_dump(mode="json", exclude_none=True)
    validate_against_template_schema(template_payload_json)

    await _record_agent_records(session, incident, records, ctx.thresholds)
    _accumulate_cost(incident, records, ctx.thresholds)
    incident.template_payload = template_payload_json
    await session.commit()


async def _run_diagnosis_onwards(
    session: AsyncSession, incident: Incident, parse_output: ParseOutput, ctx: PipelineContext
) -> None:
    thresholds = ctx.thresholds

    # Computed here (rather than only later, in dedup.check_duplicate at
    # submission time) so it's available to the /precedent endpoint as soon
    # as diagnosis completes - well before a reviewer has made a decision.
    # check_duplicate recomputes+overwrites the same value at submission
    # time, so this is purely additive.
    incident.error_signature_id = compute_error_signature_id(
        exception_type=parse_output.exception_type,
        failing_module=parse_output.failing_module,
        bot_id=incident.bot_id,
    )

    # --- Step 5: retrieval ---
    error_category = match_error_category(f"{parse_output.exception_type} {parse_output.exception_message}", ctx.taxonomy)
    retrieval_cfg = thresholds["retrieval"]
    active_index_version = await ctx.vector_store.get_active_index_version() or "v1"
    incident.active_index_version = active_index_version

    try:
        retrieval_result = await retrieve(
            session,
            ctx.vector_store,
            ctx.llm_client,
            error_signature=f"{parse_output.exception_type} {parse_output.exception_message}",
            failing_module=parse_output.failing_module,
            error_category=error_category,
            index_version=active_index_version,
            top_n=retrieval_cfg["top_n"],
            min_similarity=retrieval_cfg["min_similarity"],
            rerank_margin=retrieval_cfg["rerank_margin"],
            embedding_model=ctx.embedding_model,
        )
    except LLMPlatformError as exc:
        await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc), "LLM_PLATFORM_ERROR")
        return

    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="RETRIEVAL",
        payload={
            "no_precedent": retrieval_result.no_precedent,
            "chunk_count": len(retrieval_result.chunks),
            "chunk_refs": [c.source_ref for c in retrieval_result.chunks],
            "index_version": active_index_version,
            "error_category": error_category,
        },
    )
    if retrieval_result.no_precedent:
        record_no_precedent(error_category)

    # --- Step 6: Diagnostic Agent ---
    diagnostic_agent = DiagnosticAgent(ctx.llm_client, get_prompt_config("diagnostic.v1.yaml"))
    try:
        diagnosis, records = await diagnostic_agent.run(
            DiagnosticAgentInput(
                incident_id=incident.incident_id,
                error_category=error_category,
                parse_output=parse_output,
                kb_chunks=retrieval_result.chunks,
            )
        )
    except AgentSchemaValidationError as exc:
        await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc), "AGENT_SCHEMA_VALIDATION")
        return
    except LLMPlatformError as exc:
        await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc), "LLM_PLATFORM_ERROR")
        return

    await _record_agent_records(session, incident, records, thresholds)
    _accumulate_cost(incident, records, thresholds)
    incident.diagnosis = diagnosis.model_dump(mode="json")
    await _transition(session, incident, IncidentStatus.DIAGNOSED)
    await session.commit()

    # --- Step 7: Confidence Gate ---
    gate_result = evaluate_confidence_gate(
        diagnosis,
        error_category=error_category,
        taxonomy=ctx.taxonomy,
        auto_resolve_min=thresholds["confidence"]["auto_resolve_min"],
    )
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="CONFIDENCE_GATE",
        payload={"passed": gate_result.passed, "reasons": gate_result.reasons},
    )
    # The gate is the deterministic, policy-aware authority on whether this
    # incident may be self-served (confidence, citations, taxonomy
    # rollout_status) - the model's own resolution_type claim is just a
    # proposal. When the gate fails, force resolution_type to reflect that,
    # so the review UI's "did the suggested fix work?" prompt (which reads
    # resolution_type, not the gate result) never offers a self-service try
    # on an incident the gate has already decided must escalate. Full dict
    # reassignment, not in-place mutation, for SQLAlchemy JSON change
    # tracking.
    if not gate_result.passed and incident.diagnosis.get("resolution_type") != "escalate":
        incident.diagnosis = {**incident.diagnosis, "resolution_type": "escalate"}
    gate_target = IncidentStatus.AUTO_RESOLVE_CANDIDATE if gate_result.passed else IncidentStatus.ESCALATION_DRAFTED
    await _transition(session, incident, gate_target)
    record_incident_outcome(gate_target.value)
    await session.commit()

    # --- Step 8: Template Generator Agent - only "where escalation is
    # required" (doc step 8); self-heal candidates skip straight to review.
    generated_text = f"{diagnosis.root_cause}\n{diagnosis.proposed_resolution}"
    if not gate_result.passed:
        template_agent = TemplateGeneratorAgent(ctx.llm_client, get_prompt_config("template_generator.v1.yaml"))
        try:
            template_payload, records = await template_agent.run(
                TemplateAgentInput(
                    incident_id=incident.incident_id,
                    bot_id=incident.bot_id,
                    job_run_id=incident.job_run_id,
                    environment=incident.environment.value,
                    log_s3_uri=incident.log_s3_uri,
                    parse_output=parse_output,
                    diagnosis=diagnosis,
                    template_precedents=retrieval_result.chunks,
                )
            )
            # exclude_none: optional fields (log_s3_uri, priority) the model
            # leaves unset serialize to `null` under Pydantic's default
            # dump, but config/template_schema.json types them as plain
            # "string" with no null variant - a present-but-null value
            # fails that check even though the field is correctly not in
            # the schema's "required" list. Omitting unset optional fields
            # entirely satisfies both.
            template_payload_json = template_payload.model_dump(mode="json", exclude_none=True)
            validate_against_template_schema(template_payload_json)
        except AgentSchemaValidationError as exc:
            await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc), "AGENT_SCHEMA_VALIDATION")
            return
        except jsonschema.ValidationError as exc:
            await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc.message), "TEMPLATE_SCHEMA_INVALID")
            return
        except LLMPlatformError as exc:
            await _fail_closed(session, incident, IncidentStatus.PLATFORM_UNAVAILABLE, str(exc), "LLM_PLATFORM_ERROR")
            return

        await _record_agent_records(session, incident, records, thresholds)
        _accumulate_cost(incident, records, thresholds)
        incident.template_payload = template_payload_json
        generated_text += f"\n{template_payload.summary}\n{template_payload.recommended_action}"

    # --- Step 9: output guardrails ---
    output_cfg = ctx.guardrail_config["output_guardrails"]
    evidence_line_numbers = {e.line_no for e in parse_output.evidence_lines}
    kb_source_refs = {c.source_ref for c in retrieval_result.chunks}
    citation_refs = [(c.type, c.ref) for c in diagnosis.citations]

    output_result = evaluate_output_guardrails(
        generated_text=generated_text,
        citation_refs=citation_refs,
        evidence_line_numbers=evidence_line_numbers,
        kb_source_refs=kb_source_refs,
        cumulative_cost_usd=incident.token_cost_usd,
        cumulative_tokens=_cumulative_tokens(incident, thresholds),
        cost_ceiling_usd=Decimal(str(thresholds["budget"]["per_incident_cost_ceiling_usd"])),
        token_ceiling=thresholds["budget"]["per_incident_token_ceiling"],
        rerun_count=incident.rerun_count,
        max_reruns=thresholds["loop"]["max_controlled_reruns_per_incident"],
        grounding_enabled=output_cfg["grounding_check"]["enabled"],
        proposal_only_enabled=output_cfg["proposal_only"]["enabled"],
        egress_enabled=output_cfg["sensitive_data_egress"]["enabled"],
        moderation_enabled=output_cfg["moderation"]["enabled"],
        budget_enabled=output_cfg["budget_ceiling"]["enabled"],
        loop_cap_enabled=output_cfg["loop_cap"]["enabled"],
        unresolvable_citation_action=output_cfg["grounding_check"]["unresolvable_citation_action"],
    )
    for outcome in output_result.outcomes:
        await ledger.record(
            session, incident_id=incident.incident_id, event_type="GUARDRAIL_EVALUATION", payload=vars(outcome)
        )
        record_guardrail_outcome(rule_id=outcome.rule_id, passed=outcome.passed)
    if output_result.blocked:
        await session.commit()
        await _fail_closed(
            session,
            incident,
            IncidentStatus.BLOCKED_BY_GUARDRAIL,
            output_result.block_reason or "output guardrail blocked",
            output_result.block_reason or "OUTPUT_GUARDRAIL",
        )
        return

    await _transition(session, incident, IncidentStatus.AWAITING_REVIEW)
    incident.updated_at = datetime.now(UTC)
    await session.commit()
