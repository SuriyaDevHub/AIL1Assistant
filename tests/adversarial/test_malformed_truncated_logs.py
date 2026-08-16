"""doc 9 "Adversarial" row: malformed and truncated logs."""
import pytest
import yaml

from geniebot.agents.log_parser_agent import LogParserAgent
from geniebot.schemas.parser import ParserAgentInput
from geniebot.settings import CONFIG_DIR


def _load_prompt(name: str) -> dict:
    with (CONFIG_DIR / "prompts" / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.asyncio
async def test_binary_garbage_returns_unparseable(llm_client):
    agent = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    output, _ = await agent.run(
        ParserAgentInput(incident_id="i1", bot_id="b1", job_run_id="r1", environment="production", redacted_log="\x00\x01\x02 garbled noise \xff\xfe")
    )
    assert output.parse_status == "unparseable"


@pytest.mark.asyncio
async def test_empty_log_returns_unparseable(llm_client):
    agent = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    output, _ = await agent.run(
        ParserAgentInput(incident_id="i1", bot_id="b1", job_run_id="r1", environment="production", redacted_log="")
    )
    assert output.parse_status == "unparseable"


@pytest.mark.asyncio
async def test_truncated_mid_exception_still_parses_available_signal(llm_client):
    agent = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    output, _ = await agent.run(
        ParserAgentInput(
            incident_id="i1", bot_id="b1", job_run_id="r1", environment="production",
            redacted_log="INFO starting\nConnectionError: could not conn",
        )
    )
    # Truncation mid-line still contains a recognisable exception token
    assert output.parse_status == "parsed"
    assert output.exception_type == "ConnectionError"
