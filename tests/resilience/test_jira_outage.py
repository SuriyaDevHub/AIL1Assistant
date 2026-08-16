"""doc 9 "Resilience" row: Jira outage - the incident must not be left in
an inconsistent "closed but no ticket" state; it stays SUBMITTED for retry."""
from decimal import Decimal

import pytest

from geniebot.db.models import Environment, Incident
from geniebot.integrations.integration_agent import submit_incident
from geniebot.integrations.jira_client import JiraClient
from geniebot.integrations.mail_client import MockMailClient
from geniebot.state_machine import IncidentStatus


class OutageJiraClient(JiraClient):
    async def create_issue(self, **kwargs):
        raise ConnectionError("simulated Jira outage")

    async def add_comment(self, issue_key, comment):
        raise ConnectionError("simulated Jira outage")

    async def search_open_by_label(self, label, *, since):
        raise ConnectionError("simulated Jira outage")

    async def search_closed(self, *, project_key, limit=50):
        raise ConnectionError("simulated Jira outage")

    async def get_issue(self, issue_key):
        raise ConnectionError("simulated Jira outage")


@pytest.mark.asyncio
async def test_jira_outage_leaves_incident_unclosed_and_ticketless(session):
    incident = Incident(
        bot_id="b1", job_run_id="r1", user_id="alice", environment=Environment.PRODUCTION,
        log_s3_uri="s3://b/k", status=IncidentStatus.SUBMITTED,
        parse_output={"exception_type": "ConnectionError", "failing_module": "m.py", "evidence_lines": []},
        diagnosis={"root_cause": "x", "proposed_resolution": "y"},
        template_payload={"summary": "s", "root_cause": "x", "recommended_action": "y", "error_category": "CONNECTIVITY", "unknown_fields": []},
        token_cost_usd=Decimal("0.01"),
    )
    session.add(incident)
    await session.commit()

    with pytest.raises(ConnectionError):
        await submit_incident(
            session, incident, jira_client=OutageJiraClient(), mail_client=MockMailClient(),
            dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
        )

    assert incident.status == IncidentStatus.SUBMITTED
    assert incident.jira_key is None
