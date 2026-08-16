"""RPA failure-trigger ingestion (doc 2.2 step 1-2's push-triggered variant):
an RPA platform (e.g. a C# .NET RPA robot) that already knows a job of its
own just failed notifies GenieBot directly, instead of GenieBot discovering
the object by polling the storage backend. This is the "GenieAI agent
should be launched" half of that flow - the "logs pulled from S3" half
already exists (StorageEventSource.read_object, ingestion/s3_listener.py)
and needs no change; this endpoint just calls it on request instead of on a
poll tick.

Placeholder contract: the real RPA platform's trigger payload and auth
scheme aren't known yet. This mirrors an S3 event notification's own shape
(bucket/key/size) - the most natural fit if the real trigger does end up
being an S3 event forwarded as a webhook - and is trivially swappable once
the real contract is available. No auth is enforced here yet, matching this
reference implementation's other webhook (incidents.py's
/webhooks/jira-closure) - add real signature/token verification before a
production cutover.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.api.deps import get_session
from geniebot.ingestion.factory import get_event_source
from geniebot.ingestion.incident_factory import ingest_storage_event
from geniebot.ingestion.s3_listener import StorageEvent
from geniebot.queue.factory import get_queue

router = APIRouter()


class RpaFailureTrigger(BaseModel):
    bucket: str
    key: str
    size: int = 0


@router.post("/trigger")
async def rpa_failure_trigger(
    body: RpaFailureTrigger, session: AsyncSession = Depends(get_session)
) -> dict:
    event = StorageEvent(bucket=body.bucket, key=body.key, size=body.size, event_time=datetime.now(UTC))
    incident_id = await ingest_storage_event(
        event, event_source=get_event_source(), queue=get_queue(), session=session
    )
    if incident_id is None:
        return {"ingested": False, "reason": "not recognised as a failure log"}
    return {"ingested": True, "incident_id": incident_id}
