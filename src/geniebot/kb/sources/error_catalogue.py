"""Known error catalogue source - doc section 4.1: "Exception to root cause
mappings -> deterministic hints for common failures"."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from geniebot.schemas.kb import RawDocument

FetchFn = Callable[[], Awaitable[list[dict]]]

_SAMPLE_ENTRIES = [
    {
        "id": "ERR-CAT-ConnectionError",
        "exception_type": "ConnectionError",
        "root_cause": "Target host unreachable - most commonly scheduled maintenance, a network ACL "
        "change, or the host being down.",
        "category": "CONNECTIVITY",
    },
    {
        "id": "ERR-CAT-TimeoutError",
        "exception_type": "TimeoutError",
        "root_cause": "A downstream call exceeded its configured timeout - usually downstream "
        "degradation rather than a client-side defect.",
        "category": "CONNECTIVITY",
    },
    {
        "id": "ERR-CAT-AuthenticationExpiredError",
        "exception_type": "AuthenticationExpiredError",
        "root_cause": "Service account token expired before use, typically because a scheduled refresh "
        "job did not run.",
        "category": "AUTH_EXPIRED",
    },
]


class ErrorCatalogueExtractor:
    def __init__(self, fetch: FetchFn | None = None):
        self._fetch = fetch or self._sample_fetch

    async def _sample_fetch(self) -> list[dict]:
        return _SAMPLE_ENTRIES

    async def extract(self) -> list[RawDocument]:
        entries = await self._fetch()
        now = datetime.now(UTC)
        return [
            RawDocument(
                doc_id=e["id"],
                doc_type="runbook",
                source_ref=e["id"],
                raw_text=f"Problem:\n{e['exception_type']}\n\nRoot Cause:\n{e['root_cause']}",
                created_at=now,
                error_category_hint=e.get("category"),
            )
            for e in entries
        ]
