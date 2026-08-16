"""Append-only audit ledger (doc 1.4 "Everything auditable: prompts,
retrieved context, outputs and human decisions are recorded per incident").
One generic record() call from every layer - agent invocations (5.1),
guardrail evaluations (6.3), state transitions, dedup decisions (7.3),
human review decisions, config changes (8.2) - rather than a bespoke method
per event type, since they all share the same shape.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.db.models import AuditRecord


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


async def record(
    session: AsyncSession,
    *,
    incident_id: str | None,
    event_type: str,
    payload: dict,
) -> AuditRecord:
    row = AuditRecord(
        incident_id=incident_id,
        event_type=event_type,
        payload=json.loads(json.dumps(_json_safe(payload))),
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row
