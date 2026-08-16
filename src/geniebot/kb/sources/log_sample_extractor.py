"""Historical execution log samples source - doc section 4.1: "Representative
stack traces per error class -> Signature matching examples"."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from geniebot.schemas.kb import RawDocument

FetchFn = Callable[[], Awaitable[list[dict]]]

_SAMPLE_LOGS = [
    {
        "id": "LOGSAMPLE-ConnectionError-01",
        "category": "CONNECTIVITY",
        "trace": (
            "Problem:\n"
            "ConnectionError: could not connect to host db01.internal:5432\n"
            "  at payments/loader.py:142 in load_batch\n"
            "  at payments/loader.py:88 in run\n"
            "  at scheduler/runner.py:41 in execute_job\n"
        ),
    },
    {
        "id": "LOGSAMPLE-TimeoutError-01",
        "category": "CONNECTIVITY",
        "trace": (
            "Problem:\n"
            "TimeoutError: read timed out after 30000ms\n"
            "  at pricing/client.py:57 in fetch_quote\n"
            "  at settlement/engine.py:203 in reconcile\n"
        ),
    },
    {
        "id": "LOGSAMPLE-AuthenticationExpiredError-01",
        "category": "AUTH_EXPIRED",
        "trace": (
            "Problem:\n"
            "AuthenticationExpiredError: token expired at 2026-08-15T23:59:12Z\n"
            "  at auth/session.py:76 in refresh_if_needed\n"
            "  at recon/bot.py:19 in run\n"
        ),
    },
]


class LogSampleExtractor:
    def __init__(self, fetch: FetchFn | None = None):
        self._fetch = fetch or self._sample_fetch

    async def _sample_fetch(self) -> list[dict]:
        return _SAMPLE_LOGS

    async def extract(self) -> list[RawDocument]:
        samples = await self._fetch()
        now = datetime.now(UTC)
        return [
            RawDocument(
                doc_id=s["id"],
                doc_type="runbook",
                source_ref=s["id"],
                raw_text=s["trace"],
                created_at=now,
                error_category_hint=s.get("category"),
            )
            for s in samples
        ]
