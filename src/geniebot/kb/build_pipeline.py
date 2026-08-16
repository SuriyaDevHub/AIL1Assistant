"""KB build pipeline - doc section 4.2, twelve stages: extract, normalise,
redact, classify, deduplicate, curate, label, chunk, embed, index,
evaluate, promote/rollback. Every stage is a standalone function so it can
be unit tested in isolation (doc section 9 "unit" row: "redaction rules,
signature hashing, dedup matching, schema validation") as well as run
end to end via run_full_build(). Both the one-time initial corpus build and
the continuous feedback-loop maintenance path (doc 4.5) call these same
stage functions, per the doc's "both paths use the same ingestion pipeline"
requirement.
"""
from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from geniebot.db.models import KBChunk
from geniebot.guardrails.redaction_patterns import redact
from geniebot.kb.chunking import chunk_document
from geniebot.kb.retrieval import retrieve
from geniebot.kb.vector_store import VectorRecord, VectorStore
from geniebot.llm.client import LLMClient
from geniebot.schemas.kb import KBChunkCandidate, RawDocument
from geniebot.taxonomy_matching import match_error_category

Classification = Literal["public", "internal", "confidential", "restricted"]
CLASSIFICATION_ORDER: list[Classification] = ["public", "internal", "confidential", "restricted"]


@dataclass(frozen=True)
class ExclusionRecord:
    doc_id: str
    reason: str


@dataclass(frozen=True)
class DedupRecord:
    kept_doc_id: str
    removed_doc_id: str
    basis: str


@dataclass(frozen=True)
class CurationFlag:
    doc_id: str
    reason: str = "sampled_for_review"


@dataclass(frozen=True)
class EvalCase:
    """One row of the retrieval evaluation set (doc 4.2 step 11 / doc 3.3
    "evaluation set")."""

    error_signature: str
    failing_module: str
    error_category: str
    expected_source_refs: set[str]


@dataclass(frozen=True)
class EvalResult:
    recall: float
    precision: float
    cases_evaluated: int


@dataclass
class BuildReport:
    index_version: str
    extracted: int = 0
    excluded: list[ExclusionRecord] = field(default_factory=list)
    deduped: list[DedupRecord] = field(default_factory=list)
    curation_sample: list[CurationFlag] = field(default_factory=list)
    chunks_indexed: int = 0
    evaluation: EvalResult | None = None
    promoted: bool = False


# --- Stage 1: extract ------------------------------------------------------


async def extract(extractors: list) -> list[RawDocument]:
    docs: list[RawDocument] = []
    for extractor in extractors:
        docs.extend(await extractor.extract())
    return docs


# --- Stage 2: normalise -----------------------------------------------------


def normalise(docs: list[RawDocument]) -> list[RawDocument]:
    """Common envelope is already enforced by the RawDocument schema; this
    stage strips boilerplate whitespace and drops empty documents."""
    out = []
    for d in docs:
        text = re.sub(r"[ \t]+\n", "\n", d.raw_text.strip())
        text = re.sub(r"\n{3,}", "\n\n", text)
        if text:
            out.append(d.model_copy(update={"raw_text": text}))
    return out


# --- Stage 3: redact ---------------------------------------------------


def redact_documents(docs: list[RawDocument]) -> list[RawDocument]:
    """Same redaction engine used at runtime (doc 4.2 step 3)."""
    out = []
    for d in docs:
        result = redact(d.raw_text)
        out.append(d.model_copy(update={"raw_text": result.redacted_text}))
    return out


# --- Stage 4: classify ---------------------------------------------------

_RESTRICTED_MARKERS = ("[REDACTED_CONNECTION_STRING]", "[REDACTED_AWS_ACCESS_KEY]")
_CONFIDENTIAL_MARKERS = ("[REDACTED_SSN]", "[REDACTED_ACCOUNT_NUMBER]", "[REDACTED_EMAIL]", "[REDACTED_PHONE]")


