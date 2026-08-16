#!/usr/bin/env python
"""Runs the golden set (doc 9 "Golden set" row) against the configured LLM
backend and prints diagnosis accuracy - doc 9.1's "Diagnosis accuracy
against the golden set at or above the agreed threshold" acceptance
metric. Point this at LLM_BACKEND=internal_platform to measure the real
model; the equivalent pytest test (tests/golden_set/test_golden_set.py)
always runs against MockLLMClient as a fast regression check.

Usage:
    python scripts/evaluate_golden_set.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml

from geniebot.agents.base import AgentSchemaValidationError
from geniebot.agents.log_parser_agent import LogParserAgent
from geniebot.llm.client import LLMPlatformError
from geniebot.llm.factory import get_llm_client
from geniebot.schemas.parser import ParserAgentInput
from geniebot.settings import CONFIG_DIR, get_taxonomy
from geniebot.taxonomy_matching import match_error_category

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden_set" / "fixtures" / "golden_incidents.json"


async def main() -> None:
    fixtures = json.loads(FIXTURES_PATH.read_text())
    with (CONFIG_DIR / "prompts" / "log_parser.v1.yaml").open("r", encoding="utf-8") as fh:
        prompt_config = yaml.safe_load(fh)

    agent = LogParserAgent(get_llm_client(), prompt_config)
    taxonomy = get_taxonomy()

    correct = 0
    for case in fixtures:
        try:
            output, _ = await agent.run(
                ParserAgentInput(
                    incident_id=case["case_id"], bot_id=case["bot_id"], job_run_id=case["job_run_id"],
                    environment=case["environment"], redacted_log=case["log_text"],
                )
            )
        except (AgentSchemaValidationError, LLMPlatformError) as exc:
            print(f"{case['case_id']}: FAIL (agent error: {exc})")
            continue

        ok = False
        if not case["expect_parseable"]:
            ok = output.parse_status == "unparseable"
        elif output.parse_status == "parsed" and output.exception_type == case["expected_exception_type"]:
            category = match_error_category(f"{output.exception_type} {output.exception_message}", taxonomy)
            ok = category == case["expected_error_category"]

        correct += int(ok)
        print(f"{case['case_id']}: {'PASS' if ok else 'FAIL'} (parse_status={output.parse_status}, exception_type={output.exception_type!r})")

    accuracy = correct / len(fixtures) if fixtures else 0.0
    print(f"\nGolden set accuracy: {accuracy:.1%} ({correct}/{len(fixtures)})")


if __name__ == "__main__":
    asyncio.run(main())
