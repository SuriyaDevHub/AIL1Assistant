"""Historical Jira ticket source - doc section 4.1 "Historical Jira tickets:
closed Genie Bot support issues with resolutions -> primary diagnosis
precedent". Accepts an optional real fetcher (wired to
integrations.jira_client in production - see scripts/seed_kb_sample_corpus.py)
so this module has no hard dependency on the Jira integration; it falls
back to a small representative sample so the KB build pipeline is runnable
with zero configuration.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from geniebot.schemas.kb import RawDocument

FetchFn = Callable[[], Awaitable[list[dict]]]

_SAMPLE_TICKETS = [
    {
        "key": "GENIE-1042",
        "summary": "Payments loader batch failing with ConnectionError",
        "body": (
            "Problem:\n"
            "Batch job payments-loader-nightly failed with "
            "ConnectionError: could not connect to host db01.internal in module payments/loader.py\n\n"
            "Root Cause:\n"
            "Database host db01 underwent unscheduled maintenance during the batch window, causing "
            "connection refusals for the duration of the outage.\n\n"
            "Resolution:\n"
            "Confirmed db01 was back online, then reran the job with the same job_run_id. No data loss; "
            "job completed on rerun. Recommend a controlled rerun once connectivity is confirmed restored."
        ),
    },
    {
        "key": "GENIE-1077",
        "summary": "Reconciliation bot failing with AuthenticationExpiredError",
        "body": (
            "Problem:\n"
            "recon-bot-eod failed with AuthenticationExpiredError: token expired in module "
            "auth/session.py\n\n"
            "Root Cause:\n"
            "The service account's OpenAM token was not refreshed before the batch window because the "
            "scheduled refresh job had not run since the prior credential rotation.\n\n"
            "Resolution:\n"
            "Manually refreshed the service account token and reran the batch. Escalated separately to "
            "fix the refresh scheduler."
        ),
    },
    {
        "key": "GENIE-1103",
        "summary": "Settlement bot TimeoutError against downstream pricing service",
        "body": (
            "Problem:\n"
            "settlement-bot failed with TimeoutError: read timed out in module pricing/client.py while "
            "calling the downstream pricing service.\n\n"
            "Root Cause:\n"
            "The downstream pricing service was degraded during a partial regional outage.\n\n"
            "Resolution:\n"
            "Waited for the downstream service status page to confirm recovery, then reran the job. "
            "Recommend a controlled rerun with backoff once the dependency is confirmed healthy."
        ),
    },
]


class JiraTicketExtractor:
    def __init__(self, fetch: FetchFn | None = None):
        self._fetch = fetch or self._sample_fetch

    async def _sample_fetch(self) -> list[dict]:
        return _SAMPLE_TICKETS

    async def extract(self) -> list[RawDocument]:
        tickets = await self._fetch()
        now = datetime.now(UTC)
        return [
            RawDocument(
                doc_id=t["key"],
                doc_type="resolved_incident",
                source_ref=t["key"],
                raw_text=f"{t['summary']}\n\n{t['body']}",
                created_at=now,
            )
            for t in tickets
        ]
