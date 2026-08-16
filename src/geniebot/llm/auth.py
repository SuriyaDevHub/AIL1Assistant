"""OpenAM / DSP token translation client (doc 2.3 "Identity" row: "JWT via
OpenAM, DSP token translation - Auto-refresh background task"). Used by any
outbound real client (internal_platform_client.py, integrations/jira_client.py
when it needs a bank-issued bearer token rather than a Jira API token).

TokenProvider.start() launches a background asyncio task that refreshes the
token shortly before expiry so request-time latency never includes an auth
round trip. MockTokenProvider is a no-op stand-in for local/dev/test.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod

import httpx


class TokenProvider(ABC):
    @abstractmethod
    async def get_token(self) -> str: ...

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class MockTokenProvider(TokenProvider):
    def __init__(self, static_token: str = "mock-dev-token"):
        self._token = static_token

    async def get_token(self) -> str:
        return self._token


class OpenAMDSPTokenProvider(TokenProvider):
    """Real implementation: OAuth2 client-credentials against OpenAM, then
    DSP token translation, cached and refreshed in the background.

    Not exercised without real openam_token_url/dsp_translate_url and
    credentials - wire those in via .env before switching AUTH_BACKEND to
    "openam" (see .env.example).
    """

    def __init__(
        self,
        *,
        openam_token_url: str,
        dsp_translate_url: str,
        client_id: str,
        client_secret: str,
        refresh_margin_seconds: int = 60,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._openam_token_url = openam_token_url
        self._dsp_translate_url = dsp_translate_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_margin = refresh_margin_seconds
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._refresh_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def _fetch_token(self) -> tuple[str, float]:
        openam_resp = await self._http.post(
            self._openam_token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        openam_resp.raise_for_status()
        openam_body = openam_resp.json()

        dsp_resp = await self._http.post(
            self._dsp_translate_url,
            headers={"Authorization": f"Bearer {openam_body['access_token']}"},
        )
        dsp_resp.raise_for_status()
        dsp_body = dsp_resp.json()

        expires_in = int(dsp_body.get("expires_in", openam_body.get("expires_in", 300)))
        return dsp_body["access_token"], time.monotonic() + expires_in

    async def _refresh(self) -> None:
        async with self._lock:
            self._token, self._expires_at = await self._fetch_token()

    async def get_token(self) -> str:
        if self._token is None or time.monotonic() >= self._expires_at - self._refresh_margin:
            await self._refresh()
        assert self._token is not None
        return self._token

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self.get_token()
                sleep_for = max(5.0, self._expires_at - time.monotonic() - self._refresh_margin)
            except Exception:
                sleep_for = 15.0
            await asyncio.sleep(sleep_for)

    async def start(self) -> None:
        if self._refresh_task is None:
            await self._refresh()
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None
        await self._http.aclose()