def classify_document(text: str) -> Classification:
    if any(m in text for m in _RESTRICTED_MARKERS):
        return "restricted"
    if any(m in text for m in _CONFIDENTIAL_MARKERS):
        return "confidential"
    return "internal"


def classify_documents(
    docs: list[RawDocument], *, block_above: Classification = "restricted"
) -> tuple[list[tuple[RawDocument, Classification]], list[ExclusionRecord]]:
    """Returns (kept documents with their classification, excluded records).
    Excluded documents are logged with a reason, never silently dropped
    (doc 4.2 step 4)."""
    kept: list[tuple[RawDocument, Classification]] = []
    excluded: list[ExclusionRecord] = []
    block_idx = CLASSIFICATION_ORDER.index(block_above)
    for d in docs:
        classification = classify_document(d.raw_text)
        if CLASSIFICATION_ORDER.index(classification) >= block_idx:
            excluded.append(
                ExclusionRecord(doc_id=d.doc_id, reason=f"classification={classification} >= block_above")
            )
        else:
            kept.append((d, classification))
    return kept, excluded


# --- Stage 5: deduplicate ---------------------------------------------------


def _content_hash(text: str) -> str:
    normalised = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def deduplicate(
    docs: list[tuple[RawDocument, Classification]], *, fuzzy_threshold: float = 0.9
) -> tuple[list[tuple[RawDocument, Classification]], list[DedupRecord]]:
    kept: list[tuple[RawDocument, Classification]] = []
    removed: list[DedupRecord] = []
    seen_hashes: dict[str, RawDocument] = {}

    for doc, classification in docs:
        h = _content_hash(doc.raw_text)
        if h in seen_hashes:
            removed.append(
                DedupRecord(kept_doc_id=seen_hashes[h].doc_id, removed_doc_id=doc.doc_id, basis="exact_hash")
            )
            continue

        fuzzy_match = None
        for kept_doc, _ in kept:
            ratio = difflib.SequenceMatcher(None, kept_doc.raw_text, doc.raw_text).ratio()
            if ratio >= fuzzy_threshold:
                fuzzy_match = kept_doc
                break
        if fuzzy_match is not None:
            removed.append(
                DedupRecord(kept_doc_id=fuzzy_match.doc_id, removed_doc_id=doc.doc_id, basis="fuzzy_similarity")
            )
            continue

        seen_hashes[h] = doc
        kept.append((doc, classification))

    return kept, removed


# --- Stage 6: curate ---------------------------------------------------


def select_curation_sample(
    docs: list[tuple[RawDocument, Classification]], *, sample_rate: float = 0.2
) -> list[CurationFlag]:
    """Doc 4.2 step 6 requires subject-matter *human* review of a sample -
    this stage can only deterministically select that sample, not perform
    the review. Retiring incorrect/obsolete records (KBChunk.retired_at) is
    a human/reviewer action taken later, not an automatic pipeline step."""
    if not docs or sample_rate <= 0:
        return []
    step = max(1, round(1 / sample_rate))
    return [CurationFlag(doc_id=doc.doc_id) for i, (doc, _) in enumerate(docs) if i % step == 0]


# --- Stage 7: label ---------------------------------------------------


def label_documents(
    docs: list[tuple[RawDocument, Classification]], *, taxonomy: dict
) -> list[tuple[RawDocument, Classification, str]]:
    """Assigns error_category from the agreed taxonomy (doc 4.2 step 7).
    Prefers an explicit hint from the source extractor; otherwise does
    simple keyword matching against each category's label/description."""
    labeled = []
    for doc, classification in docs:
        category = doc.error_category_hint or match_error_category(doc.raw_text, taxonomy) or "UNKNOWN"
        labeled.append((doc, classification, category))
    return labeled


# --- Stage 8: chunk ---------------------------------------------------


