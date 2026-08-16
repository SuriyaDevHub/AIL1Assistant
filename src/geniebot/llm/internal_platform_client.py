"""Real client for an OpenAI-compatible REST surface (POST
{base_url}/chat/completions, POST {base_url}/embeddings) - JWT-authenticated
against the internal AI platform (doc 2.3: GPT-5.1 generation,
text-embedding-small embedding) when LLM_BACKEND=internal_platform, or
plain-API-key-authenticated against a real OpenAI-compatible endpoint
(OpenAI itself, Azure OpenAI, self-hosted) when LLM_BACKEND=openai - same
client class either way, only the TokenProvider differs (llm/factory.py).

The internal platform's exact wire format isn't specified by the doc -
confirm against its actual API contract before production cutover and
adjust the two request/response mappings below if needed; the LLMClient
interface and every caller stay unchanged either way. response_format
below is OpenAI/Azure-OpenAI JSON mode - drop it if the internal platform
doesn't support it.

Not exercised without a real base URL and a working TokenProvider - set
LLM_BACKEND=internal_platform (+ AUTH_BACKEND=openam) or LLM_BACKEND=openai
(+ OPENAI_API_KEY) in .env to enable.
"""
from __future__ import annotations

import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from geniebot.llm.auth import TokenProvider
from geniebot.llm.client import EmbeddingResponse, LLMClient, LLMPlatformError, LLMResponse

_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


class InternalPlatformLLMClient(LLMClient):
    def __init__(
        self,
        *,
        base_url: str,
        token_provider: TokenProvider,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        ca_bundle: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        # ca_bundle: path to a PEM file for a restricted network's internal
        # CA (settings.internal_ai_platform_ca_bundle) - ignored if an
        # http_client is supplied directly.
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds, verify=ca_bundle or True)

    async def _headers(self) -> dict[str, str]:
        token = await self._token_provider.get_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
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
        try:
            resp = await self._http.post(
                f"{self._base_url}/chat/completions",
                headers=await self._headers(),
                json={
                    "model": model,
                    "temperature": temperature,
                    "max_tokens": max_output_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            body = resp.json()
        except _RETRYABLE:
            raise
        except Exception as exc:  # pragma: no cover - defensive, unreachable in tests
            raise LLMPlatformError(str(exc)) from exc

        latency_ms = (time.perf_counter() - start) * 1000
        try:
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
        except (KeyError, IndexError) as exc:
            raise LLMPlatformError(f"unexpected response shape: {exc}") from exc

        return LLMResponse(
            content=content,
            model=body.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def embed(self, texts: list[str], *, model: str) -> EmbeddingResponse:
        try:
            resp = await self._http.post(
                f"{self._base_url}/embeddings",
                headers=await self._headers(),
                json={"model": model, "input": texts},
            )
            resp.raise_for_status()
            body = resp.json()
        except _RETRYABLE:
            raise
        except Exception as exc:  # pragma: no cover
            raise LLMPlatformError(str(exc)) from exc

        try:
            vectors = [item["embedding"] for item in body["data"]]
            tokens = body.get("usage", {}).get("total_tokens", 0)
        except (KeyError, IndexError) as exc:
            raise LLMPlatformError(f"unexpected response shape: {exc}") from exc

        return EmbeddingResponse(vectors=vectors, model=body.get("model", model), tokens=tokens)
