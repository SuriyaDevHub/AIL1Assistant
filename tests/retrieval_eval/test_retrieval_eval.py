"""doc 9 "Retrieval evaluation" row: labelled query to expected chunk set,
measures recall and precision per index version.

Note on min_similarity: MockLLMClient.embed() (llm/mock_client.py) is a
feature-hashed bag-of-tokens embedding, not a real semantic embedding
model - it clears a much lower similarity bar than
config/thresholds.yaml's production default (0.55, itself provisional
pending calibration, doc 13). This test passes min_similarity=0.0 to
isolate ranking/recall/precision correctness from that calibration
question; scripts/evaluate_retrieval.py against a real embedding backend
is what doc 9.1's actual threshold gets measured against.
"""
import json
from pathlib import Path

import pytest

from geniebot.kb.build_pipeline import EvalCase, evaluate_index, run_full_build
from geniebot.kb.sources.error_catalogue import ErrorCatalogueExtractor
from geniebot.kb.sources.jira_extractor import JiraTicketExtractor
from geniebot.kb.sources.log_sample_extractor import LogSampleExtractor
from geniebot.kb.sources.mailbox_extractor import MailboxArchiveExtractor
from geniebot.kb.sources.sop_extractor import SOPExtractor
from geniebot.settings import get_taxonomy

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "eval_set.json").read_text())


def _eval_cases() -> list[EvalCase]:
    return [
        EvalCase(
            error_signature=f["error_signature"],
            failing_module=f["failing_module"],
            error_category=f["error_category"],
            expected_source_refs=set(f["expected_source_refs"]),
        )
        for f in FIXTURES
    ]


@pytest.mark.asyncio
async def test_retrieval_recall_and_precision_meet_minimum(session, vector_store, llm_client):
    extractors = [
        JiraTicketExtractor(), MailboxArchiveExtractor(), SOPExtractor(),
        ErrorCatalogueExtractor(), LogSampleExtractor(),
    ]
    report = await run_full_build(
        extractors=extractors, session=session, vector_store=vector_store, llm_client=llm_client,
        taxonomy=get_taxonomy(), index_version="test-v1", embedding_model="text-embedding-small",
        eval_set=_eval_cases(), top_n=5, min_similarity=0.0,
    )
    assert report.chunks_indexed > 0
    assert report.evaluation is not None
    assert report.evaluation.recall >= 0.6
    assert report.evaluation.precision >= 0.6
    assert report.promoted  # first index always promotes (doc 4.2 step 12)


@pytest.mark.asyncio
async def test_retrieval_evaluation_is_empty_for_no_cases(session, vector_store, llm_client):
    result = await evaluate_index(
        [], session=session, vector_store=vector_store, llm_client=llm_client,
        index_version="v1", top_n=5, min_similarity=0.0, rerank_margin=0.03,
        embedding_model="text-embedding-small",
    )
    assert result.cases_evaluated == 0
    assert result.recall == 0.0
