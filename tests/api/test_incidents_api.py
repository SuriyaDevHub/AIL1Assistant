from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from geniebot.api.deps import create_dev_token
from geniebot.db.models import Environment, Incident, ReviewDecision
from geniebot.integrations.dedup import compute_error_signature_id
from geniebot.integrations.factory import get_jira_client
from geniebot.kb.factory import get_vector_store
from geniebot.state_machine import IncidentStatus

# Matches _seed_awaiting_review's parse_output (bot_id/exception_type/failing_module)
# - these fixtures bypass the pipeline entirely, so error_signature_id has to
# be set explicitly on any row a /precedent test wants matched against it.
_SIGNATURE = compute_error_signature_id(
    exception_type="ConnectionError", failing_module="m.py", bot_id="payments-loader"
)


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


async def _seed_self_heal_candidate(session) -> Incident:
    """An AUTO_RESOLVE_CANDIDATE that reached AWAITING_REVIEW with no
    template - doc step 8 only generates one "where escalation is
    required", so a self-heal candidate has none until/unless the end user
    reports the L1 fix didn't work after all."""
    incident = Incident(
        bot_id="payments-loader", job_run_id="run-2", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k2", status=IncidentStatus.AWAITING_REVIEW,
        parse_output={"exception_type": "ConnectionError", "failing_module": "m.py", "evidence_lines": [{"line_no": 1, "text": "x"}]},
        diagnosis={"root_cause": "x", "proposed_resolution": "y", "resolution_type": "controlled_rerun",
                   "citations": [{"type": "log", "ref": "1"}], "confidence": 0.9, "insufficient_information": False,
                   "rerun_parameters": {}},
        template_payload=None,
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
    # override the client fixture's default admin token with no credentials
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "r1", "decision": "approve"},
        headers={"Authorization": ""},
    )
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
async def test_incident_stats_counts_by_status_and_confidence(session, client):
    a = await _seed_awaiting_review(session)  # confidence 0.9 -> "0.8-1.0" bucket
    b = await _seed_self_heal_candidate(session)  # confidence 0.9 -> "0.8-1.0" bucket, AWAITING_REVIEW
    b.status = IncidentStatus.CLOSED
    b.decision = None
    session.add(b)
    await session.commit()

    r = await client.get("/incidents/stats")
    assert r.status_code == 200
    body = r.json()

    assert body["total_incidents"] == 2
    assert body["status_counts"]["AWAITING_REVIEW"] == 1
    assert body["status_counts"]["CLOSED"] == 1
    assert body["open_count"] == 1  # AWAITING_REVIEW is open, CLOSED isn't
    assert body["confidence_buckets"]["0.8-1.0"] == 2
    assert body["avg_confidence"] == pytest.approx(0.9)
    assert len(body["recent"]) == 2
    assert {i["incident_id"] for i in body["recent"]} == {a.incident_id, b.incident_id}


