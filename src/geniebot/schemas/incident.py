"""API-facing incident contracts - doc section 7.1."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from geniebot.schemas.diagnosis import DiagnosisOutput
from geniebot.schemas.parser import ParseOutput
from geniebot.schemas.template import TemplatePayload


class IncidentListItem(BaseModel):
    incident_id: str
    bot_id: str
    job_run_id: str
    environment: str
    status: str
    error_signature_id: str | None
    jira_key: str | None
    ingested_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IncidentDetail(BaseModel):
    incident_id: str
    bot_id: str
    job_run_id: str
    user_id: str
    environment: str
    log_s3_uri: str
    status: str
    error_signature_id: str | None
    parse_output: ParseOutput | None
    diagnosis: DiagnosisOutput | None
    template_payload: dict | None
    reviewer_id: str | None
    decision: str | None
    reviewer_comment: str | None
    jira_key: str | None
    token_cost_usd: Decimal
    rerun_count: int
    active_prompt_versions: dict | None
    active_index_version: str | None
    ingested_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewDecisionRequest(BaseModel):
    reviewer_id: str
    decision: Literal["approve", "reject", "approve_rerun"]
    comment: str | None = None
    rerun_overrides: dict | None = Field(
        default=None, description="Optional reviewer-approved overrides to rerun_parameters"
    )


class TemplateUpdateRequest(BaseModel):
    reviewer_id: str
    template_payload: TemplatePayload


class IncidentListFilter(BaseModel):
    status: str | None = None
    bot_id: str | None = None
    environment: str | None = None
    limit: int = 50
    offset: int = 0
