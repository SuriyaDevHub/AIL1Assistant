"""doc 9 "Contract" row: Jira and mail payload mapping."""
from decimal import Decimal

import pytest

from geniebot.db.models import Environment, Incident
from geniebot.integrations.integration_agent import submit_incident
from geniebot.integrations.jira_client import MockJiraServer
from geniebot.integrations.mail_client import MockMailClient
from geniebot.state_machine import IncidentStatus


def _submitted_incident(**overrides) -> Incident:
    defaults = {
        "bot_id": "payments-loader",
        "job_run_id": "run-1",
        "user_id": "alice",
        "environment": Environment.PRODUCTION,
        "log_s3_uri": "s3://bucket/key",
        "status": IncidentStatus.SUBMITTED,
        "parse_output": {
            "exception_type": "ConnectionError",
            "failing_module": "payments/loader.py",
            "evidence_lines": [{"line_no": 4, "text": "ConnectionError: could not connect"}],
        },
        "diagnosis": {"root_cause": "db down", "proposed_resolution": "retry"},
        "template_payload": {
            "summary": "ConnectionError while loading payments batch",
            "root_cause": "db down",
            "recommended_action": "retry",
            "error_category": "CONNECTIVITY",
            "unknown_fields": ["priority"],
        },
        "token_cost_usd": Decimal("0.02"),
    }
    defaults.update(overrides)
    return Incident(**defaults)


@pytest.mark.asyncio
async def test_jira_issue_created_with_expected_fields(session):
    incident = _submitted_incident()
    session.add(incident)
    await session.commit()

    jira = MockJiraServer()
    mail = MockMailClient()
    await submit_incident(
        session, incident, jira_client=jira, mail_client=mail,
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
    )

    issue = await jira.get_issue(incident.jira_key)
    assert issue is not None
    assert issue.summary == "ConnectionError while loading payments batch"
    assert "db down" in issue.description
    assert "retry" in issue.description
    assert "priority" in issue.description  # unknown_fields surfaced for the human
    assert "CONNECTIVITY" in issue.labels


@pytest.mark.asyncio
async def test_mail_dispatched_to_support_mailbox_with_jira_key(session):
    incident = _submitted_incident()
    session.add(incident)
    await session.commit()

    jira = MockJiraServer()
    mail = MockMailClient()
    await submit_incident(
        session, incident, jira_client=jira, mail_client=mail,
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
    )

    assert len(mail.sent) == 1
    sent = mail.sent[0]
    assert sent.to == "support@bank.example"
    assert incident.jira_key in sent.subject
    assert incident.bot_id in sent.subject


@pytest.mark.asyncio
async def test_incident_closed_after_successful_submission(session):
    incident = _submitted_incident()
    session.add(incident)
    await session.commit()

    await submit_incident(
        session, incident, jira_client=MockJiraServer(), mail_client=MockMailClient(),
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
    )
    assert incident.status == IncidentStatus.CLOSED
