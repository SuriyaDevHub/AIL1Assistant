"""Local FAISS-backed VectorStore - an interim, restart-durable alternative
to VECTOR_STORE_BACKEND=memory for an environment without a provisioned
pgvector database yet (kept out of vector_store.py so that module's
consumers don't transitively import faiss/numpy just because this backend
option exists - only kb/factory.py conditionally imports this module).

Single-process only, same constraint VECTOR_STORE_BACKEND=memory already
has today - a local index file isn't safely read/written by more than one
process at a time (two processes writing around the same time is silent
data loss on the metadata sidecar, not just staleness; a write interrupted
by a crash leaves a genuinely corrupt file). Use the combined
scripts/seed_and_run_worker.py, or otherwise guarantee a single process,
until a real pgvector database is available - see docs/going-live.md.

One FAISS index file per index_version (kb_vector_store.py's other two
backends both scope everything by index_version too), plus a JSON sidecar
for the filtering metadata (error_category/retired_at) FAISS itself
doesn't store, plus a plain text file for the active index version.

Every method body here is synchronous (no `await` inside `async def`,
deliberately) - InMemoryVectorStore is race-free under concurrent asyncio
callers only because none of its methods have a yield point, so one
coroutine's upsert/query runs to completion before another can interleave.
Matching that here (rather than `asyncio.to_thread`-ing the file I/O, which
would reintroduce a real race) inherits the same implicit guarantee. Don't
"fix" this by offloading to a thread.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import faiss
import numpy as np

from geniebot.kb.vector_store import ScoredChunkId, VectorRecord, VectorStore
from geniebot.llm.client import EMBED_DIM


class FaissVectorStore(VectorStore):
    def __init__(self, index_dir: str):
        self._dir = Path(index_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._dir / "metadata.json"
        self._active_path = self._dir / "active_version.txt"
        self._indexes: dict[str, faiss.Index] = {}
        # chunk_id -> {"faiss_id": int, "error_category": str, "index_version": str, "retired_at": str|None}
        self._meta: dict[str, dict] = {}
        self._next_faiss_id = 1
        self._load_metadata()

    def _index_path(self, index_version: str) -> Path:
        return self._dir / f"{index_version}.faiss"

    def _load_metadata(self) -> None:
        if not self._meta_path.exists():
            return
        with self._meta_path.open("r", encoding="utf-8") as fh:
            self._meta = json.load(fh)
        if self._meta:
            self._next_faiss_id = max(r["faiss_id"] for r in self._meta.values()) + 1

    def _save_metadata(self) -> None:
        with self._meta_path.open("w", encoding="utf-8") as fh:
            json.dump(self._meta, fh)

    def _get_index(self, index_version: str, *, create: bool) -> faiss.Index | None:
        if index_version in self._indexes:
            return self._indexes[index_version]
        path = self._index_path(index_version)
        index = None
        if path.exists():
            try:
                index = faiss.read_index(str(path))
            except Exception:
                # Corrupt file (e.g. an interrupted write) - treat the same
                # as "missing" rather than raising, so callers (has_data,
                # kb/factory.py's rebuild-from-KBChunk path) can recover
                # instead of crashing process startup.
                index = None
        if index is None:
            if not create:
                return None
            index = faiss.IndexIDMap(faiss.IndexFlatIP(EMBED_DIM))
        self._indexes[index_version] = index
        return index

    def _save_index(self, index_version: str) -> None:
        faiss.write_index(self._indexes[index_version], str(self._index_path(index_version)))

    def has_data(self, index_version: str) -> bool:
        """True if index_version currently loads (or is already loaded)
        with at least one vector. Used by kb/factory.py's
        ensure_vector_store_schema to decide whether the active version
        needs rebuilding from KBChunk (a missing/corrupt local file is
        this backend's own failure mode - see the module docstring)."""
        index = self._get_index(index_version, create=False)
        return index is not None and index.ntotal > 0

    async def upsert(self, records: list[VectorRecord]) -> None:
        by_version: dict[str, list[VectorRecord]] = {}
        for r in records:
            by_version.setdefault(r.index_version, []).append(r)

        for index_version, version_records in by_version.items():
            index = self._get_index(index_version, create=True)
            faiss_ids = []
            for r in version_records:
                existing = self._meta.get(r.chunk_id)
                if existing is not None:
                    faiss_id = existing["faiss_id"]
                    # remove_ids before re-adding - add_with_ids does not
                    # overwrite an existing id, it would leave a stale
                    # duplicate entry instead (PgVectorStore/InMemoryVectorStore
                    # are both genuinely idempotent on re-upsert; this matches).
                    index.remove_ids(np.array([faiss_id], dtype=np.int64))
                else:
                    faiss_id = self._next_faiss_id
                    self._next_faiss_id += 1
                faiss_ids.append(faiss_id)
                self._meta[r.chunk_id] = {
                    "faiss_id": faiss_id,
                    "error_category": r.error_category,
                    "index_version": r.index_version,
                    "retired_at": r.retired_at.isoformat() if r.retired_at else None,
                }

            vectors = np.array([r.embedding for r in version_records], dtype=np.float32)
            faiss.normalize_L2(vectors)
            index.add_with_ids(vectors, np.array(faiss_ids, dtype=np.int64))
            self._save_index(index_version)

        self._save_metadata()

    async def query(
        self,
        query_vector: list[float],
        *,
        top_n: int,
        index_version: str,
        error_category: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[ScoredChunkId]:
        index = self._get_index(index_version, create=False)
        if index is None or index.ntotal == 0:
            return []

        now = datetime.now(UTC)
        id_to_chunk = {
            m["faiss_id"]: chunk_id
            for chunk_id, m in self._meta.items()
            if m["index_version"] == index_version
        }

        query = np.array([query_vector], dtype=np.float32)
        faiss.normalize_L2(query)
        # Ask for more than top_n since retired/category-mismatched hits
        # get filtered out afterward - FAISS itself doesn't filter. Capped
        # at the index's own size.
        k = min(index.ntotal, max(top_n * 4, top_n))
        distances, ids = index.search(query, k)

        scored: list[ScoredChunkId] = []
        for faiss_id, similarity in zip(ids[0], distances[0]):
            if faiss_id == -1:
                continue
            chunk_id = id_to_chunk.get(int(faiss_id))
            if chunk_id is None:
                continue
            meta = self._meta[chunk_id]
            if error_category is not None and meta["error_category"] != error_category:
                continue
            if meta["retired_at"]:
                retired_at = datetime.fromisoformat(meta["retired_at"])
                if retired_at <= now:
                    continue
            if similarity < min_similarity:
                continue
            scored.append(ScoredChunkId(chunk_id=chunk_id, similarity=float(similarity)))

        scored.sort(key=lambda s: s.similarity, reverse=True)
        return scored[:top_n]

    async def get_active_index_version(self) -> str | None:
        if not self._active_path.exists():
            return None
        value = self._active_path.read_text(encoding="utf-8").strip()
        return value or None

    async def set_active_index_version(self, index_version: str) -> None:
        self._active_path.write_text(index_version, encoding="utf-8")

    async def delete_index_version(self, index_version: str) -> None:
        self._meta = {k: v for k, v in self._meta.items() if v["index_version"] != index_version}
        self._save_metadata()
        self._indexes.pop(index_version, None)
        path = self._index_path(index_version)
        if path.exists():
            path.unlink()
