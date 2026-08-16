"""doc 7.1 incident endpoints: list, detail, review decision, template edit."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jsonschema
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.agents.base import AgentSchemaValidationError
from geniebot.api.deps import AuthenticatedUser, get_current_principal, get_current_user, get_session
from geniebot.audit import ledger
from geniebot.db.models import AuditRecord, Environment, Incident, ReviewDecision
from geniebot.feedback.resolution_capture import capture_resolution
from geniebot.feedback.sop_drafter import maybe_draft_sop
from geniebot.integrations.factory import get_jira_client, get_mail_client
from geniebot.integrations.integration_agent import submit_incident
from geniebot.integrations.jira_client import MockJiraServer
from geniebot.kb.factory import get_vector_store
from geniebot.llm.client import LLMPlatformError
from geniebot.llm.factory import get_llm_client
from geniebot.observability.metrics import record_review_decision
from geniebot.orchestration.pipeline import build_pipeline_context, ensure_template_payload, process_rerun
from geniebot.schemas.incident import (
    IncidentDetail,
    IncidentListItem,
    IncidentPrecedent,
    IncidentStats,
    IncidentTimelineEntry,
    JiraTicketDetail,
    PrecedentIncident,
    ReviewDecisionRequest,
    TemplateUpdateRequest,
)
from geniebot.settings import get_generic_l1_checklist, get_settings, get_taxonomy, get_thresholds
from geniebot.state_machine import IncidentStatus, transition

_TERMINAL_STATUSES = {"CLOSED", "MANUAL_FALLBACK"}
_CONFIDENCE_BUCKETS = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
# GenieBot L1 Enhancement Plan, "reduce reflexive escalation" - measure,
# don't block: a comment this short on an escalation isn't useful context
# for L2 and is a weak signal the end user didn't really engage with the
# suggested fix before escalating. Not enforced (a short comment is still a
# real comment), just flagged for the L2/admin dashboard to review.
_LOW_CONTEXT_COMMENT_CHARS = 15

router = APIRouter()


async def _record_transition(session: AsyncSession, incident: Incident, target: IncidentStatus) -> None:
    """Mirrors orchestration/pipeline.py's _transition helper - every status
    change gets a STATE_TRANSITION audit record, which is also what powers
    GET /incidents/{id}/timeline (the pipeline/stage view). Without this,
    review-decision transitions (SUBMITTED, REJECTED, RERUN_APPROVED, ...)
    silently vanished from the audit trail even though the pipeline's own
    transitions were always recorded."""
    incident.status = transition(incident.status, target)
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="STATE_TRANSITION",
        payload={"target_status": incident.status.value},
    )

_DECISION_MAP = {
    "approve": ReviewDecision.APPROVE,
    "reject": ReviewDecision.REJECT,
    "approve_rerun": ReviewDecision.APPROVE_RERUN,
    "resolved": ReviewDecision.RESOLVED,
    "escalated": ReviewDecision.ESCALATED,
}


def _check_visible(incident: Incident, current_user: AuthenticatedUser) -> None:
    """GenieBot L1 Enhancement Plan, "Scope what each role can see": an
    end_user may only see an incident tied to their own bot run;
    l2_support/admin see everything. 403 rather than 404 - this is an
    internal ops tool (not adversarial exposure), so a clear "not yours"
    signal is more useful than obscuring whether the id exists."""
    if current_user.role == "end_user" and incident.user_id != current_user.sub:
        raise HTTPException(403, "you don't have access to this incident")


@router.get("", response_model=list[IncidentListItem])
async def list_incidents(
    status: str | None = None,
    bot_id: str | None = None,
    environment: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: AuthenticatedUser = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> list[IncidentListItem]:
    query = select(Incident)
    if current_user.role == "end_user":
        query = query.where(Incident.user_id == current_user.sub)
    if status:
        try:
            query = query.where(Incident.status == IncidentStatus(status))
        except ValueError:
            raise HTTPException(422, f"unknown status {status!r}") from None
    if bot_id:
        query = query.where(Incident.bot_id == bot_id)
    if environment:
        try:
            query = query.where(Incident.environment == Environment(environment))
        except ValueError:
            raise HTTPException(422, f"unknown environment {environment!r}") from None

    query = query.order_by(Incident.ingested_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    return [IncidentListItem.model_validate(row) for row in result.scalars().all()]


@router.get("/stats", response_model=IncidentStats)
async def get_incident_stats(
    current_user: AuthenticatedUser = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> IncidentStats:
    """Aggregate counts for the dashboard page - registered before
    /{incident_id} so "stats" isn't captured as an incident_id path param.
    l2_support/admin only (GenieBot L1 Enhancement Plan) - not scoped down
    for an end_user, hidden entirely."""
    if current_user.role == "end_user":
        raise HTTPException(403, "dashboard stats are available to L2 support and admin roles only")

    total_incidents = (await session.execute(select(func.count()).select_from(Incident))).scalar_one()

    status_rows = (
        await session.execute(select(Incident.status, func.count()).group_by(Incident.status))
    ).all()
    status_counts = {status.value: count for status, count in status_rows}
    open_count = sum(count for status, count in status_rows if status.value not in _TERMINAL_STATUSES)

    decision_rows = (
        await session.execute(
            select(Incident.decision, func.count())
            .where(Incident.decision.is_not(None))
            .group_by(Incident.decision)
        )
    ).all()
    decision_counts = {decision.value: count for decision, count in decision_rows}

    # confidence lives inside the diagnosis JSON column, so bucketing
    # happens in Python rather than SQL - capped at the most recent 500
    # incidents to bound the query for a demo dataset, not a real
    # pagination scheme.
    diagnoses = (
        await session.execute(
            select(Incident.diagnosis)
            .where(Incident.diagnosis.is_not(None))
            .order_by(Incident.ingested_at.desc())
            .limit(500)
        )
    ).scalars().all()
    confidence_buckets = dict.fromkeys(_CONFIDENCE_BUCKETS, 0)
    confidences: list[float] = []
    for diagnosis in diagnoses:
        confidence = diagnosis.get("confidence") if diagnosis else None
        if confidence is None:
            continue
        confidences.append(confidence)
        bucket_index = min(int(confidence * 5), 4)  # confidence == 1.0 -> last bucket
        confidence_buckets[_CONFIDENCE_BUCKETS[bucket_index]] += 1
    avg_confidence = sum(confidences) / len(confidences) if confidences else None

    cutoff = datetime.now(UTC) - timedelta(hours=24)
    cost_last_24h_usd = (
        await session.execute(
            select(func.coalesce(func.sum(Incident.token_cost_usd), 0)).where(Incident.ingested_at >= cutoff)
        )
    ).scalar_one()

    low_context_escalations_last_24h = (
        await session.execute(
            select(func.count())
            .select_from(AuditRecord)
            .where(
                AuditRecord.event_type == "ESCALATION_QUALITY_FLAG",
                AuditRecord.created_at >= cutoff,
            )
        )
    ).scalar_one()

    recent_rows = (
        await session.execute(select(Incident).order_by(Incident.ingested_at.desc()).limit(8))
    ).scalars().all()

    return IncidentStats(
        total_incidents=total_incidents,
        open_count=open_count,
        status_counts=status_counts,
        decision_counts=decision_counts,
        confidence_buckets=confidence_buckets,
        avg_confidence=avg_confidence,
        cost_last_24h_usd=Decimal(str(cost_last_24h_usd)),
        low_context_escalations_last_24h=low_context_escalations_last_24h,
        recent=[IncidentListItem.model_validate(r) for r in recent_rows],
    )


def _generic_l1_checklist_for(incident: Incident) -> list[str] | None:
    """Fixed, ops-curated fallback steps (config/generic_l1_checklist.yaml) -
    shown only when the diagnostic agent has genuinely nothing precedented
    to offer (resolution_type "escalate" *and* insufficient_information),
    so every issue still gets a fair first attempt before escalation. Never
    shown for guidance/controlled_rerun, which already have real,
    evidence-grounded content of their own, or for an escalate case where
    the model did find something concrete but confidence/policy still
    require L2 - those keep their own diagnosis text as-is."""
    diagnosis = incident.diagnosis or {}
    if diagnosis.get("resolution_type") != "escalate" or not diagnosis.get("insufficient_information"):
        return None
    checklist_cfg = get_generic_l1_checklist()
    category = (incident.template_payload or {}).get("error_category")
    steps = checklist_cfg.get("categories", {}).get(category) if category else None
    return steps or checklist_cfg.get("default")


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: str,
    current_user: AuthenticatedUser = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    _check_visible(incident, current_user)
    detail = IncidentDetail.model_validate(incident)
    detail.generic_l1_checklist = _generic_l1_checklist_for(incident)
    return detail


@router.get("/{incident_id}/jira", response_model=JiraTicketDetail)
async def get_incident_jira_ticket(
    incident_id: str,
    current_user: AuthenticatedUser = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> JiraTicketDetail:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    _check_visible(incident, current_user)
    if not incident.jira_key:
        raise HTTPException(404, "incident has no linked Jira ticket")
    issue = await get_jira_client().get_issue(incident.jira_key)
    if issue is None:
        raise HTTPException(404, f"Jira ticket {incident.jira_key} not found")
    return JiraTicketDetail(
        key=issue.key,
        summary=issue.summary,
        description=issue.description,
        status=issue.status,
        labels=issue.labels,
        comments=issue.comments,
        created_at=issue.created_at,
    )


@router.get("/{incident_id}/timeline", response_model=list[IncidentTimelineEntry])
async def get_incident_timeline(
    incident_id: str,
    current_user: AuthenticatedUser = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> list[IncidentTimelineEntry]:
    """The real, timestamped sequence of states this incident actually
    passed through - orchestration/pipeline.py's _transition helper writes
    one STATE_TRANSITION audit record on every change. Powers the frontend's
    pipeline/stage view (components/PipelineView.tsx)."""
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    _check_visible(incident, current_user)

    result = await session.execute(
        select(AuditRecord)
        .where(AuditRecord.incident_id == incident_id, AuditRecord.event_type == "STATE_TRANSITION")
        .order_by(AuditRecord.created_at.asc())
    )
    return [
        IncidentTimelineEntry(status=record.payload["target_status"], at=record.created_at)
        for record in result.scalars().all()
    ]


def _resolution_summary(incident: Incident) -> str:
    """Mirrors feedback/resolution_capture.py's build_resolved_incident_document
    resolution-text derivation, for consistency with what's actually indexed
    into the KB for this incident."""
    template = incident.template_payload or {}
    diagnosis = incident.diagnosis or {}
    return template.get("recommended_action") or diagnosis.get("proposed_resolution", "")


@router.get("/{incident_id}/precedent", response_model=IncidentPrecedent)
async def get_incident_precedent(
    incident_id: str,
    current_user: AuthenticatedUser = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> IncidentPrecedent:
    """Surfaces prior incidents sharing this incident's error signature,
    before the end user decides resolved-vs-escalate - doc's dedup logic
    (integrations/dedup.py) already links a later escalation to an existing
    Jira ticket instead of creating a duplicate; this tells the end user
    that truth up front instead of leaving them to find out after the fact.

    Deliberate exception to the usual ownership rule (GenieBot L1
    Enhancement Plan): the caller must still own *this* incident (checked
    below, same as get_incident/jira/timeline), but the precedent result
    itself can reference another user's past incident, comment, and
    resolution - that cross-user visibility is the entire point of the
    feature, learning that someone else already solved this exact problem."""
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    _check_visible(incident, current_user)
    if incident.error_signature_id is None:
        return IncidentPrecedent(resolved_precedent=None, open_precedent=None)

    result = await session.execute(
        select(Incident)
        .where(
            Incident.error_signature_id == incident.error_signature_id,
            Incident.incident_id != incident_id,
        )
        .order_by(Incident.updated_at.desc())
    )
    candidates = list(result.scalars().all())

    window_hours = get_thresholds()["dedup"]["exact_match_window_hours"]
    # SQLite (local/dev/test) returns naive datetimes for a DateTime(timezone=True)
    # column regardless of what was stored - comparing against an
    # aware `cutoff` directly raises TypeError. Normalize both sides to
    # naive UTC rather than relying on driver-specific tz behavior (Postgres
    # would return aware values here).
    cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).replace(tzinfo=None)

    def within_window(candidate: Incident) -> bool:
        # dedup.check_duplicate's own candidate pool (_recent_linked_incidents)
        # matches on ingested_at within this same window, regardless of
        # status - matching it here keeps the "escalating will join this
        # ticket" claim truthful to what dedup will actually do.
        if candidate.jira_key is None:
            return False
        ingested_at = candidate.ingested_at
        if ingested_at.tzinfo is not None:
            ingested_at = ingested_at.replace(tzinfo=None)
        return ingested_at >= cutoff

    async def to_precedent(candidate: Incident, *, fetch_live_status: bool) -> PrecedentIncident:
        jira_status = None
        if fetch_live_status and candidate.jira_key:
            issue = await get_jira_client().get_issue(candidate.jira_key)
            if issue is not None:
                jira_status = issue.status
        return PrecedentIncident(
            incident_id=candidate.incident_id,
            job_run_id=candidate.job_run_id,
            at=candidate.updated_at,
            reviewer_id=candidate.reviewer_id,
            reviewer_comment=candidate.reviewer_comment,
            resolution_summary=_resolution_summary(candidate),
            jira_key=candidate.jira_key,
            jira_status=jira_status,
            within_dedup_window=within_window(candidate),
        )

    resolved_candidate = next((c for c in candidates if c.decision == ReviewDecision.RESOLVED), None)
    open_candidate = next(
        (c for c in candidates if c.status == IncidentStatus.SUBMITTED and c.jira_key is not None and within_window(c)),
        None,
    )

    return IncidentPrecedent(
        resolved_precedent=(
            await to_precedent(resolved_candidate, fetch_live_status=False) if resolved_candidate else None
        ),
        open_precedent=(await to_precedent(open_candidate, fetch_live_status=True) if open_candidate else None),
    )


@router.post("/{incident_id}/review")
async def review_incident(
    incident_id: str,
    body: ReviewDecisionRequest,
    reviewer_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    if incident.status != IncidentStatus.AWAITING_REVIEW:
        raise HTTPException(409, f"incident not awaiting review (status={incident.status.value})")
    if body.decision == "escalated" and not (body.comment or "").strip():
        # GenieBot L1 Enhancement Plan, "reduce reflexive escalation without
        # gating it" - light, honest friction: a real sentence about what
        # was tried is both a deterrent to a reflexive click and useful
        # context for L2 either way. Enforced server-side, not just in the
        # UI, since a client-only check is trivially bypassed and wouldn't
        # actually guarantee the audit trail has anything useful in it.
        raise HTTPException(422, "a comment describing what was tried is required when escalating")

    incident.reviewer_id = body.reviewer_id or reviewer_id
    incident.reviewer_comment = body.comment
    incident.decision = _DECISION_MAP[body.decision]
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="REVIEW_DECISION",
        payload={"decision": body.decision, "reviewer_id": incident.reviewer_id, "comment": body.comment},
    )
    record_review_decision(body.decision)

    if body.decision == "approve":
        # Legacy staff-reviewer decision (predates the resolved/escalated
        # redesign) - "approve" means the reviewer is treating this as
        # fully handled, matching its original doc-step-12 semantics:
        # create the ticket and close immediately, same as "resolved".
        await _record_transition(session, incident, IncidentStatus.SUBMITTED)
        await session.commit()

        thresholds = get_thresholds()
        settings = get_settings()
        await submit_incident(
            session,
            incident,
            jira_client=get_jira_client(),
            mail_client=get_mail_client(),
            dedup_window_hours=thresholds["dedup"]["exact_match_window_hours"],
            dedup_fuzzy_threshold=thresholds["dedup"]["fuzzy_similarity_threshold"],
            support_mailbox=settings.genie_support_mailbox,
            resolution_confirmed=True,
        )

    elif body.decision == "reject":
        await _record_transition(session, incident, IncidentStatus.REJECTED)
        await _record_transition(session, incident, IncidentStatus.CLOSED)
        await session.commit()

    elif body.decision == "approve_rerun":
        if body.rerun_overrides and incident.diagnosis:
            incident.diagnosis = {**incident.diagnosis, "rerun_parameters": body.rerun_overrides}
        await _record_transition(session, incident, IncidentStatus.RERUN_APPROVED)
        await session.commit()

        ctx = build_pipeline_context(get_llm_client(), get_vector_store(session))
        await process_rerun(session, incident.incident_id, ctx)

    elif body.decision == "resolved":
        # End user tried the L1-proposed fix and it worked. A Jira ticket is
        # still created (audit trail + KB feedback), but there's no L2 work
        # left to do, so it's closed immediately.
        await _record_transition(session, incident, IncidentStatus.SUBMITTED)
        await session.commit()

        thresholds = get_thresholds()
        settings = get_settings()
        vector_store = get_vector_store(session)
        llm_client = get_llm_client()
        await submit_incident(
            session,
            incident,
            jira_client=get_jira_client(),
            mail_client=get_mail_client(),
            dedup_window_hours=thresholds["dedup"]["exact_match_window_hours"],
            dedup_fuzzy_threshold=thresholds["dedup"]["fuzzy_similarity_threshold"],
            support_mailbox=settings.genie_support_mailbox,
            resolution_confirmed=True,
        )
        # A ticket GenieBot just closed itself will never trigger the real
        # L2 closure webhook, so capture the confirmed resolution into the
        # KB feedback loop right here instead of waiting for one (doc 4.5).
        await capture_resolution(
            session,
            incident,
            vector_store=vector_store,
            llm_client=llm_client,
            taxonomy=get_taxonomy(),
            embedding_model=settings.embedding_model,
        )

    elif body.decision == "escalated":
        if len(body.comment.strip()) < _LOW_CONTEXT_COMMENT_CHARS:
            await ledger.record(
                session,
                incident_id=incident.incident_id,
                event_type="ESCALATION_QUALITY_FLAG",
                payload={"reason": "low_context_comment", "comment_length": len(body.comment.strip())},
            )
        # End user tried the L1-proposed fix and it didn't work - escalate
        # to L2. AUTO_RESOLVE_CANDIDATE incidents reach here with no
        # template (doc step 8 only generates one "where escalation is
        # required" - this is the moment that becomes true), so generate one
        # now if it's still missing; already-escalated incidents already
        # have one and this is a no-op. Done *before* transitioning past
        # AWAITING_REVIEW so a generation failure leaves the incident
        # retriable rather than stranded in SUBMITTED with no template.
        thresholds = get_thresholds()
        settings = get_settings()
        ctx = build_pipeline_context(get_llm_client(), get_vector_store(session))
        try:
            await ensure_template_payload(session, incident, ctx)
        except AgentSchemaValidationError as exc:
            raise HTTPException(502, f"template generation failed: {exc}") from exc
        except jsonschema.ValidationError as exc:
            raise HTTPException(502, f"template schema invalid: {exc.message}") from exc
        except LLMPlatformError as exc:
            raise HTTPException(502, f"LLM platform error: {exc}") from exc

        await _record_transition(session, incident, IncidentStatus.SUBMITTED)
        await session.commit()

        await submit_incident(
            session,
            incident,
            jira_client=get_jira_client(),
            mail_client=get_mail_client(),
            dedup_window_hours=thresholds["dedup"]["exact_match_window_hours"],
            dedup_fuzzy_threshold=thresholds["dedup"]["fuzzy_similarity_threshold"],
            support_mailbox=settings.genie_support_mailbox,
            resolution_confirmed=False,
        )

    return {"incident_id": incident.incident_id, "status": incident.status.value}


@router.put("/{incident_id}/template")
async def update_template(
    incident_id: str,
    body: TemplateUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    if incident.status not in (IncidentStatus.AWAITING_REVIEW,):
        raise HTTPException(409, f"template not editable in status {incident.status.value}")

    incident.template_payload = body.template_payload.model_dump(mode="json")
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="REVIEWER_TEMPLATE_EDIT",
        payload={"reviewer_id": body.reviewer_id},
    )
    await session.commit()
    return {"incident_id": incident.incident_id, "updated": True}


class JiraClosureWebhook(BaseModel):
    issue_key: str


async def _handle_jira_closure(session: AsyncSession, issue_key: str) -> dict:
    """doc 7.2 "closure webhook for feedback" / doc 2.2 step 13: capture the
    resolution into the KB, draft an SOP if the pattern is recurring, and
    close every incident still open and waiting on this ticket. Shared by
    the real webhook below and the local-only simulate-jira-closure demo
    endpoint - same event, two ways of learning about it.

    A single Jira ticket can have several incidents linked to it (dedup,
    doc 7.3) - the resolution is captured once from the earliest-ingested
    (originating) incident rather than once per linked duplicate, which
    would otherwise index the same resolution content into the KB
    repeatedly. Every linked incident still SUBMITTED (escalated, open,
    waiting on this exact ticket) closes now that it's genuinely done -
    incidents already CLOSED (e.g. self-resolved before ever sharing this
    ticket) are left alone.
    """
    result = await session.execute(
        select(Incident).where(Incident.jira_key == issue_key).order_by(Incident.ingested_at.asc())
    )
    incidents = list(result.scalars().all())
    if not incidents:
        raise HTTPException(404, "no incident linked to this Jira issue")
    incident = incidents[0]

    # Jira webhooks retry on timeout/non-2xx, so the same closure can be
    # delivered more than once - guard against re-indexing the same
    # resolution content each time. Pattern detection (below) still runs
    # regardless, since whether a pattern is "recurring" depends on global
    # state (how many incidents have closed with this signature by now),
    # not on whether this particular capture already happened.
    already_captured = await session.execute(
        select(AuditRecord).where(
            AuditRecord.event_type == "FEEDBACK_RESOLUTION_CAPTURED",
            AuditRecord.incident_id == incident.incident_id,
        )
    )
    capture_result: dict = {"captured": False, "reason": "already_captured"}
    if already_captured.scalars().first() is None:
        vector_store = get_vector_store(session)
        settings = get_settings()
        capture_result = await capture_resolution(
            session,
            incident,
            vector_store=vector_store,
            llm_client=get_llm_client(),
            taxonomy=get_taxonomy(),
            embedding_model=settings.embedding_model,
        )

    thresholds = get_thresholds()
    feedback_cfg = thresholds.get("feedback", {})
    draft_id = None
    for candidate in incidents:
        draft = await maybe_draft_sop(
            session,
            candidate,
            min_occurrences=feedback_cfg.get("sop_min_occurrences", 3),
            window_hours=feedback_cfg.get("sop_window_hours", 720),
        )
        if draft is not None:
            draft_id = draft.draft_id
            break

    closed_incident_ids = []
    for candidate in incidents:
        if candidate.status == IncidentStatus.SUBMITTED:
            await _record_transition(session, candidate, IncidentStatus.CLOSED)
            closed_incident_ids.append(candidate.incident_id)
    await session.commit()

    return {"capture": capture_result, "sop_draft_created": draft_id, "closed_incident_ids": closed_incident_ids}


@router.post("/webhooks/jira-closure")
async def jira_closure_webhook(
    body: JiraClosureWebhook, session: AsyncSession = Depends(get_session)
) -> dict:
    """Real Jira would be configured to POST here on an L2 support engineer
    closing the issue. Not involved when GenieBot itself closes a ticket
    (end user reports the L1 fix worked) - that path captures the
    resolution and closes the incident immediately instead, see the
    "resolved" branch of review_incident above."""
    return await _handle_jira_closure(session, body.issue_key)


@router.post("/{incident_id}/simulate-jira-closure")
async def simulate_jira_closure(incident_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Local-only demo affordance: this reference implementation has no
    real Jira instance to send a real closure webhook, so this is how you
    exercise "L2 closes the ticket" - it closes the mock ticket itself
    (MockJiraServer.simulate_l2_closure, integrations/jira_client.py) and
    then runs exactly the same closure handling a real webhook would
    trigger. Not meaningful against RealJiraClient - a real ticket closes
    for real, and its own webhook is what should fire."""
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    if not incident.jira_key:
        raise HTTPException(404, "incident has no linked Jira ticket")

    jira_client = get_jira_client()
    if not isinstance(jira_client, MockJiraServer):
        raise HTTPException(
            501, "simulate-jira-closure only works against the mock Jira backend - close the real ticket instead"
        )
    jira_client.simulate_l2_closure(incident.jira_key)
    return await _handle_jira_closure(session, incident.jira_key)
