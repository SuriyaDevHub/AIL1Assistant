"""Builds the configured LLMClient/TokenProvider from settings. The single
place that knows about both mock and real implementations - everything else
depends only on the LLMClient abstraction."""
from __future__ import annotations

from functools import lru_cache

from geniebot.llm.auth import MockTokenProvider, OpenAMDSPTokenProvider, TokenProvider
from geniebot.llm.client import LLMClient
from geniebot.llm.internal_platform_client import InternalPlatformLLMClient
from geniebot.llm.mock_client import MockLLMClient
from geniebot.settings import get_settings


@lru_cache
def get_token_provider() -> TokenProvider:
    settings = get_settings()
    if settings.auth_backend == "openam":
        return OpenAMDSPTokenProvider(
            openam_token_url=settings.openam_token_url,
            dsp_translate_url=settings.dsp_translate_url,
            client_id=settings.openam_client_id,
            client_secret=settings.openam_client_secret,
        )
    return MockTokenProvider()


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.llm_backend == "internal_platform":
        return InternalPlatformLLMClient(
            base_url=settings.internal_ai_platform_base_url,
            token_provider=get_token_provider(),
        )
    return MockLLMClient()
