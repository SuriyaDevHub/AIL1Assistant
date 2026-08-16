#!/usr/bin/env python
"""Builds the initial knowledge base corpus (doc section 4.2) from the
sample source extractors (kb/sources/*.py) and promotes it if it's the
first index. Run once against a fresh environment before incidents start
flowing, or any time you want a demo/dev database populated.

Usage:
    python scripts/seed_kb_sample_corpus.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geniebot.db.session import get_sessionmaker, init_models
from geniebot.kb.build_pipeline import run_full_build
from geniebot.kb.factory import get_vector_store
from geniebot.kb.sources.error_catalogue import ErrorCatalogueExtractor
from geniebot.kb.sources.jira_extractor import JiraTicketExtractor
from geniebot.kb.sources.log_sample_extractor import LogSampleExtractor
from geniebot.kb.sources.mailbox_extractor import MailboxArchiveExtractor
from geniebot.kb.sources.sop_extractor import SOPExtractor
from geniebot.llm.factory import get_llm_client
from geniebot.settings import get_settings, get_taxonomy


async def main() -> None:
    await init_models()
    settings = get_settings()
    sessionmaker = get_sessionmaker()

    extractors = [
        JiraTicketExtractor(),
        MailboxArchiveExtractor(),
        SOPExtractor(),
        ErrorCatalogueExtractor(),
        LogSampleExtractor(),
    ]

    async with sessionmaker() as session:
        vector_store = get_vector_store(session)
        report = await run_full_build(
            extractors=extractors,
            session=session,
            vector_store=vector_store,
            llm_client=get_llm_client(),
            taxonomy=get_taxonomy(),
            index_version="v1-seed",
            embedding_model=settings.embedding_model,
        )

    print(f"extracted:      {report.extracted}")
    print(f"excluded:       {len(report.excluded)}")
    print(f"deduped:        {len(report.deduped)}")
    print(f"chunks indexed: {report.chunks_indexed}")
    print(f"promoted:       {report.promoted}")


if __name__ == "__main__":
    asyncio.run(main())
