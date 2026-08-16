"""doc 9 "Contract" row: Jira and mail payload mapping."""
from decimal import Decimal

import pytest

from geniebot.db.models import Environment, Incident, ReviewDecision
from geniebot.integrations.dedup import compute_error_signature_id
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
async def test_incident_stays_submitted_when_resolution_not_confirmed(session):
    """Default (resolution_confirmed=False, the escalated path): the ticket
    is genuinely open for L2, so the incident stays SUBMITTED rather than
    closing itself - it closes later, when the ticket actually does (see
    api/routers/incidents.py's jira_closure_webhook /
    simulate_jira_closure)."""
    incident = _submitted_incident()
    session.add(incident)
    await session.commit()

    await submit_incident(
        session, incident, jira_client=MockJiraServer(), mail_client=MockMailClient(),
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
    )
    assert incident.status == IncidentStatus.SUBMITTED
    assert incident.jira_key is not None


@pytest.mark.asyncio
async def test_resolution_confirmed_closes_freshly_created_ticket(session):
    """End user reported the L1 fix worked - the ticket GenieBot just
    created for this incident is closed immediately, no L2 action needed."""
    incident = _submitted_incident()
    session.add(incident)
    await session.commit()

    jira = MockJiraServer()
    await submit_incident(
        session, incident, jira_client=jira, mail_client=MockMailClient(),
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
        resolution_confirmed=True,
    )

    issue = await jira.get_issue(incident.jira_key)
    assert issue.status == "Done"


@pytest.mark.asyncio
async def test_resolution_confirmed_does_not_close_shared_duplicate_ticket(session):
    """A dedup match means this incident's ticket is really someone else's
    still-open escalation - confirming this particular instance resolved
    itself must not close a ticket other linked incidents may still need."""
    jira = MockJiraServer()
    existing_key = await jira.create_issue(project_key="GENIE", summary="original escalation", description="d")

    signature_id = compute_error_signature_id(
        exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="payments-loader"
    )
    existing = _submitted_incident(job_run_id="run-0", jira_key=existing_key, error_signature_id=signature_id)
    session.add(existing)
    await session.commit()

    incident = _submitted_incident(job_run_id="run-0")  # same signature -> exact_match dedup
    session.add(incident)
    await session.commit()

    await submit_incident(
        session, incident, jira_client=jira, mail_client=MockMailClient(),
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
        resolution_confirmed=True,
    )

    assert incident.jira_key == existing_key
    issue = await jira.get_issue(existing_key)
    assert issue.status != "Done"
    assert any("resolved by the end user" in c for c in issue.comments)


@pytest.mark.asyncio
async def test_escalation_matching_a_fixed_precedent_gets_a_new_ticket_tagged_to_it(session):
    """If a prior occurrence of this exact issue was already confirmed
    fixed, and this one is being escalated anyway (not self-resolved), the
    earlier fix evidently didn't hold - a fresh ticket for L2 beats a
    comment on a ticket nobody's watching anymore, but it should still
    reference the old one for history."""
    jira = MockJiraServer()
    fixed_key = await jira.create_issue(project_key="GENIE", summary="original fix", description="d")
    await jira.close_issue(fixed_key, status="Done")

    signature_id = compute_error_signature_id(
        exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="payments-loader"
    )
    previously_fixed = _submitted_incident(
        job_run_id="run-0", jira_key=fixed_key, error_signature_id=signature_id,
        decision=ReviewDecision.RESOLVED, status=IncidentStatus.CLOSED,
    )
    session.add(previously_fixed)
    await session.commit()

    incident = _submitted_incident(job_run_id="run-0")  # same signature -> exact_match dedup
    session.add(incident)
    await session.commit()

    await submit_incident(
        session, incident, jira_client=jira, mail_client=MockMailClient(),
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
        resolution_confirmed=False,
    )

    assert incident.jira_key is not None
    assert incident.jira_key != fixed_key

    new_issue = await jira.get_issue(incident.jira_key)
    assert fixed_key in new_issue.description
    assert f"recurrence-of-{fixed_key}" in new_issue.labels

    old_issue = await jira.get_issue(fixed_key)
    assert old_issue.status == "Done"  # untouched


@pytest.mark.asyncio
async def test_escalation_matching_a_still_open_precedent_still_links(session):
    """The recurrence case above is specifically about a precedent someone
    already marked fixed - a precedent that's simply still open (escalated,
    not yet resolved either way) keeps the existing dedup behavior: link to
    it rather than opening a second ticket for the same ongoing issue."""
    jira = MockJiraServer()
    open_key = await jira.create_issue(project_key="GENIE", summary="still open escalation", description="d")

    signature_id = compute_error_signature_id(
        exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="payments-loader"
    )
    still_open = _submitted_incident(
        job_run_id="run-0", jira_key=open_key, error_signature_id=signature_id,
        decision=ReviewDecision.ESCALATED,
    )
    session.add(still_open)
    await session.commit()

    incident = _submitted_incident(job_run_id="run-0")
    session.add(incident)
    await session.commit()

    await submit_incident(
        session, incident, jira_client=jira, mail_client=MockMailClient(),
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
        resolution_confirmed=False,
    )

    assert incident.jira_key == open_key
    issue = await jira.get_issue(open_key)
    assert any("Linked duplicate incident" in c for c in issue.comments)


@pytest.mark.asyncio
async def test_self_resolve_matching_a_fixed_precedent_still_links(session):
    """Recurrence-detection only kicks in for an escalation matching a
    fixed precedent (the user's report contradicts "this is fixed"). Two
    self-resolutions of the same signature is not a contradiction - still
    links/comments as before."""
    jira = MockJiraServer()
    fixed_key = await jira.create_issue(project_key="GENIE", summary="original fix", description="d")
    await jira.close_issue(fixed_key, status="Done")

    signature_id = compute_error_signature_id(
        exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="payments-loader"
    )
    previously_fixed = _submitted_incident(
        job_run_id="run-0", jira_key=fixed_key, error_signature_id=signature_id,
        decision=ReviewDecision.RESOLVED, status=IncidentStatus.CLOSED,
    )
    session.add(previously_fixed)
    await session.commit()

    incident = _submitted_incident(job_run_id="run-0")
    session.add(incident)
    await session.commit()

    await submit_incident(
        session, incident, jira_client=jira, mail_client=MockMailClient(),
        dedup_window_hours=72, dedup_fuzzy_threshold=0.85, support_mailbox="support@bank.example",
        resolution_confirmed=True,
    )

    assert incident.jira_key == fixed_key
