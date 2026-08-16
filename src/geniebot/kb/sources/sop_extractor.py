"""Existing SOPs and runbooks source - doc section 4.1: "Documented
operational procedures -> authoritative resolution steps"."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from geniebot.schemas.kb import RawDocument

FetchFn = Callable[[], Awaitable[list[dict]]]

_SAMPLE_SOPS = [
    {
        "id": "SOP-CONNECTIVITY-01",
        "title": "Genie Bot database connectivity failures",
        "body": (
            "Problem:\n"
            "Any Genie Bot batch job failing with ConnectionError or TimeoutError against an internal "
            "database host.\n\n"
            "Resolution:\n"
            "1. Check the platform status page for scheduled maintenance on the target host.\n"
            "2. If maintenance is in progress, wait for completion before approving a rerun.\n"
            "3. If no maintenance is scheduled, escalate to the database on-call before rerunning.\n"
            "4. Once connectivity is confirmed restored, a controlled rerun with the original "
            "job_run_id is safe - the loader is idempotent per run id."
        ),
    },
    {
        "id": "SOP-AUTH-01",
        "title": "Genie Bot service account token expiry",
        "body": (
            "Problem:\n"
            "Any Genie Bot batch job failing with AuthenticationExpiredError or a 401/403 from an "
            "internal API.\n\n"
            "Resolution:\n"
            "1. Confirm the service account's credential rotation date against the platform IAM log.\n"
            "2. Manually trigger the token refresh job if the automated scheduler missed its window.\n"
            "3. Reissue the token and approve a controlled rerun once the new token is confirmed valid.\n"
            "4. If this is the second occurrence for the same bot within 30 days, file a platform ticket "
            "against the refresh scheduler rather than only treating the symptom."
        ),
    },
]


class SOPExtractor:
    def __init__(self, fetch: FetchFn | None = None):
        self._fetch = fetch or self._sample_fetch

    async def _sample_fetch(self) -> list[dict]:
        return _SAMPLE_SOPS

    async def extract(self) -> list[RawDocument]:
        sops = await self._fetch()
        now = datetime.now(UTC)
        return [
            RawDocument(
                doc_id=s["id"],
                doc_type="sop",
                source_ref=s["id"],
                raw_text=f"{s['title']}\n\n{s['body']}",
                created_at=now,
            )
            for s in sops
        ]
