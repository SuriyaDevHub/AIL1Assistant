"""Incident creation from a raw storage event - doc 2.2 step 2: "failure
logs create an incident record and enqueue it" (success logs are not
turned into incidents at all).

Key convention: `{prefix}{bot_id}/{job_run_id}/execution.log`. The log body
may start with a small header block (`# user_id: ...` / `# environment:
...`) - doc 3.1 "bot_id / job_run_id: Genie Bot identifiers from the log
path and header". bot_id/job_run_id fall back to the path segments if the
header is absent; user_id/environment default to "unknown" / "production"
if genuinely not present anywhere - this convention is a reference
implementation, not something the doc specifies precisely, so adjust it to
match the real Genie Bot log-writing convention before production cutover.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.audit import ledger
from geniebot.db.models import Environment, Incident
from geniebot.ingestion.s3_listener import StorageEvent, StorageEventSource
from geniebot.queue.base import Queue
from geniebot.settings import get_settings
from geniebot.state_machine import IncidentStatus

_FAILURE_MARKERS = re.compile(r"(?i)(error|exception|traceback|fatal|\bfailed\b)")
_HEADER_LINE = re.compile(r"^#\s*(\w+):\s*(.+)$", re.MULTILINE)


def is_failure_log(raw_text: str) -> bool:
    return bool(_FAILURE_MARKERS.search(raw_text))


def _parse_identifiers(event: StorageEvent, raw_text: str, *, working_prefix: str) -> dict[str, str]:
    header = dict(_HEADER_LINE.findall(raw_text))
    key = event.key
    if working_prefix and key.startswith(working_prefix):
        key = key[len(working_prefix) :]
    parts = key.split("/")
    bot_id = header.get("bot_id") or (parts[0] if len(parts) > 0 else "unknown-bot")
    job_run_id = header.get("job_run_id") or (parts[1] if len(parts) > 1 else "unknown-run")
    user_id = header.get("user_id", "unknown")
    environment = header.get("environment", "production").lower()
    if environment not in (e.value for e in Environment):
        environment = "production"
    return {"bot_id": bot_id, "job_run_id": job_run_id, "user_id": user_id, "environment": environment}


async def create_incident_if_failure(
    session: AsyncSession, event: StorageEvent, raw_text: str, *, working_prefix: str | None = None
) -> Incident | None:
    if not is_failure_log(raw_text):
        return None

    if working_prefix is None:
        working_prefix = get_settings().s3_working_prefix
    ids = _parse_identifiers(event, raw_text, working_prefix=working_prefix)
    incident = Incident(
        bot_id=ids["bot_id"],
        job_run_id=ids["job_run_id"],
        user_id=ids["user_id"],
        environment=Environment(ids["environment"]),
        log_s3_uri=f"s3://{event.bucket}/{event.key}",
        ingested_at=datetime.now(UTC),
        status=IncidentStatus.INGESTED,
    )
    session.add(incident)
    await session.commit()
    return incident


async def ingest_storage_event(
    event: StorageEvent, *, event_source: StorageEventSource, queue: Queue, session: AsyncSession
) -> str | None:
    """Pulls the object and creates+enqueues an incident if it's a failure
    log - the one place both discovery paths converge: the polling loop
    (orchestration/worker.py's run_ingestion_loop) and a push trigger (e.g.
    api/routers/ingest.py, for a system like an RPA platform that already
    knows which object just failed rather than making us discover it by
    polling). A triggered incident and a polling-discovered one are
    identical downstream - same Incident row shape, same queue message,
    same INGESTED audit record. Returns the new incident_id, or None if
    this wasn't a failure log."""
    raw_text = await event_source.read_object(event.key)
    incident = await create_incident_if_failure(session, event, raw_text)
    if incident is None:
        return None
    await queue.enqueue({"incident_id": incident.incident_id})
    await ledger.record(
        session,
        incident_id=incident.incident_id,
        event_type="INGESTED",
        payload={"bucket": event.bucket, "key": event.key, "size": event.size},
    )
    await session.commit()
    return incident.incident_id