def chunk_all(docs: list[tuple[RawDocument, Classification, str]]) -> list[KBChunkCandidate]:
    chunks: list[KBChunkCandidate] = []
    for doc, classification, category in docs:
        chunks.extend(
            chunk_document(
                doc_id=doc.doc_id,
                doc_type=doc.doc_type,
                source_ref=doc.source_ref,
                error_category=category,
                classification=classification,
                raw_text=doc.raw_text,
            )
        )
    return chunks


# --- Stage 9: embed ---------------------------------------------------


async def embed_chunks(
    chunks: list[KBChunkCandidate], llm_client: LLMClient, *, embedding_model: str, batch_size: int = 32
) -> list[tuple[KBChunkCandidate, list[float]]]:
    """Batches requests to control cost and rate limits (doc 4.2 step 9)."""
    results: list[tuple[KBChunkCandidate, list[float]]] = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        response = await llm_client.embed([c.content for c in batch], model=embedding_model)
        results.extend(zip(batch, response.vectors))
    return results


# --- Stage 10: index ---------------------------------------------------


async def index_chunks(
    embedded: list[tuple[KBChunkCandidate, list[float]]],
    *,
    session: AsyncSession,
    vector_store: VectorStore,
    index_version: str,
) -> int:
    now = datetime.now(UTC)
    vector_records = []
    for candidate, embedding in embedded:
        row = KBChunk(
            doc_id=candidate.doc_id,
            doc_type=candidate.doc_type,
            content=candidate.content,
            embedding=embedding,
            error_category=candidate.error_category,
            source_ref=candidate.source_ref,
            parent_summary=candidate.parent_summary,
            classification=candidate.classification,
            index_version=index_version,
            effective_from=now,
        )
        session.add(row)
        await session.flush()
        vector_records.append(
            VectorRecord(
                chunk_id=row.chunk_id,
                embedding=embedding,
                error_category=candidate.error_category,
                index_version=index_version,
            )
        )
    await session.commit()
    await vector_store.upsert(vector_records)
    return len(vector_records)


# --- Stage 11: evaluate ---------------------------------------------------


async def evaluate_index(
    eval_set: list[EvalCase],
    *,
    session: AsyncSession,
    vector_store: VectorStore,
    llm_client: LLMClient,
    index_version: str,
    top_n: int,
    min_similarity: float,
    rerank_margin: float,
    embedding_model: str,
) -> EvalResult:
    if not eval_set:
        return EvalResult(recall=0.0, precision=0.0, cases_evaluated=0)

    recalls = []
    precisions = []
    for case in eval_set:
        result = await retrieve(
            session,
            vector_store,
            llm_client,
            error_signature=case.error_signature,
            failing_module=case.failing_module,
            error_category=case.error_category,
            index_version=index_version,
            top_n=top_n,
            min_similarity=min_similarity,
            rerank_margin=rerank_margin,
            embedding_model=embedding_model,
        )
        returned_refs = {c.source_ref for c in result.chunks}
        hits = returned_refs & case.expected_source_refs
        recalls.append(len(hits) / len(case.expected_source_refs) if case.expected_source_refs else 0.0)
        precisions.append(len(hits) / len(returned_refs) if returned_refs else 0.0)

    return EvalResult(
        recall=sum(recalls) / len(recalls),
        precision=sum(precisions) / len(precisions),
        cases_evaluated=len(eval_set),
    )


# --- Stage 12: promote / rollback ---------------------------------------------------


async def promote_or_rollback(
    *,
    session: AsyncSession,
    vector_store: VectorStore,
    index_version: str,
    new_eval: EvalResult,
    current_recall: float,
    current_precision: float,
) -> bool:
    """Promotes only if recall and precision meet or exceed the current
    active index (doc 4.2 step 11); otherwise discards the candidate index
    and keeps the previous one active (doc 4.2 step 12: "retain the
    previous version for rollback" - here "rollback" means never switching
    the pointer away from it in the first place)."""
    should_promote = new_eval.recall >= current_recall and new_eval.precision >= current_precision
    if should_promote:
        await vector_store.set_active_index_version(index_version)
        result = await session.execute(select(KBChunk.index_version).distinct())
        _ = result  # no-op touch to keep session active in this branch
    else:
        await vector_store.delete_index_version(index_version)
    return should_promote