@pytest.mark.asyncio
async def test_incident_jira_ticket_404_without_jira_key(session, client):
    incident = await _seed_awaiting_review(session)
    r = await client.get(f"/incidents/{incident.incident_id}/jira")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_incident_jira_ticket_returns_mock_ticket_content(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("reviewer1")
    review = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "reviewer1", "decision": "resolved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert review.status_code == 200
    jira_key = (await client.get(f"/incidents/{incident.incident_id}")).json()["jira_key"]
    assert jira_key

    r = await client.get(f"/incidents/{incident.incident_id}/jira")
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == jira_key
    assert body["status"] == "Done"  # resolved decision closes the ticket immediately
    assert "x" in body["description"]  # root_cause "x" from _seed_awaiting_review's diagnosis


@pytest.mark.asyncio
async def test_review_resolved_closes_jira_ticket_immediately(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("alice")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "alice", "decision": "resolved", "comment": "restarted the job, worked fine"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "CLOSED"

    detail = (await client.get(f"/incidents/{incident.incident_id}")).json()
    assert detail["decision"] == "resolved"
    assert detail["jira_key"] is not None

    issue = await get_jira_client().get_issue(detail["jira_key"])
    assert issue.status == "Done"


@pytest.mark.asyncio
async def test_review_escalated_generates_missing_template_and_leaves_jira_open(session, client):
    incident = await _seed_self_heal_candidate(session)
    assert incident.template_payload is None

    token = create_dev_token("bob")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "bob", "decision": "escalated", "comment": "tried the suggested rerun, still fails"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    # Escalated incidents stay open (SUBMITTED) until the Jira ticket
    # itself actually closes - see test_simulate_jira_closure_* below for
    # that transition.
    assert r.json()["status"] == "SUBMITTED"

    detail = (await client.get(f"/incidents/{incident.incident_id}")).json()
    assert detail["decision"] == "escalated"
    assert detail["template_payload"] is not None
    assert detail["jira_key"] is not None

    issue = await get_jira_client().get_issue(detail["jira_key"])
    assert issue.status != "Done"


@pytest.mark.asyncio
async def test_simulate_jira_closure_closes_submitted_incident_and_captures_resolution(session, client):
    # get_vector_store(session) resolves to the same process-wide
    # InMemoryVectorStore singleton the API route uses internally (unlike
    # the `vector_store` pytest fixture, which is its own separate
    # instance) - give it an active index so capture_resolution's
    # no-active-index guard doesn't short-circuit the capture. Reset it in
    # `finally` - it's a process-wide singleton that outlives this test, and
    # other tests (e.g. test_kb_api.py's no-active-index-yet case) depend on
    # it starting unset.
    store = get_vector_store(session)
    await store.set_active_index_version("v1")
    try:
        incident = await _seed_awaiting_review(session)
        token = create_dev_token("bob")
        review = await client.post(
            f"/incidents/{incident.incident_id}/review",
            json={"reviewer_id": "bob", "decision": "escalated", "comment": "tried the suggested rerun, still fails"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert review.json()["status"] == "SUBMITTED"
        jira_key = (await client.get(f"/incidents/{incident.incident_id}")).json()["jira_key"]

        r = await client.post(f"/incidents/{incident.incident_id}/simulate-jira-closure")
        assert r.status_code == 200
        assert incident.incident_id in r.json()["closed_incident_ids"]
        assert r.json()["capture"]["captured"] is True

        detail = (await client.get(f"/incidents/{incident.incident_id}")).json()
        assert detail["status"] == "CLOSED"
    finally:
        await store.set_active_index_version(None)

    issue = await get_jira_client().get_issue(jira_key)
    assert issue.status == "Closed"


@pytest.mark.asyncio
async def test_simulate_jira_closure_404_without_jira_key(session, client):
    incident = await _seed_awaiting_review(session)
    r = await client.post(f"/incidents/{incident.incident_id}/simulate-jira-closure")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_incident_timeline_returns_ordered_transitions(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("bob")
    await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "bob", "decision": "resolved"},
        headers={"Authorization": f"Bearer {token}"},
    )

    r = await client.get(f"/incidents/{incident.incident_id}/timeline")
    assert r.status_code == 200
    statuses = [entry["status"] for entry in r.json()]
    # _seed_awaiting_review inserts the row directly at AWAITING_REVIEW (no
    # audit trail for the earlier pipeline steps) - only transitions after
    # that are expected here.
    assert statuses == ["SUBMITTED", "CLOSED"]


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


@pytest.mark.asyncio
async def test_precedent_returns_nulls_before_diagnosis(session, client):
    incident = Incident(
        bot_id="payments-loader", job_run_id="run-99", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k99", status=IncidentStatus.SCREENED,
    )
    session.add(incident)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}/precedent")
    assert r.status_code == 200
    assert r.json() == {"resolved_precedent": None, "open_precedent": None}


@pytest.mark.asyncio
async def test_precedent_ignores_different_signature(session, client):
    incident = await _seed_awaiting_review(session)
    incident.error_signature_id = _SIGNATURE
    await session.commit()

    other = Incident(
        bot_id="other-bot", job_run_id="run-x", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/kx", status=IncidentStatus.CLOSED,
        error_signature_id="different-signature", decision=ReviewDecision.RESOLVED,
    )
    session.add(other)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}/precedent")
    assert r.status_code == 200
    assert r.json() == {"resolved_precedent": None, "open_precedent": None}


@pytest.mark.asyncio
async def test_precedent_returns_resolved_precedent_for_matching_signature(session, client):
    incident = await _seed_awaiting_review(session)
    incident.error_signature_id = _SIGNATURE
    await session.commit()

    prior = Incident(
        bot_id="payments-loader", job_run_id="run-prior", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/kprior", status=IncidentStatus.CLOSED,
        error_signature_id=_SIGNATURE, decision=ReviewDecision.RESOLVED,
        reviewer_id="carol", reviewer_comment="cleared the stale lock and reran",
        diagnosis={
            "root_cause": "x", "proposed_resolution": "clear the lock", "resolution_type": "controlled_rerun",
            "citations": [], "confidence": 0.9, "insufficient_information": False,
        },
        jira_key="GENIE-1",
    )
    session.add(prior)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}/precedent")
    assert r.status_code == 200
    body = r.json()
    assert body["open_precedent"] is None
    resolved = body["resolved_precedent"]
    assert resolved["incident_id"] == prior.incident_id
    assert resolved["reviewer_id"] == "carol"
    assert resolved["resolution_summary"] == "clear the lock"
    assert resolved["within_dedup_window"] is True


