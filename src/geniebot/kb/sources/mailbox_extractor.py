"""Genie Support Mailbox archive source - doc section 4.1: "Historical
support templates and email threads -> template precedent and phrasing".
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from geniebot.schemas.kb import RawDocument

FetchFn = Callable[[], Awaitable[list[dict]]]

_SAMPLE_THREADS = [
    {
        "id": "MAIL-2291",
        "subject": "[Genie Bot] Escalation: payments-loader-nightly - ConnectionError",
        "body": (
            "Problem:\n"
            "payments-loader-nightly (job_run_id=20260110-01) failed with ConnectionError connecting to "
            "db01.internal.\n\n"
            "Resolution:\n"
            "Support confirmed the DB maintenance window with the platform team, then approved a "
            "controlled rerun once maintenance completed. Ticket closed with no data loss."
        ),
    },
    {
        "id": "MAIL-2340",
        "subject": "[Genie Bot] Escalation: recon-bot-eod - AuthenticationExpiredError",
        "body": (
            "Problem:\n"
            "recon-bot-eod failed with AuthenticationExpiredError.\n\n"
            "Resolution:\n"
            "Support rotated the service account credential manually and reran the job. Follow-up item "
            "opened against the platform team to fix the automated refresh scheduler."
        ),
    },
]


class MailboxArchiveExtractor:
    def __init__(self, fetch: FetchFn | None = None):
        self._fetch = fetch or self._sample_fetch

    async def _sample_fetch(self) -> list[dict]:
        return _SAMPLE_THREADS

    async def extract(self) -> list[RawDocument]:
        threads = await self._fetch()
        now = datetime.now(UTC)
        return [
            RawDocument(
                doc_id=t["id"],
                doc_type="template",
                source_ref=t["id"],
                raw_text=f"{t['subject']}\n\n{t['body']}",
                created_at=now,
            )
            for t in threads
        ]