# --- Single-document indexing (doc 4.5 continuous maintenance path) --------


async def index_single_document(
    doc: RawDocument,
    *,
    session: AsyncSession,
    vector_store: VectorStore,
    llm_client: LLMClient,
    taxonomy: dict,
    index_version: str,
    embedding_model: str,
    block_above: Classification = "restricted",
) -> int:
    """Runs one already-approved document through normalise/redact/classify/
    label/chunk/embed/index (skipping dedupe/curate, which only make sense
    across a batch) and appends it to `index_version`. Shared by the
    /kb/documents API endpoint and the feedback loop (feedback/
    resolution_capture.py, feedback/sop_drafter.py) so both go through the
    same runtime redaction/classification gate as the initial corpus build
    (doc 4.2's "both paths use the same ingestion pipeline" requirement).
    Raises ValueError if the document is excluded by classification.
    """
    normalised = normalise([doc])
    redacted = redact_documents(normalised)
    classified, excluded = classify_documents(redacted, block_above=block_above)
    if excluded:
        raise ValueError(f"document excluded: {excluded[0].reason}")

    labeled = label_documents(classified, taxonomy=taxonomy)
    chunks = chunk_all(labeled)
    embedded = await embed_chunks(chunks, llm_client, embedding_model=embedding_model)
    return await index_chunks(embedded, session=session, vector_store=vector_store, index_version=index_version)


# --- Orchestration ---------------------------------------------------


async def run_full_build(
    *,
    extractors: list,
    session: AsyncSession,
    vector_store: VectorStore,
    llm_client: LLMClient,
    taxonomy: dict,
    index_version: str,
    embedding_model: str,
    block_above: Classification = "restricted",
    fuzzy_threshold: float = 0.9,
    curation_sample_rate: float = 0.2,
    eval_set: list[EvalCase] | None = None,
    top_n: int = 5,
    min_similarity: float = 0.55,
    rerank_margin: float = 0.03,
    current_recall: float = 0.0,
    current_precision: float = 0.0,
) -> BuildReport:
    report = BuildReport(index_version=index_version)

    raw_docs = await extract(extractors)
    report.extracted = len(raw_docs)

    normalised = normalise(raw_docs)
    redacted = redact_documents(normalised)
    classified, excluded = classify_documents(redacted, block_above=block_above)
    report.excluded = excluded

    deduped, dedup_records = deduplicate(classified, fuzzy_threshold=fuzzy_threshold)
    report.deduped = dedup_records

    report.curation_sample = select_curation_sample(deduped, sample_rate=curation_sample_rate)

    labeled = label_documents(deduped, taxonomy=taxonomy)
    chunks = chunk_all(labeled)

    embedded = await embed_chunks(chunks, llm_client, embedding_model=embedding_model)
    report.chunks_indexed = await index_chunks(
        embedded, session=session, vector_store=vector_store, index_version=index_version
    )

    if eval_set:
        eval_result = await evaluate_index(
            eval_set,
            session=session,
            vector_store=vector_store,
            llm_client=llm_client,
            index_version=index_version,
            top_n=top_n,
            min_similarity=min_similarity,
            rerank_margin=rerank_margin,
            embedding_model=embedding_model,
        )
        report.evaluation = eval_result
        report.promoted = await promote_or_rollback(
            session=session,
            vector_store=vector_store,
            index_version=index_version,
            new_eval=eval_result,
            current_recall=current_recall,
            current_precision=current_precision,
        )
    else:
        await vector_store.set_active_index_version(index_version)
        report.promoted = True

    return report
