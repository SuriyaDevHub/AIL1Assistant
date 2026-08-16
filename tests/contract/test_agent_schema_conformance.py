"""doc 9 "Contract" row: agent output schema conformance."""
import os
from datetime import UTC, datetime

import pytest
import yaml

from geniebot.agents.diagnostic_agent import DiagnosticAgent
from geniebot.agents.log_parser_agent import LogParserAgent
from geniebot.agents.template_generator_agent import TemplateGeneratorAgent
from geniebot.schemas.diagnosis import DiagnosticAgentInput
from geniebot.schemas.kb import RetrievedChunk
from geniebot.schemas.parser import ParserAgentInput
from geniebot.schemas.template import TemplateAgentInput, validate_against_template_schema
from geniebot.settings import CONFIG_DIR, get_settings


def _load_prompt(name: str) -> dict:
    with (CONFIG_DIR / "prompts" / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


SAMPLE_LOG = (
    "INFO starting batch job\n"
    "ConnectionError: could not connect to host db01 in module payments/loader.py\n"
)


@pytest.mark.asyncio
async def test_log_parser_agent_output_is_schema_valid(llm_client):
    agent = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    output, records = await agent.run(
        ParserAgentInput(incident_id="i1", bot_id="b1", job_run_id="r1", environment="production", redacted_log=SAMPLE_LOG)
    )
    assert output.parse_status in ("parsed", "unparseable")
    assert records[-1].success
    assert records[-1].agent_name == "log_parser"


@pytest.mark.asyncio
async def test_diagnostic_agent_output_is_schema_valid(llm_client):
    parser = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    parse_output, _ = await parser.run(
        ParserAgentInput(incident_id="i1", bot_id="b1", job_run_id="r1", environment="production", redacted_log=SAMPLE_LOG)
    )
    chunk = RetrievedChunk(
        chunk_id="c1", content="restart after connectivity blip", source_ref="SOP-1",
        error_category="CONNECTIVITY", similarity=0.9, effective_from=datetime.now(UTC),
    )
    diag_agent = DiagnosticAgent(llm_client, _load_prompt("diagnostic.v1.yaml"))
    diagnosis, records = await diag_agent.run(
        DiagnosticAgentInput(incident_id="i1", error_category="CONNECTIVITY", parse_output=parse_output, kb_chunks=[chunk])
    )
    assert 0.0 <= diagnosis.confidence <= 1.0
    assert diagnosis.resolution_type in ("guidance", "controlled_rerun", "escalate")
    assert records[-1].success


@pytest.mark.asyncio
async def test_template_generator_output_matches_json_schema(llm_client):
    parser = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    parse_output, _ = await parser.run(
        ParserAgentInput(incident_id="i1", bot_id="b1", job_run_id="r1", environment="production", redacted_log=SAMPLE_LOG)
    )
    chunk = RetrievedChunk(
        chunk_id="c1", content="restart after connectivity blip", source_ref="SOP-1",
        error_category="CONNECTIVITY", similarity=0.9, effective_from=datetime.now(UTC),
    )
    diag_agent = DiagnosticAgent(llm_client, _load_prompt("diagnostic.v1.yaml"))
    diagnosis, _ = await diag_agent.run(
        DiagnosticAgentInput(incident_id="i1", error_category="CONNECTIVITY", parse_output=parse_output, kb_chunks=[chunk])
    )
    tmpl_agent = TemplateGeneratorAgent(llm_client, _load_prompt("template_generator.v1.yaml"))
    template, records = await tmpl_agent.run(
        TemplateAgentInput(
            incident_id="i1", bot_id="b1", job_run_id="r1", environment="production",
            log_s3_uri="s3://bucket/key", parse_output=parse_output, diagnosis=diagnosis,
            template_precedents=[chunk],
        )
    )
    validate_against_template_schema(template.model_dump(mode="json"))
    assert records[-1].success


@pytest.mark.asyncio
async def test_log_parser_returns_unparseable_for_garbage_input(llm_client):
    agent = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    output, _ = await agent.run(
        ParserAgentInput(incident_id="i1", bot_id="b1", job_run_id="r1", environment="production", redacted_log="asdf qwer zxcv no signal here")
    )
    assert output.parse_status == "unparseable"


@pytest.mark.asyncio
async def test_generation_model_setting_overrides_prompts_own_pinned_model(llm_client):
    """settings.generation_model (GENERATION_MODEL), when set, overrides
    every agent's individually-pinned model in one place - e.g. to point
    the whole pipeline at an internal AI platform's model without editing
    every prompt file (agents/base.py)."""
    original = os.environ.get("GENERATION_MODEL")
    os.environ["GENERATION_MODEL"] = "internal-platform-model-x"
    get_settings.cache_clear()
    try:
        agent = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
        _, records = await agent.run(
            ParserAgentInput(incident_id="i1", bot_id="b1", job_run_id="r1", environment="production", redacted_log=SAMPLE_LOG)
        )
        assert records[-1].model == "internal-platform-model-x"
    finally:
        if original is None:
            os.environ.pop("GENERATION_MODEL", None)
        else:
            os.environ["GENERATION_MODEL"] = original
        get_settings.cache_clear()