@pytest.mark.asyncio
async def test_precedent_returns_open_precedent_with_live_jira_status(session, client):
    incident = await _seed_awaiting_review(session)
    incident.error_signature_id = _SIGNATURE
    await session.commit()

    jira = get_jira_client()
    key = await jira.create_issue(project_key="GENIE", summary="prior escalation", description="d")

    prior = Incident(
        bot_id="payments-loader", job_run_id="run-open", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/kopen", status=IncidentStatus.SUBMITTED,
        error_signature_id=_SIGNATURE, decision=ReviewDecision.ESCALATED, jira_key=key,
        diagnosis={
            "root_cause": "x", "proposed_resolution": "escalate", "resolution_type": "escalate",
            "citations": [], "confidence": 0.4, "insufficient_information": False,
        },
    )
    session.add(prior)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}/precedent")
    assert r.status_code == 200
    body = r.json()
    assert body["resolved_precedent"] is None
    open_precedent = body["open_precedent"]
    assert open_precedent["incident_id"] == prior.incident_id
    assert open_precedent["jira_key"] == key
    assert open_precedent["jira_status"] == "Open"
    assert open_precedent["within_dedup_window"] is True


@pytest.mark.asyncio
async def test_precedent_ignores_precedent_outside_dedup_window(session, client):
    incident = await _seed_awaiting_review(session)
    incident.error_signature_id = _SIGNATURE
    await session.commit()

    jira = get_jira_client()
    key = await jira.create_issue(project_key="GENIE", summary="stale escalation", description="d")

    stale = Incident(
        bot_id="payments-loader", job_run_id="run-stale", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/kstale", status=IncidentStatus.SUBMITTED,
        error_signature_id=_SIGNATURE, decision=ReviewDecision.ESCALATED, jira_key=key,
        ingested_at=datetime.now(UTC) - timedelta(hours=200),  # configured window is 72h
    )
    session.add(stale)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}/precedent")
    assert r.status_code == 200
    assert r.json()["open_precedent"] is None


