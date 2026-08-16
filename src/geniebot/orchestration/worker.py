"""Two loops that together implement doc 2.2 steps 1-9:

- run_ingestion_loop: watches the storage backend (doc step 1-2), creates
  an incident for failure logs, and enqueues it.
- run_worker_loop: dequeues incidents and runs the orchestration pipeline
  (doc steps 3-9). On an unexpected exception (not one of the handled
  fail-closed paths, which return normally) the message is nacked for
  redelivery up to the queue's max_attempts, then moves to the DLQ - doc
  2.3 "durable message queue with retry and DLQ".
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from geniebot.audit import ledger
from geniebot.db.models import Incident
from geniebot.ingestion.incident_factory import create_incident_if_failure
from geniebot.ingestion.s3_listener import StorageEventSource
from geniebot.kb.factory import get_vector_store
from geniebot.llm.client import LLMClient
from geniebot.orchestration.pipeline import build_pipeline_context, process_incident_from_log
from geniebot.queue.base import Queue

logger = logging.getLogger("geniebot.worker")


def _key_from_uri(log_s3_uri: str) -> str:
    # "s3://bucket/key/with/slashes" -> "key/with/slashes"
    return log_s3_uri.split("/", 3)[3]


async def run_ingestion_loop(
    event_source: StorageEventSource,
    queue: Queue,
    session_factory: async_sessionmaker,
    *,
    poll_interval: float = 5.0,
) -> None:
    async for event in event_source.watch(poll_interval=poll_interval):
        try:
            raw_text = await event_source.read_object(event.key)
            async with session_factory() as session:
                incident = await create_incident_if_failure(session, event, raw_text)
                if incident is not None:
                    await queue.enqueue({"incident_id": incident.incident_id})
                    await ledger.record(
                        session,
                        incident_id=incident.incident_id,
                        event_type="INGESTED",
                        payload={"bucket": event.bucket, "key": event.key, "size": event.size},
                    )
                    await session.commit()
        except Exception:
            logger.exception("ingestion failed for key=%s", event.key)


async def run_worker_loop(
    queue: Queue,
    event_source: StorageEventSource,
    llm_client: LLMClient,
    session_factory: async_sessionmaker,
    *,
    idle_wait_seconds: float = 5.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    while stop_event is None or not stop_event.is_set():
        message = await queue.dequeue(wait_seconds=idle_wait_seconds)
        if message is None:
            continue

        incident_id = message.body["incident_id"]
        try:
            async with session_factory() as session:
                incident = await session.get(Incident, incident_id)
                if incident is None:
                    logger.warning("dropping message for unknown incident %s", incident_id)
                    await queue.ack(message.receipt_handle)
                    continue
                raw_text = await event_source.read_object(_key_from_uri(incident.log_s3_uri))
                # Built per-message from *this* session - required for the
                # pgvector backend, whose VectorStore wraps one specific
                # AsyncSession and must not outlive it (doc note in
                # kb/factory.py). Reusing a single PipelineContext across
                # messages would leave later incidents holding a stale,
                # already-closed session.
                ctx = build_pipeline_context(llm_client, get_vector_store(session))
                await process_incident_from_log(session, incident_id, raw_text, ctx)
            await queue.ack(message.receipt_handle)
        except Exception:
            logger.exception("processing failed for incident %s (attempt %s)", incident_id, message.attempt)
            await queue.nack(message.receipt_handle, requeue=True)
