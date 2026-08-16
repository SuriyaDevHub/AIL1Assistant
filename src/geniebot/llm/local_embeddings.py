"""Deterministic, offline embedding used wherever no real embeddings
endpoint is available: MockLLMClient (no real backend at all) and
AnthropicLLMClient (Anthropic has no embeddings API, so KB retrieval falls
back to this even when generation is real) - see kb/factory.py and
llm/anthropic_client.py.

Feature-hashed bag-of-tokens - good enough for local retrieval-recall
tests and demos, not a real embedding model's semantic output. Swap
EMBEDDING_MODEL to a provider that actually serves embeddings (e.g.
LLM_BACKEND=openai, which uses OpenAI's real text-embedding-3-small) for
production-quality retrieval.
"""
from __future__ import annotations

import hashlib
import re
from math import sqrt

from geniebot.llm.client import EMBED_DIM


def deterministic_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[bucket] += sign
    norm = sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