@pytest.mark.asyncio
async def test_precedent_404_unknown_incident(client):
    r = await client.get("/incidents/does-not-exist/precedent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_generic_l1_checklist_null_when_resolution_type_not_escalate(session, client):
    incident = await _seed_awaiting_review(session)  # resolution_type: controlled_rerun
    r = await client.get(f"/incidents/{incident.incident_id}")
    assert r.json()["generic_l1_checklist"] is None


@pytest.mark.asyncio
async def test_generic_l1_checklist_null_when_escalate_but_information_sufficient(session, client):
    incident = Incident(
        bot_id="payments-loader", job_run_id="run-esc-1", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.AWAITING_REVIEW,
        diagnosis={"root_cause": "x", "proposed_resolution": "y", "resolution_type": "escalate",
                   "citations": [], "confidence": 0.5, "insufficient_information": False},
        template_payload={"incident_id": "p", "bot_id": "payments-loader", "job_run_id": "run-esc-1",
                           "environment": "production", "error_category": "CONNECTIVITY", "summary": "s",
                           "root_cause": "x", "recommended_action": "y", "evidence_refs": [], "unknown_fields": []},
    )
    session.add(incident)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}")
    assert r.json()["generic_l1_checklist"] is None


@pytest.mark.asyncio
async def test_generic_l1_checklist_uses_category_steps_when_no_precedent(session, client):
    incident = Incident(
        bot_id="payments-loader", job_run_id="run-esc-2", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.AWAITING_REVIEW,
        diagnosis={"root_cause": "", "proposed_resolution": "escalate", "resolution_type": "escalate",
                   "citations": [], "confidence": 0.2, "insufficient_information": True},
        template_payload={"incident_id": "p", "bot_id": "payments-loader", "job_run_id": "run-esc-2",
                           "environment": "production", "error_category": "AUTH_EXPIRED", "summary": "s",
                           "root_cause": "x", "recommended_action": "y", "evidence_refs": [], "unknown_fields": []},
    )
    session.add(incident)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}")
    checklist = r.json()["generic_l1_checklist"]
    assert checklist is not None
    assert any("token" in step.lower() for step in checklist)


@pytest.mark.asyncio
async def test_generic_l1_checklist_falls_back_to_default_without_category(session, client):
    incident = Incident(
        bot_id="payments-loader", job_run_id="run-esc-3", user_id="alice",
        environment=Environment.PRODUCTION, log_s3_uri="s3://b/k", status=IncidentStatus.AWAITING_REVIEW,
        diagnosis={"root_cause": "", "proposed_resolution": "escalate", "resolution_type": "escalate",
                   "citations": [], "confidence": 0.2, "insufficient_information": True},
        template_payload=None,
    )
    session.add(incident)
    await session.commit()

    r = await client.get(f"/incidents/{incident.incident_id}")
    checklist = r.json()["generic_l1_checklist"]
    assert checklist is not None
    assert any("retry" in step.lower() for step in checklist)


# --- Enhancement 3: scope what each role can see -----------------------


async def _seed_owned_incident(session, *, user_id: str, job_run_id: str) -> Incident:
    incident = Incident(
        bot_id="payments-loader", job_run_id=job_run_id, user_id=user_id,
        environment=Environment.PRODUCTION, log_s3_uri=f"s3://b/{job_run_id}", status=IncidentStatus.AWAITING_REVIEW,
        diagnosis={"root_cause": "x", "proposed_resolution": "y", "resolution_type": "escalate",
                   "citations": [], "confidence": 0.5, "insufficient_information": False},
    )
    session.add(incident)
    await session.commit()
    return incident


