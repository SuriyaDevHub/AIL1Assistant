#!/usr/bin/env python
"""Runs the retrieval evaluation set (doc 9 "Retrieval evaluation" row)
against the currently active KB index and prints recall/precision - doc
9.1's retrieval acceptance metric and the doc 4.2 step 11 gate used before
promoting a rebuilt index. Also usable standalone to sanity-check a fresh
seed (scripts/seed_kb_sample_corpus.py must have run first).

Usage:
    python scripts/evaluate_retrieval.py [--index-version V] [--min-similarity 0.0]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geniebot.db.session import get_sessionmaker
from geniebot.kb.build_pipeline import EvalCase, evaluate_index
from geniebot.kb.factory import get_vector_store
from geniebot.llm.factory import get_llm_client
from geniebot.settings import get_settings, get_thresholds

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "tests" / "retrieval_eval" / "fixtures" / "eval_set.json"


async def main(index_version: str | None, min_similarity: float | None) -> None:
    fixtures = json.loads(FIXTURES_PATH.read_text())
    cases = [
        EvalCase(
            error_signature=f["error_signature"], failing_module=f["failing_module"],
            error_category=f["error_category"], expected_source_refs=set(f["expected_source_refs"]),
        )
        for f in fixtures
    ]

    settings = get_settings()
    thresholds = get_thresholds()
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        vector_store = get_vector_store(session)
        resolved_index_version = index_version or await vector_store.get_active_index_version()
        if resolved_index_version is None:
            print("No active index_version - run scripts/seed_kb_sample_corpus.py first.")
            return

        result = await evaluate_index(
            cases, session=session, vector_store=vector_store, llm_client=get_llm_client(),
            index_version=resolved_index_version,
            top_n=thresholds["retrieval"]["top_n"],
            min_similarity=min_similarity if min_similarity is not None else thresholds["retrieval"]["min_similarity"],
            rerank_margin=thresholds["retrieval"]["rerank_margin"],
            embedding_model=settings.embedding_model,
        )

    print(f"index_version: {resolved_index_version}")
    print(f"cases:         {result.cases_evaluated}")
    print(f"recall:        {result.recall:.1%}")
    print(f"precision:     {result.precision:.1%}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-version", default=None)
    parser.add_argument("--min-similarity", type=float, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.index_version, args.min_similarity))
