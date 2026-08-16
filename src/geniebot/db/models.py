"""SQLAlchemy models.

Incident -> doc section 3.1. KBChunk -> doc section 3.3. AuditRecord is the
append-only ledger referenced throughout the doc (5.1 agent invocations,
6.3 guardrail evaluations, 7.3 dedup decisions, human review decisions,
config changes) - one generic, queryable, immutable table rather than a
table per event type, since every event shares the same audit shape:
who/what, when, and a typed payload.

Note on embeddings: KBChunk.embedding is stored as JSON (portable across
Postgres and the SQLite test database). The pgvector-backed VectorStore
implementation (src/geniebot/kb/vector_store.py) maintains its own
`kb_embeddings(chunk_id, embedding vector(N))` table via raw DDL for actual
ANN search - kept out of the ORM so the relational schema stays
dialect-agnostic and the vector index can be rebuilt/versioned independently.
"""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from geniebot.db.base import Base
from geniebot.state_machine import IncidentStatus


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Environment(str, enum.Enum):
    PRODUCTION = "production"
    UAT = "uat"
    DEVELOPMENT = "development"


class ReviewDecision(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    APPROVE_RERUN = "approve_rerun"
    # End-user self-service outcomes (redesigned flow): the end user who hit
    # the error tries the L1-proposed fix themselves and reports back.
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class DocType(str, enum.Enum):
    RESOLVED_INCIDENT = "resolved_incident"
    SOP = "sop"
    TEMPLATE = "template"
    RUNBOOK = "runbook"


class Classification(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Incident(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bot_id: Mapped[str] = mapped_column(String(255), index=True)
    job_run_id: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str] = mapped_column(String(255))
    environment: Mapped[Environment] = mapped_column(Enum(Environment))
    log_s3_uri: Mapped[str] = mapped_column(String(2048))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus), default=IncidentStatus.INGESTED, index=True
    )

    error_signature_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    parse_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    diagnosis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    template_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    reviewer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[ReviewDecision | None] = mapped_column(Enum(ReviewDecision), nullable=True)
    reviewer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    jira_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    token_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal(0))

    rerun_count: Mapped[int] = mapped_column(default=0)
    active_prompt_versions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    active_index_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    audit_records: Mapped[list[AuditRecord]] = relationship(back_populates="incident")


class KBChunk(Base):
    __tablename__ = "kb_chunks"

    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doc_id: Mapped[str] = mapped_column(String(36), index=True)
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    error_category: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str] = mapped_column(String(255))
    parent_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    classification: Mapped[Classification] = mapped_column(Enum(Classification))
    index_version: Mapped[str] = mapped_column(String(64), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditRecord(Base):
    """Append-only audit ledger. Never updated or deleted, only inserted."""

    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("incidents.incident_id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    incident: Mapped[Incident | None] = relationship(back_populates="audit_records")


class SopDraftStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SopDraft(Base):
    """Doc 4.5: "Recurring patterns trigger SOP draft generation, which
    requires human approval before indexing." A staging row, never indexed
    directly - feedback/sop_drafter.py creates PENDING rows, an admin
    approves (-> indexed into the KB, doc 4.2 pipeline) or rejects them."""

    __tablename__ = "sop_drafts"

    draft_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    error_signature_id: Mapped[str] = mapped_column(String(64), index=True)
    error_category: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    source_incident_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[SopDraftStatus] = mapped_column(Enum(SopDraftStatus), default=SopDraftStatus.PENDING)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SystemFlag(Base):
    """Tiny key/value table backing the kill-switch (doc 7.1 /admin/killswitch,
    doc 9 Resilience row) so its state is shared across API/worker processes
    and survives restarts, rather than living only in process memory."""

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[bool] = mapped_column(default=False)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class IndexVersion(Base):
    """Tracks vector-store index promotions/rollbacks (doc 4.2 steps 11-12)."""

    __tablename__ = "index_versions"

    index_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    active: Mapped[bool] = mapped_column(default=False, index=True)
    recall: Mapped[float | None] = mapped_column(nullable=True)
    precision: Mapped[float | None] = mapped_column(nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
