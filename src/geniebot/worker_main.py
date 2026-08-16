"""Process entrypoint for the ingestion+worker loops (doc 2.2 steps 1-9).
Runs both the storage-event watcher (doc step 1-2, creates and enqueues
incidents) and the queue consumer (doc steps 3-9, runs the orchestration
pipeline) concurrently in one process - split them into separate
deployments/replicas if ingestion and processing need to scale
independently.

Usage:
    python -m geniebot.worker_main
"""
from __future__ import annotations

import asyncio
import logging

from geniebot.db.session import get_sessionmaker, init_models
from geniebot.ingestion.s3_listener import (
    LocalFilesystemEventSource,
    S3EventSource,
    StorageEventSource,
)
from geniebot.kb.factory import ensure_vector_store_schema
from geniebot.llm.factory import get_llm_client
from geniebot.observability.logging_config import configure_logging
from geniebot.orchestration.worker import run_ingestion_loop, run_worker_loop
from geniebot.queue.factory import get_queue
from geniebot.settings import get_settings

logger = logging.getLogger("geniebot.worker_main")


def build_event_source() -> StorageEventSource:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3EventSource(bucket=settings.s3_bucket, prefix=settings.s3_working_prefix)
    return LocalFilesystemEventSource(
        root=settings.local_s3_root, bucket_name=settings.s3_bucket, prefix=settings.s3_working_prefix
    )


async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.is_production:
        await init_models()

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await ensure_vector_store_schema(session)

    queue = get_queue()
    event_source = build_event_source()
    llm_client = get_llm_client()

    logger.info(
        "worker starting",
        extra={"storage_backend": settings.storage_backend, "queue_backend": settings.queue_backend},
    )

    await asyncio.gather(
        run_ingestion_loop(event_source, queue, sessionmaker),
        run_worker_loop(queue, event_source, llm_client, sessionmaker),
    )


if __name__ == "__main__":
    asyncio.run(main())
