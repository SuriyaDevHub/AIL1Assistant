#!/usr/bin/env python
"""Dev-only convenience: seeds the KB, serves the API, and runs the worker
loops all in a single process, so the VECTOR_STORE_BACKEND=memory singleton
(kb/factory.py's _shared_memory_store, process-wide via lru_cache) is
actually shared between everything that touches it.

Running `seed_kb_sample_corpus.py`, `uvicorn geniebot.main:app`, and
`worker_main.py` as separate processes (as the README's no-Docker Python
quickstart describes) leaves each of them with its own empty in-memory
index: the worker's retrieval never sees what the seed script indexed, and
a reviewer decision handled by the API process (which calls
feedback/resolution_capture.py to index a confirmed resolution) never sees
what the worker seeded either. pgvector doesn't have this problem since
Postgres is genuinely shared state across processes - this script exists
purely to make the zero-services memory-backend path behave the same way
for local demos.

Usage:
    python scripts/seed_and_run_worker.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn

from geniebot.db.session import get_sessionmaker, init_models
from geniebot.ingestion.factory import get_event_source
from geniebot.kb.build_pipeline import run_full_build
from geniebot.kb.factory import ensure_vector_store_schema, get_vector_store
from geniebot.kb.sources.error_catalogue import ErrorCatalogueExtractor
from geniebot.kb.sources.jira_extractor import JiraTicketExtractor
from geniebot.kb.sources.log_sample_extractor import LogSampleExtractor
from geniebot.kb.sources.mailbox_extractor import MailboxArchiveExtractor
from geniebot.kb.sources.sop_extractor import SOPExtractor
from geniebot.llm.factory import get_llm_client
from geniebot.main import app
from geniebot.observability.logging_config import configure_logging
from geniebot.orchestration.worker import run_ingestion_loop, run_worker_loop
from geniebot.queue.factory import get_queue
from geniebot.settings import get_settings, get_taxonomy

logger = logging.getLogger("geniebot.seed_and_run_worker")


async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.is_production:
        await init_models()

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await ensure_vector_store_schema(session)

    async with sessionmaker() as session:
        vector_store = get_vector_store(session)
        report = await run_full_build(
            extractors=[
                JiraTicketExtractor(),
                MailboxArchiveExtractor(),
                SOPExtractor(),
                ErrorCatalogueExtractor(),
                LogSampleExtractor(),
            ],
            session=session,
            vector_store=vector_store,
            llm_client=get_llm_client(),
            taxonomy=get_taxonomy(),
            index_version="v1-seed",
            embedding_model=settings.embedding_model,
        )
    logger.info(
        "kb seeded in-process",
        extra={"chunks_indexed": report.chunks_indexed, "promoted": report.promoted},
    )

    queue = get_queue()
    event_source = get_event_source()
    llm_client = get_llm_client()

    logger.info(
        "worker starting",
        extra={"storage_backend": settings.storage_backend, "queue_backend": settings.queue_backend},
    )

    host = os.environ.get("GENIEBOT_API_HOST", "127.0.0.1")
    port = int(os.environ.get("GENIEBOT_API_PORT", "8000"))
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
    logger.info("api server starting", extra={"host": host, "port": port})

    await asyncio.gather(
        server.serve(),
        run_ingestion_loop(event_source, queue, sessionmaker),
        run_worker_loop(queue, event_source, llm_client, sessionmaker),
    )


if __name__ == "__main__":
    asyncio.run(main())
