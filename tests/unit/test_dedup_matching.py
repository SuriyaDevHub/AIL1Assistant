from datetime import UTC
from decimal import Decimal

import pytest

from geniebot.db.models import Environment, Incident
from geniebot.integrations.dedup import check_duplicate
from geniebot.state_machine import IncidentStatus


def _make_incident(**overrides) -> Incident:
    defaults = {
        "bot_id": "payments-loader",
        "job_run_id": "run-1",
        "user_id": "alice",
        "environment": Environment.PRODUCTION,
        "log_s3_uri": "s3://b/k1",
        "status": IncidentStatus.SUBMITTED,
        "parse_output": {"exception_type": "ConnectionError", "failing_module": "payments/loader.py"},
        "diagnosis": {"root_cause": "db down"},
        "template_payload": {"summary": "ConnectionError while loading payments batch"},
        "jira_key": "GENIE-1",
        "token_cost_usd": Decimal("0.01"),
    }
    defaults.update(overrides)
    return Incident(**defaults)


@pytest.mark.asyncio
async def test_exact_match_links_duplicate(session):
    # error_signature_id is normally populated the first time an incident
    # goes through dedup itself - simulate that here so the "existing"
    # fixture looks like a genuinely already-processed incident.
    existing = _make_incident()
    session.add(existing)
    await session.commit()
    await check_duplicate(session, existing, window_hours=72, fuzzy_threshold=0.99)
    await session.commit()

    new_incident = _make_incident(
        job_run_id="run-2", jira_key=None,
        template_payload={"summary": "totally different wording that should not fuzzy-match"},
    )
    session.add(new_incident)
    await session.commit()

    result = await check_duplicate(session, new_incident, window_hours=72, fuzzy_threshold=0.99)
    assert result.is_duplicate
    assert result.basis == "exact_match"
    assert result.matched_jira_key == "GENIE-1"


@pytest.mark.asyncio
async def test_fuzzy_match_links_similar_summary(session):
    existing = _make_incident(
        parse_output={"exception_type": "TimeoutError", "failing_module": "pricing/client.py"},
        template_payload={"summary": "TimeoutError while fetching pricing quote from downstream service"},
    )
    session.add(existing)
    await session.commit()

    new_incident = _make_incident(
        job_run_id="run-2",
        jira_key=None,
        parse_output={"exception_type": "TimeoutError", "failing_module": "pricing/quote_client.py"},
        template_payload={"summary": "TimeoutError while fetching pricing quotes from the downstream service"},
    )
    session.add(new_incident)
    await session.commit()

    result = await check_duplicate(session, new_incident, window_hours=72, fuzzy_threshold=0.85)
    assert result.is_duplicate
    assert result.basis == "fuzzy_match"


@pytest.mark.asyncio
async def test_no_match_for_distinct_incident(session):
    existing = _make_incident()
    session.add(existing)
    await session.commit()

    new_incident = _make_incident(
        job_run_id="run-2",
        jira_key=None,
        parse_output={"exception_type": "AuthenticationExpiredError", "failing_module": "auth/session.py"},
        template_payload={"summary": "Completely unrelated authentication failure"},
    )
    session.add(new_incident)
    await session.commit()

    result = await check_duplicate(session, new_incident, window_hours=72, fuzzy_threshold=0.85)
    assert not result.is_duplicate


@pytest.mark.asyncio
async def test_no_match_outside_window(session):
    from datetime import datetime, timedelta

    existing = _make_incident(ingested_at=datetime.now(UTC) - timedelta(hours=200))
    session.add(existing)
    await session.commit()

    new_incident = _make_incident(job_run_id="run-2", jira_key=None)
    session.add(new_incident)
    await session.commit()

    result = await check_duplicate(session, new_incident, window_hours=72, fuzzy_threshold=0.99)
    assert not result.is_duplicate
