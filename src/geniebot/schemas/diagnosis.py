"""Diagnostic (L1 Triage) Agent contract - doc section 5.3."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from geniebot.schemas.kb import RetrievedChunk
from geniebot.schemas.parser import ParseOutput


class DiagnosticAgentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    error_category: str | None
    parse_output: ParseOutput
    kb_chunks: list[RetrievedChunk]


class Citation(BaseModel):
    type: Literal["log", "kb"]
    ref: str


class DiagnosisOutput(BaseModel):
    root_cause: str = ""
    proposed_resolution: str = ""
    resolution_type: Literal["guidance", "controlled_rerun", "escalate"] = "escalate"
    rerun_parameters: dict = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0
    insufficient_information: bool = True
