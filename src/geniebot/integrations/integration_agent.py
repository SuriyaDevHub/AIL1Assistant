"""Integration Agent - doc section 5.6: "Deterministic orchestration with
no generative step at submission time. Maps the approved template to Jira
fields, attaches log references, dispatches the email, and applies
deduplication before creation." Runs after a reviewer decision of
"approve" (doc 2.2 step 12); reject just closes the incident with no
integration side effects.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.audit import ledger
from geniebot.db.models import Incident
from geniebot.integrations.dedup import check_duplicate
from geniebot.integrations.jira_client import JiraClient
from geniebot.integrations.mail_client import MailClient
from geniebot.settings import get_settings
from geniebot.state_machine import IncidentStatus, transition


def _build_description(incident: Incident, template: dict) -> str:
    diagnosis = incident.diagnosis or {}
    parts = [
        f"Incident: {incident.incident_id}",
        f"Bot: {incident.bot_id}  Job run: {incident.job_run_id}  Environment: {incident.environment.value}",
        f"Log: {incident.log_s3_uri}",
        "",
        f"Root cause: {template.get('root_cause') or diagnosis.get('root_cause', '')}",
        f"Recommended action: {template.get('recommended_action') or diagnosis.get('proposed_resolution', '')}",
    ]
    if template.get("unknown_fields"):
        parts.append(f"Fields the assistant could not populate from evidence: {', '.join(template['unknown_fields'])}")
    parts.append("")
    parts.append("This ticket was pre-filled by GenieBot L1 Assistant and approved by a human reviewer.")
    return "\n".join(parts)


def _build_log_excerpt(incident: Incident) -> str:
    parse_output = incident.parse_output or {}
    lines = [f"{e['line_no']}: {e['text']}" for e in parse_output.get("evidence_lines", [])]
    return "\n".join(lines) or "(no evidence lines captured)"


def _build_email_body(incident: Incident, template: dict, dedup_basis: str | None) -> str:
    lines = [
        f"Incident {incident.incident_id} has been approved and submitted.",
        f"Jira: {incident.jira_key}",
        f"Bot: {incident.bot_id}  Job run: {incident.job_run_id}",
        "",
        template.get("summary", ""),
    ]
    if dedup_basis:
        lines.append(f"\n(Linked to an existing ticket - dedup basis: {dedup_basis})")
    return "\n".join(lines)


async def submit_incident(
    session: AsyncSession,
    incident: Incident,
    *,
    jira_client: JiraClient,
    mail_client: MailClient,
    dedup_window_hours: int,
    dedup_fuzzy_threshold: float,
    support_mailbox: str,
) -> None:
    dedup_result = await check_duplicate(
        session, incident, window_hours=dedup_window_hours, fuzzy_threshold=dedup_fuzzy_threshold
    )
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="DEDUP_DECISION",
        payload={
            "is_duplicate": dedup_result.is_duplicate,
            "basis": dedup_result.basis,
            "matched_incident_id": dedup_result.matched_incident_id,
            "matched_jira_key": dedup_result.matched_jira_key,
        },
    )

    template = incident.template_payload or {}

    if dedup_result.is_duplicate and dedup_result.matched_jira_key:
        incident.jira_key = dedup_result.matched_jira_key
        await jira_client.add_comment(
            dedup_result.matched_jira_key,
            f"Linked duplicate incident {incident.incident_id} "
            f"(bot_id={incident.bot_id}, job_run_id={incident.job_run_id}). "
            f"Dedup basis: {dedup_result.basis}.",
        )
    else:
        settings = get_settings()
        summary = template.get("summary") or (incident.diagnosis or {}).get("root_cause") or (
            f"Genie Bot failure: {incident.bot_id}/{incident.job_run_id}"
        )
        issue_key = await jira_client.create_issue(
            project_key=settings.jira_project_key,
            summary=summary[:250],
            description=_build_description(incident, template),
            labels=[l for l in [incident.error_signature_id, template.get("error_category")] if l],
            attachments=[("execution.log", _build_log_excerpt(incident).encode("utf-8"))],
        )
        incident.jira_key = issue_key

    await mail_client.send(
        to=support_mailbox,
        subject=f"[Genie Bot] {incident.jira_key}: {incident.bot_id} / {incident.job_run_id}",
        body=_build_email_body(incident, template, dedup_result.basis),
    )

    incident.status = transition(incident.status, IncidentStatus.CLOSED)
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="STATE_TRANSITION",
        payload={"target_status": incident.status.value},
    )
    await session.commit()
