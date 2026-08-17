"""Real client for the Anthropic Messages API (LLM_BACKEND=anthropic).

Uses the official `anthropic` SDK rather than hand-rolled HTTP - its wire
format differs from the OpenAI-compatible one internal_platform_client.py
assumes (auth is `x-api-key`, `system` is a separate top-level field, and a
response's `content` is a list of typed blocks - thinking/text/... - rather
than a single string), and the SDK already handles that mapping, retries
(connection errors, 408/409/429/5xx with backoff), and typed exceptions
correctly.

Anthropic has no embeddings endpoint, so `embed()` falls back to the same
deterministic local embedding MockLLMClient uses (llm/local_embeddings.py) -
KB retrieval still works, just without real semantic embeddings. Use
LLM_BACKEND=openai instead if real embeddings matter more than a real
generation model for a given demo.
"""
from __future__ import annotations

import time

import anthropic

from geniebot.llm.auth import TokenProvider
from geniebot.llm.client import EmbeddingResponse, LLMClient, LLMPlatformError, LLMResponse
from geniebot.llm.local_embeddings import deterministic_embedding


class AnthropicLLMClient(LLMClient):
    def __init__(
        self,
        *,
        base_url: str,
        token_provider: TokenProvider,
        timeout_seconds: float = 60.0,
    ):
        self._base_url = base_url
        self._token_provider = token_provider
        self._timeout_seconds = timeout_seconds
        self._client: anthropic.AsyncAnthropic | None = None

    async def _get_client(self) -> anthropic.AsyncAnthropic:
        # Built lazily on first use, from the token provider's key, so the
        # SDK client (which wants a synchronous api_key at construction) can
        # still be sourced from the same async TokenProvider abstraction
        # every other real client here uses. Cached rather than rebuilt per
        # call - fine for StaticTokenProvider (llm/auth.py), whose key never
        # changes; a refreshing TokenProvider would need this reconsidered.
        if self._client is None:
            api_key = await self._token_provider.get_token()
            self._client = anthropic.AsyncAnthropic(
                api_key=api_key, base_url=self._base_url, timeout=self._timeout_seconds
            )
        return self._client

    async def chat_completion(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1500,
    ) -> LLMResponse:
        start = time.perf_counter()
        client = await self._get_client()
        try:
            # temperature (and top_p/top_k) is rejected outright on current
            # Claude models ("`temperature` is deprecated for this model",
            # confirmed live against claude-sonnet-5) - the `temperature`
            # parameter this method takes is part of the shared LLMClient
            # interface (MockLLMClient and internal_platform_client.py both
            # use it) but isn't forwarded here. Determinism instead comes
            # from the prompts' own "strict JSON only" instructions plus
            # BaseAgent's bounded corrective retry on a bad response.
            response = await client.messages.create(
                model=model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.RateLimitError as exc:
            raise LLMPlatformError(f"rate limited: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMPlatformError(f"connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMPlatformError(f"API error {exc.status_code}: {exc.message}") from exc
        except Exception as exc:
            # Defensive catch-all (matches internal_platform_client.py's own
            # equivalent) - not every real failure is one of the three typed
            # exceptions above. A missing/invalid ANTHROPIC_API_KEY, for one,
            # raises a bare TypeError from the SDK's own header validation,
            # before any request is even sent - confirmed live. Without this,
            # that exception isn't an LLMPlatformError, so it never reaches
            # pipeline.py's fail-closed handling and just crashes
            # process_incident_from_log outright, leaving the incident stuck
            # mid-pipeline; the worker's retry then re-runs from the top and
            # hits an illegal SCREENED->SCREENED transition instead of the
            # original error, retrying that forever. Wrapping here routes it
            # to PLATFORM_UNAVAILABLE -> MANUAL_FALLBACK correctly instead,
            # same as every other genuine platform failure.
            raise LLMPlatformError(f"unexpected Anthropic client error: {exc}") from exc

        latency_ms = (time.perf_counter() - start) * 1000

        if response.stop_reason == "refusal":
            raise LLMPlatformError("Anthropic declined the request (stop_reason=refusal)")

        # content is a list of typed blocks (thinking/text/...) - the
        # agents only ever want the text, per doc 5.1 "strict JSON only".
        content = "".join(block.text for block in response.content if block.type == "text")

        return LLMResponse(
            content=content,
            model=response.model,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResponse:
        vectors = [deterministic_embedding(t) for t in texts]
        return EmbeddingResponse(
            vectors=vectors, model=model, tokens=sum(max(1, len(t) // 4) for t in texts)
        )
