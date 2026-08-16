"""doc 9 "Resilience" row: mail dispatch failure - the incident must not be
marked CLOSED if the notification never went out, so it's safe to retry."""
from decimal import Decimal

import pytest

from geniebot.db.models import Environment, Incident
from geniebot.integrations.integration_agent import submit_incident
from geniebot.integrations.jira_client import MockJiraServer
from geniebot.integrations.mail_client import MailClient
from geniebot.state_machine import IncidentStatus


class OutageMailClient(MailClient):
    async def send(self, **kwargs):
        raise ConnectionError("simulated mail relay outage")


@pytest.mark.asyncio
async def test_mail_outage_leaves_incident_unclosed(session):
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
            session, incident, jira_client=MockJiraServer(), mail_client=OutageMailClient(),
            dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
        )

    # the Jira ticket may already exist (created before the mail step) but
    # the incident must still be retriable, not silently marked done
    assert incident.status == IncidentStatus.SUBMITTED
