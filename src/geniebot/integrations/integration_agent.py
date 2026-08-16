"""Integration Agent - doc section 5.6: "Deterministic orchestration with
no generative step at submission time. Maps the approved template to Jira
fields, attaches log references, dispatches the email, and applies
deduplication before creation." (doc 2.2 step 12).

Runs after either end-user outcome (api/routers/incidents.py
review_incident): "resolved" (L1 fix confirmed working -
resolution_confirmed=True, ticket is closed immediately) or "escalated" (L1
fix didn't work - resolution_confirmed=False, ticket is left open for L2).
Also still runs after the legacy staff "approve" decision. "reject" just
closes the incident with no integration side effects.
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


def _build_description(incident: Incident, template: dict, *, resolution_confirmed: bool) -> str:
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
    if resolution_confirmed:
        parts.append(
            "This ticket documents an L1 resolution confirmed working by the submitter - filed for "
            "audit history and knowledge-base feedback, not for L2 action."
        )
    else:
        parts.append("This ticket was pre-filled by GenieBot L1 Assistant for L2 review.")
    submitter = incident.reviewer_id or incident.user_id
    parts.append(f"Submitted by: {submitter}")
    if incident.reviewer_comment:
        parts.append(f"Submitter comment: {incident.reviewer_comment}")
    return "\n".join(parts)


def _build_log_excerpt(incident: Incident) -> str:
    parse_output = incident.parse_output or {}
    lines = [f"{e['line_no']}: {e['text']}" for e in parse_output.get("evidence_lines", [])]
    return "\n".join(lines) or "(no evidence lines captured)"


def _build_email_body(
    incident: Incident,
    template: dict,
    dedup_basis: str | None,
    *,
    resolution_confirmed: bool,
    recurrence_of: str | None = None,
) -> str:
    headline = (
        "Incident {id} was resolved by the end user applying the L1 fix and has been closed."
        if resolution_confirmed
        else "Incident {id} could not be resolved with the L1 fix and has been escalated to L2."
    ).format(id=incident.incident_id)
    lines = [
        headline,
        f"Jira: {incident.jira_key}",
        f"Bot: {incident.bot_id}  Job run: {incident.job_run_id}",
        f"Submitted by: {incident.reviewer_id or incident.user_id}",
        "",
        template.get("summary", ""),
    ]
    if incident.reviewer_comment:
        lines.append(f"\nComment: {incident.reviewer_comment}")
    if recurrence_of:
        lines.append(f"\n(This issue was previously confirmed fixed in {recurrence_of} - it has recurred.)")
    elif dedup_basis:
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
    resolution_confirmed: bool = False,
) -> None:
    """resolution_confirmed=True: the end user reported the L1-proposed fix
    actually worked, so there's no L2 action to wait for - the ticket exists
    purely as an audit record and is closed immediately (caller is then
    responsible for capturing the resolution into the KB feedback loop,
    which needs an LLM client this deterministic module deliberately doesn't
    take - doc 5.6 "no generative step at submission time"). Default False
    is the escalation path: ticket is created/updated and left open for L2."""
    dedup_result = await check_duplicate(
        session, incident, window_hours=dedup_window_hours, fuzzy_threshold=dedup_fuzzy_threshold
    )

    # A precedent someone already marked fixed isn't "ongoing work" to pile
    # onto - if it's recurring anyway (this report is an escalation, not
    # another self-resolution), that's a signal the earlier fix didn't
    # hold, and deserves L2's fresh attention rather than a comment on a
    # ticket nobody's watching anymore. Still tagged back to that ticket
    # for history, just not reused as the destination.
    recurrence_of = (
        dedup_result.matched_jira_key
        if dedup_result.is_duplicate and dedup_result.matched_was_resolved and not resolution_confirmed
        else None
    )
    links_to_matched_ticket = dedup_result.is_duplicate and dedup_result.matched_jira_key and not recurrence_of

    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="DEDUP_DECISION",
        payload={
            "is_duplicate": dedup_result.is_duplicate,
            "basis": dedup_result.basis,
            "matched_incident_id": dedup_result.matched_incident_id,
            "matched_jira_key": dedup_result.matched_jira_key,
            "matched_was_resolved": dedup_result.matched_was_resolved,
            "treated_as_recurrence_new_ticket": recurrence_of is not None,
        },
    )

    template = incident.template_payload or {}

    if links_to_matched_ticket:
        incident.jira_key = dedup_result.matched_jira_key
        comment = (
            f"Linked duplicate incident {incident.incident_id} "
            f"(bot_id={incident.bot_id}, job_run_id={incident.job_run_id}). "
            f"Dedup basis: {dedup_result.basis}."
        )
        if resolution_confirmed:
            # The shared ticket may still be relevant to other linked
            # incidents - note that this instance self-resolved rather than
            # closing a ticket other job runs may still need L2 on.
            comment += " This instance was resolved by the end user applying the L1 fix."
        await jira_client.add_comment(dedup_result.matched_jira_key, comment)
    else:
        settings = get_settings()
        summary = template.get("summary") or (incident.diagnosis or {}).get("root_cause") or (
            f"Genie Bot failure: {incident.bot_id}/{incident.job_run_id}"
        )
        description = _build_description(incident, template, resolution_confirmed=resolution_confirmed)
        labels = [l for l in [incident.error_signature_id, template.get("error_category")] if l]
        if recurrence_of:
            description += (
                f"\n\nThis exact issue was previously confirmed fixed in {recurrence_of}, but has "
                f"recurred - the earlier fix may not have held, or this is a regression. See that "
                f"ticket for the prior root cause and resolution."
            )
            labels.append(f"recurrence-of-{recurrence_of}")
        issue_key = await jira_client.create_issue(
            project_key=settings.jira_project_key,
            summary=summary[:250],
            description=description,
            labels=labels,
            attachments=[("execution.log", _build_log_excerpt(incident).encode("utf-8"))],
        )
        incident.jira_key = issue_key
        # Only close a ticket GenieBot just created for this incident - a
        # matched duplicate ticket may still be relevant to other linked
        # incidents, so it's left for L2 to close (handled above instead).
        if resolution_confirmed:
            await jira_client.close_issue(issue_key, status="Done")

    await mail_client.send(
        to=support_mailbox,
        subject=f"[Genie Bot] {incident.jira_key}: {incident.bot_id} / {incident.job_run_id}",
        body=_build_email_body(
            incident, template, dedup_result.basis,
            resolution_confirmed=resolution_confirmed, recurrence_of=recurrence_of,
        ),
    )

    # resolution_confirmed=True: GenieBot just closed the ticket itself
    # (above), so there's nothing left to wait on - close the incident now.
    # False (escalated): the ticket is genuinely open for L2, so the
    # incident stays SUBMITTED until it really closes - see
    # api/routers/incidents.py's jira_closure_webhook / the demo
    # simulate-jira-closure endpoint, which transition it then.
    if resolution_confirmed:
        incident.status = transition(incident.status, IncidentStatus.CLOSED)
        await ledger.record(
            session,
            incident_id=incident.incident_id,
            event_type="STATE_TRANSITION",
            payload={"target_status": incident.status.value},
        )
    await session.commit()
