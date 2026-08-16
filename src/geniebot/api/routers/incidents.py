"""doc 7.1 incident endpoints: list, detail, review decision, template edit."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.api.deps import get_current_user, get_session
from geniebot.audit import ledger
from geniebot.db.models import AuditRecord, Environment, Incident, ReviewDecision
from geniebot.feedback.resolution_capture import capture_resolution
from geniebot.feedback.sop_drafter import maybe_draft_sop
from geniebot.integrations.factory import get_jira_client, get_mail_client
from geniebot.integrations.integration_agent import submit_incident
from geniebot.kb.factory import get_vector_store
from geniebot.llm.factory import get_llm_client
from geniebot.observability.metrics import record_review_decision
from geniebot.orchestration.pipeline import build_pipeline_context, process_rerun
from geniebot.schemas.incident import (
    IncidentDetail,
    IncidentListItem,
    ReviewDecisionRequest,
    TemplateUpdateRequest,
)
from geniebot.settings import get_settings, get_taxonomy, get_thresholds
from geniebot.state_machine import IncidentStatus, transition

router = APIRouter()

_DECISION_MAP = {
    "approve": ReviewDecision.APPROVE,
    "reject": ReviewDecision.REJECT,
    "approve_rerun": ReviewDecision.APPROVE_RERUN,
}


@router.get("", response_model=list[IncidentListItem])
async def list_incidents(
    status: str | None = None,
    bot_id: str | None = None,
    environment: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[IncidentListItem]:
    query = select(Incident)
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


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(incident_id: str, session: AsyncSession = Depends(get_session)) -> IncidentDetail:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    return IncidentDetail.model_validate(incident)


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
        incident.status = transition(incident.status, IncidentStatus.SUBMITTED)
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
        )

    elif body.decision == "reject":
        incident.status = transition(incident.status, IncidentStatus.REJECTED)
        incident.status = transition(incident.status, IncidentStatus.CLOSED)
        await ledger.record(
            session,
            incident_id=incident.incident_id,
            event_type="STATE_TRANSITION",
            payload={"target_status": incident.status.value},
        )
        await session.commit()

    elif body.decision == "approve_rerun":
        if body.rerun_overrides and incident.diagnosis:
            incident.diagnosis = {**incident.diagnosis, "rerun_parameters": body.rerun_overrides}
        incident.status = transition(incident.status, IncidentStatus.RERUN_APPROVED)
        await session.commit()

        ctx = build_pipeline_context(get_llm_client(), get_vector_store(session))
        await process_rerun(session, incident.incident_id, ctx)

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


@router.post("/webhooks/jira-closure")
async def jira_closure_webhook(
    body: JiraClosureWebhook, session: AsyncSession = Depends(get_session)
) -> dict:
    """doc 7.2 "closure webhook for feedback" / doc 2.2 step 13. Real Jira
    would be configured to POST here on issue transition to Closed;
    MockJiraServer.close_issue() (integrations/jira_client.py) is a
    test/demo stand-in for that transition, so this endpoint has to be
    called explicitly rather than firing automatically in local/dev runs.

    A single Jira ticket can have several incidents linked to it (dedup,
    doc 7.3) - the resolution is captured once from the earliest-ingested
    (originating) incident rather than once per linked duplicate, which
    would otherwise index the same resolution content into the KB
    repeatedly.
    """
    result = await session.execute(
        select(Incident).where(Incident.jira_key == body.issue_key).order_by(Incident.ingested_at.asc())
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

    return {"capture": capture_result, "sop_draft_created": draft_id}
