"""LLM client abstraction. Every agent (doc section 5) talks to this
interface only - never to a concrete SDK - so swapping the mock for the
real internal AI platform client (internal_platform_client.py) is a
settings.llm_backend change, not a code change.

EMBED_DIM must match whatever the real text-embedding-small model actually
returns before production cutover; 64 here is a placeholder chosen for fast
local tests, not a documented platform value (doc open item, section 13).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

EMBED_DIM = 64


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    model: str
    tokens: int


class LLMClient(ABC):
    @abstractmethod
    async def chat_completion(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1500,
    ) -> LLMResponse: ...

    @abstractmethod
    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResponse: ...


class LLMPlatformError(RuntimeError):
    """Raised on any failure talking to the LLM backend - callers treat this
    as a trigger for PLATFORM_UNAVAILABLE / MANUAL_FALLBACK (doc 1.4)."""