@pytest.mark.asyncio
async def test_end_user_list_only_sees_own_incidents(session, client):
    alice_incident = await _seed_owned_incident(session, user_id="alice", job_run_id="run-alice")
    await _seed_owned_incident(session, user_id="bob", job_run_id="run-bob")

    token = create_dev_token("alice", role="end_user")
    r = await client.get("/incidents", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    ids = {i["incident_id"] for i in r.json()}
    assert ids == {alice_incident.incident_id}


@pytest.mark.asyncio
async def test_l2_support_list_sees_every_incident(session, client):
    await _seed_owned_incident(session, user_id="alice", job_run_id="run-alice2")
    await _seed_owned_incident(session, user_id="bob", job_run_id="run-bob2")

    token = create_dev_token("carol", role="l2_support")
    r = await client.get("/incidents", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_end_user_cannot_view_others_incident_detail(session, client):
    incident = await _seed_owned_incident(session, user_id="alice", job_run_id="run-alice3")

    token = create_dev_token("bob", role="end_user")
    r = await client.get(f"/incidents/{incident.incident_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_end_user_can_view_own_incident_detail(session, client):
    incident = await _seed_owned_incident(session, user_id="alice", job_run_id="run-alice4")

    token = create_dev_token("alice", role="end_user")
    r = await client.get(f"/incidents/{incident.incident_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_end_user_cannot_view_others_timeline_or_precedent(session, client):
    incident = await _seed_owned_incident(session, user_id="alice", job_run_id="run-alice5")
    token = create_dev_token("bob", role="end_user")
    headers = {"Authorization": f"Bearer {token}"}

    r1 = await client.get(f"/incidents/{incident.incident_id}/timeline", headers=headers)
    assert r1.status_code == 403

    r2 = await client.get(f"/incidents/{incident.incident_id}/precedent", headers=headers)
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_end_user_cannot_view_dashboard_stats(session, client):
    token = create_dev_token("alice", role="end_user")
    r = await client.get("/incidents/stats", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_l2_support_can_view_dashboard_stats(session, client):
    token = create_dev_token("carol", role="l2_support")
    r = await client.get("/incidents/stats", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_incident_endpoints_require_auth(session, client):
    incident = await _seed_owned_incident(session, user_id="alice", job_run_id="run-alice6")
    r = await client.get(f"/incidents/{incident.incident_id}", headers={"Authorization": ""})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_without_role_claim_defaults_to_end_user(session, client):
    """A real OpenAM token (or any token minted before this claim existed)
    won't carry our custom "role" claim - get_current_principal must
    default to least privilege, not crash or grant full access."""
    import jwt as pyjwt

    from geniebot.settings import get_settings

    incident = await _seed_owned_incident(session, user_id="alice", job_run_id="run-alice7")
    settings = get_settings()
    legacy_token = pyjwt.encode({"sub": "alice"}, settings.api_dev_shared_secret, algorithm="HS256")

    own = await client.get(
        f"/incidents/{incident.incident_id}", headers={"Authorization": f"Bearer {legacy_token}"}
    )
    assert own.status_code == 200  # alice viewing her own incident still works

    r = await client.get("/incidents/stats", headers={"Authorization": f"Bearer {legacy_token}"})
    assert r.status_code == 403  # but treated as end_user, not admin


# --- Enhancement 4: reduce reflexive escalation -------------------------


@pytest.mark.asyncio
async def test_escalate_requires_comment(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("bob")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "bob", "decision": "escalated"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_escalate_requires_comment_rejects_whitespace_only(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("bob")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "bob", "decision": "escalated", "comment": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_escalate_with_short_comment_flags_low_context_stat(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("bob")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={"reviewer_id": "bob", "decision": "escalated", "comment": "nope"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    stats = await client.get("/incidents/stats")
    assert stats.json()["low_context_escalations_last_24h"] == 1


@pytest.mark.asyncio
async def test_escalate_with_substantial_comment_not_flagged(session, client):
    incident = await _seed_awaiting_review(session)
    token = create_dev_token("bob")
    r = await client.post(
        f"/incidents/{incident.incident_id}/review",
        json={
            "reviewer_id": "bob",
            "decision": "escalated",
            "comment": "Tried the suggested rerun twice, connection still refused after token refresh.",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    stats = await client.get("/incidents/stats")
    assert stats.json()["low_context_escalations_last_24h"] == 0
