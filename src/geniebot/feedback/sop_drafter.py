"""SOP drafting - doc 4.5 bullet 2: "Recurring patterns trigger SOP draft
generation, which requires human approval before indexing." A recurring
pattern here means the same error_signature_id (doc 7.3's normalised
exception type + failing module + bot identifier) has closed with an
approved resolution multiple times within a window - deterministically
templated from those resolutions rather than a fresh LLM call, since the
content already exists in the closed incidents and doesn't need
generation, only consolidation. Drafts are staged in db.models.SopDraft and
only indexed once a human approves them (api/routers/kb.py sop-draft
endpoints).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.audit import ledger
from geniebot.db.models import Incident, SopDraft, SopDraftStatus
from geniebot.state_machine import IncidentStatus


async def _closed_incidents_with_signature(
    session: AsyncSession, error_signature_id: str, *, window_hours: int
) -> list[Incident]:
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    result = await session.execute(
        select(Incident).where(
            Incident.error_signature_id == error_signature_id,
            Incident.status == IncidentStatus.CLOSED,
            Incident.ingested_at >= cutoff,
        )
    )
    return list(result.scalars().all())


def _draft_content(incidents: list[Incident]) -> tuple[str, str]:
    """Returns (title, content) - content uses the same section headers
    kb/chunking.py recognises (Problem/Root Cause/Resolution) so it chunks
    the same way any other SOP does once approved and indexed."""
    template = incidents[0].template_payload or {}
    diagnosis = incidents[0].diagnosis or {}
    category = template.get("error_category", "UNKNOWN")
    title = f"Draft SOP: recurring {category} failures ({len(incidents)} occurrences)"
    resolutions = "\n".join(
        f"- ({i.jira_key or i.incident_id}) {(i.template_payload or {}).get('recommended_action') or (i.diagnosis or {}).get('proposed_resolution', '')}"
        for i in incidents
    )
    content = (
        f"Problem:\n{template.get('summary') or diagnosis.get('root_cause', '')}\n\n"
        f"Root Cause:\n{diagnosis.get('root_cause', '')}\n\n"
        f"Resolution:\nObserved {len(incidents)} times with the following approved resolutions:\n{resolutions}\n\n"
        "(Auto-drafted from recurring resolved incidents - review and edit before approving.)"
    )
    return title, content


async def maybe_draft_sop(
    session: AsyncSession,
    incident: Incident,
    *,
    min_occurrences: int = 3,
    window_hours: int = 24 * 30,
) -> SopDraft | None:
    if not incident.error_signature_id:
        return None

    existing = await session.execute(
        select(SopDraft).where(
            SopDraft.error_signature_id == incident.error_signature_id,
            SopDraft.status == SopDraftStatus.PENDING,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None  # already have a pending draft for this signature

    related = await _closed_incidents_with_signature(
        session, incident.error_signature_id, window_hours=window_hours
    )
    if len(related) < min_occurrences:
        return None

    template = related[0].template_payload or {}
    title, content = _draft_content(related)
    draft = SopDraft(
        error_signature_id=incident.error_signature_id,
        error_category=template.get("error_category", "UNKNOWN"),
        title=title,
        content=content,
        source_incident_ids=[i.incident_id for i in related],
        status=SopDraftStatus.PENDING,
    )
    session.add(draft)
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="SOP_DRAFT_CREATED",
        payload={"error_signature_id": incident.error_signature_id, "occurrences": len(related)},
    )
    await session.commit()
    return draft
