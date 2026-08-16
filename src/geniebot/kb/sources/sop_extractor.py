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
    {
        "id": "SOP-DATAVAL-01",
        "title": "Genie Bot inbound file data validation failures",
        "body": (
            "Problem:\n"
            "Any Genie Bot batch job failing with SchemaValidationFailure, missing/malformed required "
            "fields, or a row-level validation rejection while loading an inbound file (e.g. a required "
            "column absent, an unparseable amount/date, an out-of-range value). This category is not "
            "eligible for automated resolution (config/taxonomy.yaml: DATA_VALIDATION is rollout_status "
            "disabled) - always escalate to L2, even when the diagnosis is high-confidence.\n\n"
            "Resolution:\n"
            "1. Pull the exact rejected row(s) from the execution log evidence lines and confirm which "
            "field(s) failed validation and why.\n"
            "2. Check whether the upstream source system changed its file format or export job recently - "
            "most recurrences trace back to an unannounced upstream schema change, not a one-off bad "
            "record.\n"
            "3. If it's a genuinely isolated bad record (e.g. a single malformed row), coordinate with the "
            "data owner to correct or exclude that record at the source, then approve a controlled rerun "
            "with the original job_run_id.\n"
            "4. If it's a systemic format change, do not rerun against the same file - file a ticket with "
            "the upstream team and hold the batch until a corrected export is provided.\n"
            "5. Never approve a rerun that silently drops or defaults invalid rows to force a pass - "
            "financial batch data must fail loud, not be guessed at."
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
