"""Template Generator Agent contract - doc section 5.5, matches
config/template_schema.json exactly (kept in sync by
tests/contract/test_template_schema_sync.py)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field

from geniebot.schemas.diagnosis import DiagnosisOutput
from geniebot.schemas.kb import RetrievedChunk
from geniebot.schemas.parser import ParseOutput


class TemplateAgentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    bot_id: str
    job_run_id: str
    environment: str
    log_s3_uri: str
    parse_output: ParseOutput
    diagnosis: DiagnosisOutput
    template_precedents: list[RetrievedChunk]


class EvidenceRef(BaseModel):
    type: Literal["log", "kb"]
    ref: str


class TemplatePayload(BaseModel):
    incident_id: str
    bot_id: str
    job_run_id: str
    environment: Literal["production", "uat", "development"]
    error_category: str
    summary: str = Field(max_length=500)
    root_cause: str
    recommended_action: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    log_s3_uri: str | None = None
    priority: Literal["P1", "P2", "P3", "P4"] | None = None
    unknown_fields: list[str] = Field(default_factory=list)


_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "config" / "template_schema.json"


@lru_cache
def _load_template_schema() -> dict:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_against_template_schema(payload: dict) -> None:
    """Doc 5.5: "Output is validated against the approved template schema
    before it reaches the reviewer." Raises jsonschema.ValidationError on
    failure - the caller (orchestration/pipeline.py) treats that as a
    schema-validation failure subject to the same bounded-retry-then-fail-
    closed handling as the agent's own pydantic validation (doc 5.1)."""
    jsonschema.validate(payload, _load_template_schema())
