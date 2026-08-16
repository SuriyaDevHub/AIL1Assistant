"""Resolution capture - doc 2.2 step 13 ("On Jira closure, the feedback
service captures the resolution...") and doc 4.5 bullet 1 ("Closed Jira
resolutions and reviewer edits are captured as new candidate documents")
and bullet 3 ("Reviewer rejections are recorded as negative signal and used
in evaluation, not indexed as guidance").

Triggered by the Jira closure webhook (api/routers/incidents.py
`/webhooks/jira-closure`).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.audit import ledger
from geniebot.db.models import Incident, ReviewDecision
from geniebot.kb.build_pipeline import index_single_document
from geniebot.kb.vector_store import VectorStore
from geniebot.llm.client import LLMClient
from geniebot.schemas.kb import RawDocument


def build_resolved_incident_document(incident: Incident) -> RawDocument:
    template = incident.template_payload or {}
    diagnosis = incident.diagnosis or {}
    text = (
        f"Problem:\n{template.get('summary') or diagnosis.get('root_cause', '')}\n\n"
        f"Root Cause:\n{diagnosis.get('root_cause', '')}\n\n"
        f"Resolution:\n{template.get('recommended_action') or diagnosis.get('proposed_resolution', '')}"
    )
    return RawDocument(
        doc_id=incident.incident_id,
        doc_type="resolved_incident",
        source_ref=incident.jira_key or incident.incident_id,
        raw_text=text,
        created_at=datetime.now(UTC),
        error_category_hint=template.get("error_category"),
    )


async def capture_resolution(
    session: AsyncSession,
    incident: Incident,
    *,
    vector_store: VectorStore,
    llm_client: LLMClient,
    taxonomy: dict,
    embedding_model: str,
) -> dict:
    if incident.decision == ReviewDecision.REJECT:
        await ledger.record(
            session,
            incident_id=incident.incident_id,
            event_type="FEEDBACK_NEGATIVE_SIGNAL",
            payload={"reason": "reviewer_rejected", "jira_key": incident.jira_key},
        )
        await session.commit()
        return {"captured": False, "reason": "rejected"}

    index_version = await vector_store.get_active_index_version()
    if index_version is None:
        return {"captured": False, "reason": "no_active_index"}

    doc = build_resolved_incident_document(incident)
    count = await index_single_document(
        doc,
        session=session,
        vector_store=vector_store,
        llm_client=llm_client,
        taxonomy=taxonomy,
        index_version=index_version,
        embedding_model=embedding_model,
    )
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="FEEDBACK_RESOLUTION_CAPTURED",
        payload={"chunks_indexed": count, "index_version": index_version, "jira_key": incident.jira_key},
    )
    await session.commit()
    return {"captured": True, "chunks_indexed": count}
