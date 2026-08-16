"""initial schema - incidents, kb_chunks, audit_records, index_versions

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    incident_status = sa.Enum(
        "INGESTED",
        "SCREENED",
        "PARSED",
        "DIAGNOSED",
        "AUTO_RESOLVE_CANDIDATE",
        "ESCALATION_DRAFTED",
        "AWAITING_REVIEW",
        "SUBMITTED",
        "REJECTED",
        "RERUN_APPROVED",
        "CLOSED",
        "BLOCKED_BY_GUARDRAIL",
        "UNPARSEABLE",
        "PLATFORM_UNAVAILABLE",
        "MANUAL_FALLBACK",
        name="incidentstatus",
    )
    environment = sa.Enum("PRODUCTION", "UAT", "DEVELOPMENT", name="environment")
    review_decision = sa.Enum("APPROVE", "REJECT", "APPROVE_RERUN", name="reviewdecision")
    doc_type = sa.Enum("RESOLVED_INCIDENT", "SOP", "TEMPLATE", "RUNBOOK", name="doctype")
    classification = sa.Enum(
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", name="classification"
    )

    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(36), primary_key=True),
        sa.Column("bot_id", sa.String(255), nullable=False),
        sa.Column("job_run_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("environment", environment, nullable=False),
        sa.Column("log_s3_uri", sa.String(2048), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", incident_status, nullable=False),
        sa.Column("error_signature_id", sa.String(64), nullable=True),
        sa.Column("parse_output", sa.JSON(), nullable=True),
        sa.Column("diagnosis", sa.JSON(), nullable=True),
        sa.Column("template_payload", sa.JSON(), nullable=True),
        sa.Column("reviewer_id", sa.String(255), nullable=True),
        sa.Column("decision", review_decision, nullable=True),
        sa.Column("reviewer_comment", sa.Text(), nullable=True),
        sa.Column("jira_key", sa.String(64), nullable=True),
        sa.Column("token_cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("rerun_count", sa.Integer(), nullable=False),
        sa.Column("active_prompt_versions", sa.JSON(), nullable=True),
        sa.Column("active_index_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incidents_bot_id", "incidents", ["bot_id"])
    op.create_index("ix_incidents_job_run_id", "incidents", ["job_run_id"])
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_error_signature_id", "incidents", ["error_signature_id"])
    op.create_index("ix_incidents_jira_key", "incidents", ["jira_key"])

    op.create_table(
        "kb_chunks",
        sa.Column("chunk_id", sa.String(36), primary_key=True),
        sa.Column("doc_id", sa.String(36), nullable=False),
        sa.Column("doc_type", doc_type, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("parent_summary", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("classification", classification, nullable=False),
        sa.Column("index_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kb_chunks_doc_id", "kb_chunks", ["doc_id"])
    op.create_index("ix_kb_chunks_error_category", "kb_chunks", ["error_category"])
    op.create_index("ix_kb_chunks_index_version", "kb_chunks", ["index_version"])

    op.create_table(
        "audit_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "incident_id",
            sa.String(36),
            sa.ForeignKey("incidents.incident_id"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_records_incident_id", "audit_records", ["incident_id"])
    op.create_index("ix_audit_records_event_type", "audit_records", ["event_type"])
    op.create_index("ix_audit_records_created_at", "audit_records", ["created_at"])

    op.create_table(
        "index_versions",
        sa.Column("index_version", sa.String(64), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("recall", sa.Float(), nullable=True),
        sa.Column("precision", sa.Float(), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_index_versions_active", "index_versions", ["active"])

    sop_draft_status = sa.Enum("PENDING", "APPROVED", "REJECTED", name="sopdraftstatus")
    op.create_table(
        "sop_drafts",
        sa.Column("draft_id", sa.String(36), primary_key=True),
        sa.Column("error_signature_id", sa.String(64), nullable=False),
        sa.Column("error_category", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_incident_ids", sa.JSON(), nullable=False),
        sa.Column("status", sop_draft_status, nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sop_drafts_error_signature_id", "sop_drafts", ["error_signature_id"])

    op.create_table(
        "system_flags",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_flags")
    op.drop_table("sop_drafts")
    op.drop_table("index_versions")
    op.drop_table("audit_records")
    op.drop_table("kb_chunks")
    op.drop_table("incidents")
    for enum_name in (
        "incidentstatus",
        "environment",
        "reviewdecision",
        "doctype",
        "classification",
        "sopdraftstatus",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
