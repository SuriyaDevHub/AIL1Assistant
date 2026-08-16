from datetime import UTC, datetime

import pytest

from geniebot.db.models import Environment
from geniebot.ingestion.incident_factory import create_incident_if_failure, is_failure_log
from geniebot.ingestion.s3_listener import StorageEvent


def test_is_failure_log_detects_exception_markers():
    assert is_failure_log("ConnectionError: could not connect")
    assert is_failure_log("Traceback (most recent call last):")
    assert is_failure_log("job FAILED with exit code 1")


def test_is_failure_log_false_for_clean_run():
    assert not is_failure_log("INFO batch completed successfully in 42s")


def _event(key: str) -> StorageEvent:
    return StorageEvent(bucket="genie-bot-logs", key=key, size=100, event_time=datetime.now(UTC))


@pytest.mark.asyncio
async def test_create_incident_if_failure_returns_none_for_success_log(session):
    event = _event("working/payments-loader/run-1/execution.log")
    incident = await create_incident_if_failure(session, event, "INFO all good", working_prefix="working/")
    assert incident is None


@pytest.mark.asyncio
async def test_create_incident_if_failure_parses_bot_and_job_from_path(session):
    event = _event("working/payments-loader/run-1/execution.log")
    raw = "INFO starting\nConnectionError: could not connect to host db01\n"
    incident = await create_incident_if_failure(session, event, raw, working_prefix="working/")
    assert incident is not None
    assert incident.bot_id == "payments-loader"
    assert incident.job_run_id == "run-1"
    assert incident.log_s3_uri == "s3://genie-bot-logs/working/payments-loader/run-1/execution.log"
    assert incident.environment == Environment.PRODUCTION


@pytest.mark.asyncio
async def test_create_incident_if_failure_prefers_header_over_path(session):
    event = _event("working/some-folder/some-run/execution.log")
    raw = "# bot_id: real-bot\n# job_run_id: real-run\n# user_id: alice\n# environment: uat\nConnectionError: x\n"
    incident = await create_incident_if_failure(session, event, raw, working_prefix="working/")
    assert incident.bot_id == "real-bot"
    assert incident.job_run_id == "real-run"
    assert incident.user_id == "alice"
    assert incident.environment == Environment.UAT


@pytest.mark.asyncio
async def test_create_incident_if_failure_defaults_unknown_environment_to_production(session):
    event = _event("working/b/r/execution.log")
    raw = "# environment: not_a_real_env\nConnectionError: x\n"
    incident = await create_incident_if_failure(session, event, raw, working_prefix="working/")
    assert incident.environment == Environment.PRODUCTION
