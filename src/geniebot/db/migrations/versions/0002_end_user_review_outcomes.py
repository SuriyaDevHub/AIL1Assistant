"""add resolved/escalated review outcomes

Redesigned human-in-the-loop flow: the end user who hit the error tries the
L1-proposed fix themselves and reports back "resolved" (L1 fix worked - a
Jira ticket is created purely for audit/KB-feedback purposes and immediately
closed) or "escalated" (L1 fix didn't work - Template Generator runs, if it
hasn't already, and a Jira ticket is created/updated for L2, left open).
APPROVE/REJECT/APPROVE_RERUN are kept for the existing staff-reviewer
capability (doc step 11's controlled-rerun loop); nothing is removed.

Revision ID: 0002_end_user_review_outcomes
Revises: 0001_initial
Create Date: 2026-08-16
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_end_user_review_outcomes"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres enums require explicit ALTER TYPE ... ADD VALUE (no-op on
    # SQLite, which stores the Enum as a plain VARCHAR with a CHECK
    # constraint SQLAlchemy doesn't enforce on ad hoc values, but this
    # migration only ever runs against the real target - production/UAT use
    # pgvector/Postgres per README's cutover checklist).
    op.execute("ALTER TYPE reviewdecision ADD VALUE IF NOT EXISTS 'RESOLVED'")
    op.execute("ALTER TYPE reviewdecision ADD VALUE IF NOT EXISTS 'ESCALATED'")


def downgrade() -> None:
    # Postgres does not support removing enum values in place; downgrading
    # this would require rebuilding the type and is intentionally left as a
    # manual operation if ever needed.
    raise NotImplementedError("reviewdecision enum values cannot be dropped in place")
