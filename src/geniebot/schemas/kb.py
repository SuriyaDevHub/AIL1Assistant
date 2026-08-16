"""Knowledge base contracts - doc sections 3.3 and 4.4."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RawDocument(BaseModel):
    """Common document envelope produced by every KB source extractor -
    doc 4.2 step 2 "Normalise": doc_type, source_ref, timestamps and raw
    text, regardless of origin (Jira, mailbox, SOPs, error catalogue,
    execution log samples, or a fresh resolved-incident/reviewer-edit from
    the feedback loop, doc 4.5)."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    doc_type: Literal["resolved_incident", "sop", "template", "runbook"]
    source_ref: str
    raw_text: str
    created_at: datetime
    error_category_hint: str | None = None


class KBChunkCandidate(BaseModel):
    """A chunk produced by the build pipeline, prior to embedding/indexing."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    doc_type: Literal["resolved_incident", "sop", "template", "runbook"]
    content: str
    error_category: str
    source_ref: str
    parent_summary: str | None = None
    classification: Literal["public", "internal", "confidential", "restricted"]


class RetrievedChunk(BaseModel):
    """Return contract for retrieval (doc 4.4): chunk text with source_ref
    and timestamp so the Diagnostic Agent can cite it."""

    chunk_id: str
    content: str
    source_ref: str
    error_category: str
    similarity: float
    effective_from: datetime
    parent_summary: str | None = None


class RetrievalQuery(BaseModel):
    error_signature: str
    failing_module: str
    error_category: str | None = None
    environment: str | None = None
    top_n: int | None = None


class RetrievalResult(BaseModel):
    chunks: list[RetrievedChunk]
    no_precedent: bool
    """True when no qualifying match was found - doc 4.4 'empty result
    handling': an explicit no-precedent signal that lowers confidence,
    rather than silently returning an empty list the caller might miss."""
