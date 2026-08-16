"""llm/factory.py::get_token_provider - keyed on whether OpenAM is actually
configured for the internal AI platform, independent of AUTH_BACKEND (a
separate, unrelated setting for the review UI's own end-user login -
reusing it here meant a deployment with real OpenAM creds configured for
the LLM platform but AUTH_BACKEND still "mock" would silently send a fake
dev token to the real platform instead of authenticating)."""
from __future__ import annotations

import os

from geniebot.llm.auth import MockTokenProvider, OpenAMDSPTokenProvider
from geniebot.llm.factory import get_token_provider
from geniebot.settings import get_settings


def _set_env(**kwargs: str | None) -> dict[str, str | None]:
    saved: dict[str, str | None] = {}
    for k, v in kwargs.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_falls_back_to_mock_without_openam_config():
    saved = _set_env(OPENAM_TOKEN_URL=None, DSP_TRANSLATE_URL=None, AUTH_BACKEND="openam")
    get_settings.cache_clear()
    get_token_provider.cache_clear()
    try:
        assert isinstance(get_token_provider(), MockTokenProvider)
    finally:
        _restore_env(saved)
        get_settings.cache_clear()
        get_token_provider.cache_clear()


def test_uses_openam_when_configured_regardless_of_auth_backend():
    saved = _set_env(
        OPENAM_TOKEN_URL="https://openam.example/token",
        DSP_TRANSLATE_URL="https://dsp.example/translate",
        AUTH_BACKEND="mock",  # deliberately NOT "openam" - the whole point of the fix
    )
    get_settings.cache_clear()
    get_token_provider.cache_clear()
    try:
        assert isinstance(get_token_provider(), OpenAMDSPTokenProvider)
    finally:
        _restore_env(saved)
        get_settings.cache_clear()
        get_token_provider.cache_clear()
