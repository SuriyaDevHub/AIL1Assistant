"""Kill-switch - doc 7.1 (/admin/killswitch) and doc 10.3 runbook scenarios
("Systematic misdiagnosis" / "Guardrail failure or bypass" both start with
"Activate kill-switch"). Backed by a single-row-per-key table
(db.models.SystemFlag) so state is shared across the API and worker
processes and survives restarts.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.db.models import SystemFlag

KILLSWITCH_KEY = "automated_processing_enabled"


async def is_processing_enabled(session: AsyncSession) -> bool:
    result = await session.execute(select(SystemFlag).where(SystemFlag.key == KILLSWITCH_KEY))
    row = result.scalar_one_or_none()
    if row is None:
        return True  # default: automated processing is on
    return row.value


async def set_processing_enabled(session: AsyncSession, *, enabled: bool, actor: str) -> None:
    result = await session.execute(select(SystemFlag).where(SystemFlag.key == KILLSWITCH_KEY))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemFlag(key=KILLSWITCH_KEY, value=enabled, updated_by=actor)
        session.add(row)
    else:
        row.value = enabled
        row.updated_by = actor
        row.updated_at = datetime.now(UTC)
    await session.commit()
