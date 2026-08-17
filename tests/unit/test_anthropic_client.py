"""AnthropicLLMClient.chat_completion's exception wrapping - every real
failure must become an LLMPlatformError, so pipeline.py's fail-closed
handling (PLATFORM_UNAVAILABLE -> MANUAL_FALLBACK) actually catches it,
rather than crashing process_incident_from_log outright (which the
worker's blanket retry then re-runs from the top, hitting an illegal
SCREENED->SCREENED transition on the *original* problem's error message)."""
from __future__ import annotations

import httpx
import pytest
from anthropic import APIConnectionError

from geniebot.llm.anthropic_client import AnthropicLLMClient
from geniebot.llm.auth import StaticTokenProvider
from geniebot.llm.client import LLMPlatformError


class _FakeMessages:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def create(self, **kwargs):
        raise self._exc


class _FakeAnthropicClient:
    def __init__(self, exc: Exception):
        self.messages = _FakeMessages(exc)


def _client_raising(exc: Exception) -> AnthropicLLMClient:
    client = AnthropicLLMClient(base_url="https://api.anthropic.com", token_provider=StaticTokenProvider(""))
    client._client = _FakeAnthropicClient(exc)  # bypass lazy construction - inject the failure directly
    return client


@pytest.mark.asyncio
async def test_unexpected_client_error_wrapped_as_llm_platform_error():
    """Regression test for the exact failure observed live: a missing/
    invalid ANTHROPIC_API_KEY raises a bare TypeError from the SDK's own
    header validation, before any request is even sent - not one of the
    three typed anthropic.* exceptions the original except clauses
    covered."""
    client = _client_raising(TypeError("Could not resolve authentication method"))
    with pytest.raises(LLMPlatformError):
        await client.chat_completion(system="s", user="u", model="claude-sonnet-5")


@pytest.mark.asyncio
async def test_connection_error_wrapped_as_llm_platform_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    client = _client_raising(APIConnectionError(request=request))
    with pytest.raises(LLMPlatformError):
        await client.chat_completion(system="s", user="u", model="claude-sonnet-5")
