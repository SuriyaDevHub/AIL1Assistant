from decimal import Decimal

import pytest

from geniebot.api.deps import create_dev_token
from geniebot.db.models import Environment, Incident
from geniebot.state_machine import IncidentStatus


async def _seed_awaiting_review(session) -> Incident:
    incident = Incident(
        bot_id="payments-loader", job_run_id="run-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k1", status=IncidentStatus.AWAITING_REVIEW,
        parse_output={"exception_type": "ConnectionError", "failing_module": "m.py", "evidence_lines": [{"line_no": 1, "text": "x"}]},
        diagnosis={"root_cause": "x", "proposed_resolution": "y", "resolution_type": "controlled_rerun",
                   "citations": [{"type": "log", "ref": "1"}], "confidence": 0.9, "insufficient_information": False},
        template_payload={"incident_id": "p", "bot_id": "payments-loader", "job_run_id": "run-1",
                           "environment": "production", "error_category": "CONNECTIVITY", "summary": "s",
                           "root_cause": "x", "recommended_action": "y", "evidence_refs": [], "unknown_fields": []},
        token_cost_usd=Decimal("0.02"),
    )
    session.add(incident)
    await session.commit()
    return incident


@pytest.mark.asyncio
async def test_list_incidents_filters_by_status(session, client):
    await _seed_awaiting_review(session)
    r = await client.get("/incidents", params={"status": "AWAITING_REVIEW"})
    assert r.status_code == 200
    assert len(r.json()) == 1

    r2 = await client.get("/incidents", params={"status": "CLOSED"})
    assert r2.json() == []


@pytest.mark.asyncio
async def test_get_incident_detail(session, client):
    incident = await _seed_awaiting_review(session)
    r = await client.get(f"/incidents/{incident.incident_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["diagnosis"]["confidence"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_get_incident_404(client):
    r = await client.get("/incidents/does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_review_requires_auth(session, client):
    incident = await _seed_awaiting_review(session)
    r = await client.post(f"/incidents/{incident.incident_id}/review", json={"reviewer_id": "r1", "decision": "approve"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_review_approve_closes_incident_and_creates_jira(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("reviewer1")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "reviewer1", "decision": "approve"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "CLOSED"

    detail = (await client.get(f"/incidents/{incident.incident_id}")).json()
    assert detail["jira_key"] is not None
    assert detail["decision"] == "approve"


@pytest.mark.asyncio
async def test_review_reject_closes_incident_without_jira(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("reviewer1")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "reviewer1", "decision": "reject", "comment": "not our bug"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "CLOSED"

    detail = (await client.get(f"/incidents/{incident.incident_id}")).json()
    assert detail["jira_key"] is None
    assert detail["decision"] == "reject"


@pytest.mark.asyncio
async def test_review_rejects_double_submission(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("reviewer1")
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.post(
        f"/incidents/{incident.incident_id}/review", json={"reviewer_id": "reviewer1", "decision": "approve"}, headers=headers
    )
    assert first.status_code == 200

    second = await client.post(
        f"/incidents/{incident.incident_id}/review", json={"reviewer_id": "reviewer1", "decision": "approve"}, headers=headers
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_update_template_persists_reviewer_edits(session, client):
    incident = await _seed_awaiting_review(session)
    payload = {
        "reviewer_id": "reviewer1",
        "template_payload": {
            "incident_id": incident.incident_id, "bot_id": "payments-loader", "job_run_id": "run-1",
            "environment": "production", "error_category": "CONNECTIVITY", "summary": "edited summary",
            "root_cause": "x", "recommended_action": "edited action", "evidence_refs": [], "unknown_fields": [],
        },
    }
    r = await client.put(f"/incidents/{incident.incident_id}/template", json=payload)
    assert r.status_code == 200

    detail = (await client.get(f"/incidents/{incident.incident_id}")).json()
    assert detail["template_payload"]["summary"] == "edited summary"
