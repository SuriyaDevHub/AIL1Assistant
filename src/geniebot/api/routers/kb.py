"""doc 7.1 KB endpoints: search (diagnostic retrieval), documents (submit
approved content for indexing), index/promote."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.api.deps import get_current_user, get_session
from geniebot.audit import ledger
from geniebot.db.models import SopDraft, SopDraftStatus
from geniebot.feedback.reindex import mark_superseded
from geniebot.kb.build_pipeline import index_single_document
from geniebot.kb.factory import get_vector_store
from geniebot.kb.retrieval import retrieve
from geniebot.llm.factory import get_llm_client
from geniebot.schemas.kb import RawDocument, RetrievalQuery, RetrievalResult
from geniebot.settings import get_guardrail_config, get_settings, get_taxonomy, get_thresholds

router = APIRouter()


@router.post("/search", response_model=RetrievalResult)
async def kb_search(query: RetrievalQuery, session: AsyncSession = Depends(get_session)) -> RetrievalResult:
    vector_store = get_vector_store(session)
    llm = get_llm_client()
    settings = get_settings()
    thresholds = get_thresholds()

    index_version = await vector_store.get_active_index_version()
    if index_version is None:
        return RetrievalResult(chunks=[], no_precedent=True)

    return await retrieve(
        session,
        vector_store,
        llm,
        error_signature=query.error_signature,
        failing_module=query.failing_module,
        error_category=query.error_category,
        index_version=index_version,
        top_n=query.top_n or thresholds["retrieval"]["top_n"],
        min_similarity=thresholds["retrieval"]["min_similarity"],
        rerank_margin=thresholds["retrieval"]["rerank_margin"],
        embedding_model=settings.embedding_model,
    )


class DocumentSubmission(BaseModel):
    doc_id: str
    doc_type: Literal["resolved_incident", "sop", "template", "runbook"]
    source_ref: str
    raw_text: str
    error_category_hint: str | None = None


@router.post("/documents")
async def submit_document(
    body: DocumentSubmission,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Doc 4.5 continuous maintenance: indexes one already-approved document
    into the currently active index_version (append, not a full rebuild -
    scripts/evaluate_retrieval.py / a fresh run_full_build is how a new
    index_version gets created and evaluated before promotion)."""
    vector_store = get_vector_store(session)
    index_version = await vector_store.get_active_index_version()
    if index_version is None:
        raise HTTPException(409, "no active index_version - run the initial KB build first")

    doc = RawDocument(
        doc_id=body.doc_id,
        doc_type=body.doc_type,
        source_ref=body.source_ref,
        raw_text=body.raw_text,
        created_at=datetime.now(UTC),
        error_category_hint=body.error_category_hint,
    )
    guardrail_cfg = get_guardrail_config()["input_guardrails"]["data_classification"]
    try:
        count = await index_single_document(
            doc,
            session=session,
            vector_store=vector_store,
            llm_client=get_llm_client(),
            taxonomy=get_taxonomy(),
            index_version=index_version,
            embedding_model=get_settings().embedding_model,
            block_above=guardrail_cfg["block_above"],
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    await ledger.record(
        session,
        incident_id=None,
        event_type="KB_DOCUMENT_INDEXED",
        payload={"doc_id": body.doc_id, "source_ref": body.source_ref, "chunks_indexed": count, "index_version": index_version},
    )
    await session.commit()
    return {"chunks_indexed": count, "index_version": index_version}


class PromoteRequest(BaseModel):
    index_version: str


@router.post("/index/promote")
async def promote_index(
    body: PromoteRequest,
    actor: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    vector_store = get_vector_store(session)
    await vector_store.set_active_index_version(body.index_version)
    await ledger.record(
        session,
        incident_id=None,
        event_type="INDEX_PROMOTED",
        payload={"index_version": body.index_version, "actor": actor},
    )
    await session.commit()
    return {"active_index_version": body.index_version}


class SopDraftSummary(BaseModel):
    draft_id: str
    error_signature_id: str
    error_category: str
    title: str
    content: str
    source_incident_ids: list[str]
    status: str

    model_config = {"from_attributes": True}


@router.get("/sop-drafts", response_model=list[SopDraftSummary])
async def list_sop_drafts(
    status: str = "pending", session: AsyncSession = Depends(get_session)
) -> list[SopDraftSummary]:
    """doc 4.5 bullet 2: SOP drafts pending human approval before indexing."""
    result = await session.execute(select(SopDraft).where(SopDraft.status == SopDraftStatus(status)))
    return [SopDraftSummary.model_validate(row) for row in result.scalars().all()]


@router.post("/sop-drafts/{draft_id}/approve")
async def approve_sop_draft(
    draft_id: str,
    actor: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    draft = await session.get(SopDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "sop draft not found")
    if draft.status != SopDraftStatus.PENDING:
        raise HTTPException(409, f"draft already {draft.status.value}")

    vector_store = get_vector_store(session)
    index_version = await vector_store.get_active_index_version()
    if index_version is None:
        raise HTTPException(409, "no active index_version - run the initial KB build first")

    # A prior SOP for the same signature (if any) is now superseded.
    await mark_superseded(session, source_ref=draft.draft_id, index_version=index_version)

    doc = RawDocument(
        doc_id=draft.draft_id,
        doc_type="sop",
        source_ref=f"SOP-DRAFT-{draft.draft_id[:8]}",
        raw_text=f"{draft.title}\n\n{draft.content}",
        created_at=datetime.now(UTC),
        error_category_hint=draft.error_category,
    )
    guardrail_cfg = get_guardrail_config()["input_guardrails"]["data_classification"]
    count = await index_single_document(
        doc,
        session=session,
        vector_store=vector_store,
        llm_client=get_llm_client(),
        taxonomy=get_taxonomy(),
        index_version=index_version,
        embedding_model=get_settings().embedding_model,
        block_above=guardrail_cfg["block_above"],
    )

    draft.status = SopDraftStatus.APPROVED
    draft.reviewed_by = actor
    draft.reviewed_at = datetime.now(UTC)
    await ledger.record(
        session,
        incident_id=None,
        event_type="SOP_DRAFT_APPROVED",
        payload={"draft_id": draft.draft_id, "actor": actor, "chunks_indexed": count},
    )
    await session.commit()
    return {"draft_id": draft.draft_id, "status": "approved", "chunks_indexed": count}


@router.post("/sop-drafts/{draft_id}/reject")
async def reject_sop_draft(
    draft_id: str,
    actor: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    draft = await session.get(SopDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "sop draft not found")
    if draft.status != SopDraftStatus.PENDING:
        raise HTTPException(409, f"draft already {draft.status.value}")

    draft.status = SopDraftStatus.REJECTED
    draft.reviewed_by = actor
    draft.reviewed_at = datetime.now(UTC)
    await ledger.record(
        session, incident_id=None, event_type="SOP_DRAFT_REJECTED", payload={"draft_id": draft.draft_id, "actor": actor}
    )
    await session.commit()
    return {"draft_id": draft.draft_id, "status": "rejected"}
