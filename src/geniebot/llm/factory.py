"""Builds the configured LLMClient/TokenProvider from settings. The single
place that knows about both mock and real implementations - everything else
depends only on the LLMClient abstraction."""
from __future__ import annotations

from functools import lru_cache

from geniebot.llm.anthropic_client import AnthropicLLMClient
from geniebot.llm.auth import MockTokenProvider, OpenAMDSPTokenProvider, StaticTokenProvider, TokenProvider
from geniebot.llm.client import LLMClient
from geniebot.llm.internal_platform_client import InternalPlatformLLMClient
from geniebot.llm.mock_client import MockLLMClient
from geniebot.settings import get_settings


@lru_cache
def get_token_provider() -> TokenProvider:
    """Auth for LLM_BACKEND=internal_platform's OpenAM/DSP token
    translation - keyed on whether OpenAM is actually configured for the
    internal platform, not on AUTH_BACKEND. AUTH_BACKEND is a different,
    unrelated concern (how the review UI's own end users log in) - a
    deployment could reasonably cut the LLM backend over to a real internal
    platform with real OpenAM credentials before (or without ever) cutting
    end-user login over to openam, or vice versa. Reusing AUTH_BACKEND here
    meant that case would silently fall back to a fake dev token being sent
    to the real platform instead of erroring or actually authenticating."""
    settings = get_settings()
    if settings.openam_token_url and settings.dsp_translate_url:
        return OpenAMDSPTokenProvider(
            openam_token_url=settings.openam_token_url,
            dsp_translate_url=settings.dsp_translate_url,
            client_id=settings.openam_client_id,
            client_secret=settings.openam_client_secret,
            ca_bundle=settings.internal_ai_platform_ca_bundle or None,
        )
    return MockTokenProvider()


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.llm_backend == "internal_platform":
        return InternalPlatformLLMClient(
            base_url=settings.internal_ai_platform_base_url,
            token_provider=get_token_provider(),
            ca_bundle=settings.internal_ai_platform_ca_bundle or None,
        )
    if settings.llm_backend == "openai":
        return InternalPlatformLLMClient(
            base_url=settings.openai_base_url,
            token_provider=StaticTokenProvider(settings.openai_api_key),
        )
    if settings.llm_backend == "anthropic":
        return AnthropicLLMClient(
            base_url=settings.anthropic_base_url,
            token_provider=StaticTokenProvider(settings.anthropic_api_key),
        )
    return MockLLMClient()
