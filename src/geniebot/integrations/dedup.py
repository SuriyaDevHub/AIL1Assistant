"""Deduplication logic - doc section 7.3.

1. Compute error_signature_id from normalised exception type, failing
   module and bot identifier (the doc's step 1 here is slightly more
   specific than the data-model description in doc 3.1, which omits the
   bot identifier - this module follows 7.3 since it's the operative dedup
   spec; error_signature_id is still stored verbatim on Incident either way).
2. Exact match: open incidents/Jira issues with the same signature within
   the configured window.
3. Fuzzy match: summary similarity above the configured threshold.
4. On match, link rather than create a duplicate.
5. Record the matching basis in the audit ledger (done by the caller,
   integrations/integration_agent.py, via the returned DedupResult).
"""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.db.models import Incident


def compute_error_signature_id(*, exception_type: str, failing_module: str, bot_id: str) -> str:
    normalised = f"{exception_type.strip().lower()}|{failing_module.strip().lower()}|{bot_id.strip().lower()}"
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class DedupResult:
    is_duplicate: bool
    matched_incident_id: str | None = None
    matched_jira_key: str | None = None
    basis: str | None = None  # "exact_match" | "fuzzy_match" | None


async def _recent_linked_incidents(
    session: AsyncSession, *, window_hours: int, exclude_incident_id: str
) -> list[Incident]:
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    result = await session.execute(
        select(Incident).where(
            Incident.ingested_at >= cutoff,
            Incident.incident_id != exclude_incident_id,
            Incident.jira_key.is_not(None),
        )
    )
    return list(result.scalars().all())


async def check_duplicate(
    session: AsyncSession,
    incident: Incident,
    *,
    window_hours: int,
    fuzzy_threshold: float,
) -> DedupResult:
    parse_output = incident.parse_output or {}
    signature_id = compute_error_signature_id(
        exception_type=parse_output.get("exception_type", ""),
        failing_module=parse_output.get("failing_module", ""),
        bot_id=incident.bot_id,
    )
    incident.error_signature_id = signature_id

    candidates = await _recent_linked_incidents(
        session, window_hours=window_hours, exclude_incident_id=incident.incident_id
    )

    for candidate in candidates:
        if candidate.error_signature_id == signature_id:
            return DedupResult(True, candidate.incident_id, candidate.jira_key, "exact_match")

    summary = (incident.template_payload or {}).get("summary", "") or (incident.diagnosis or {}).get(
        "root_cause", ""
    )
    if summary:
        for candidate in candidates:
            candidate_summary = (candidate.template_payload or {}).get("summary", "") or (
                candidate.diagnosis or {}
            ).get("root_cause", "")
            if not candidate_summary:
                continue
            ratio = difflib.SequenceMatcher(None, summary.lower(), candidate_summary.lower()).ratio()
            if ratio >= fuzzy_threshold:
                return DedupResult(True, candidate.incident_id, candidate.jira_key, "fuzzy_match")

    return DedupResult(False)
