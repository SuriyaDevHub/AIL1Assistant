"""doc 9 "Golden set" row: labelled historical incidents with known-correct
root causes; measures diagnosis accuracy and confidence calibration.
Against MockLLMClient this is a deterministic regression check (accuracy
should stay at 100%); against a real backend (scripts/evaluate_golden_set.py)
the same fixture set measures actual model accuracy, per doc 9.1's
"Diagnosis accuracy against the golden set at or above the agreed
threshold" acceptance metric.
"""
import json
from pathlib import Path

import pytest
import yaml

from geniebot.agents.log_parser_agent import LogParserAgent
from geniebot.schemas.parser import ParserAgentInput
from geniebot.settings import CONFIG_DIR
from geniebot.taxonomy_matching import match_error_category

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "golden_incidents.json").read_text())


def _load_prompt(name: str) -> dict:
    with (CONFIG_DIR / "prompts" / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _taxonomy() -> dict:
    with (CONFIG_DIR / "taxonomy.yaml").open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.asyncio
async def test_golden_set_diagnosis_accuracy(llm_client):
    agent = LogParserAgent(llm_client, _load_prompt("log_parser.v1.yaml"))
    taxonomy = _taxonomy()

    correct = 0
    for case in FIXTURES:
        output, _ = await agent.run(
            ParserAgentInput(
                incident_id=case["case_id"],
                bot_id=case["bot_id"],
                job_run_id=case["job_run_id"],
                environment=case["environment"],
                redacted_log=case["log_text"],
            )
        )
        if not case["expect_parseable"]:
            if output.parse_status == "unparseable":
                correct += 1
            continue

        if output.parse_status != "parsed":
            continue
        if output.exception_type != case["expected_exception_type"]:
            continue
        category = match_error_category(f"{output.exception_type} {output.exception_message}", taxonomy)
        if category != case["expected_error_category"]:
            continue
        correct += 1

    accuracy = correct / len(FIXTURES)
    assert accuracy == pytest.approx(1.0), f"golden set accuracy {accuracy:.2%}, expected 100% against mock backend"
