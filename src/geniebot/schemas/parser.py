"""Log Parser Agent contract - doc section 5.2."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ParserAgentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    bot_id: str
    job_run_id: str
    environment: str
    redacted_log: str


class EvidenceLine(BaseModel):
    line_no: int
    text: str


class ParseOutput(BaseModel):
    """Strict output schema the Log Parser Agent must produce as JSON."""

    exception_type: str = ""
    exception_message: str = ""
    failing_module: str = ""
    root_frame: str = ""
    cascading_errors: list[str] = Field(default_factory=list)
    evidence_lines: list[EvidenceLine] = Field(default_factory=list)
    parse_status: Literal["parsed", "unparseable"] = "unparseable"
