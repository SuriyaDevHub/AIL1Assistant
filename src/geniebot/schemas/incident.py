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
    generic_l1_checklist: list[str] | None = None

    model_config = {"from_attributes": True}


class ReviewDecisionRequest(BaseModel):
    reviewer_id: str
    decision: Literal["approve", "reject", "approve_rerun", "resolved", "escalated"]
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


class JiraTicketDetail(BaseModel):
    """Thin wrapper over integrations/jira_client.py's JiraIssue dataclass -
    exposes the mock/real ticket's actual content, not just its key."""

    key: str
    summary: str
    description: str
    status: str
    labels: list[str]
    comments: list[str]
    created_at: datetime


class IncidentTimelineEntry(BaseModel):
    status: str
    at: datetime


class PrecedentIncident(BaseModel):
    incident_id: str
    job_run_id: str
    at: datetime
    reviewer_id: str | None
    reviewer_comment: str | None
    resolution_summary: str
    jira_key: str | None
    jira_status: str | None
    within_dedup_window: bool


class IncidentPrecedent(BaseModel):
    resolved_precedent: PrecedentIncident | None
    open_precedent: PrecedentIncident | None


class IncidentStats(BaseModel):
    total_incidents: int
    open_count: int
    status_counts: dict[str, int]
    decision_counts: dict[str, int]
    confidence_buckets: dict[str, int]
    avg_confidence: float | None
    cost_last_24h_usd: Decimal
    low_context_escalations_last_24h: int
    recent: list[IncidentListItem]
