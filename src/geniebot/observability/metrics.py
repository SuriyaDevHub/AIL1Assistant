"""Prometheus metrics - doc section 10.1: cost, performance, quality,
safety, drift. /metrics is scraped by the platform's Prometheus; doc 10.2
alerting triggers (budget breach, rejection-rate rise, queue backlog,
retrieval regression) are Prometheus alert rules defined against these
series in the real deployment, not something this codebase evaluates
itself - see deploy/ for where that wiring would live.
"""
from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# --- Cost (doc 10.1 "Cost") ---
AGENT_TOKENS_TOTAL = Counter(
    "genie_agent_tokens_total", "Tokens consumed per agent invocation", ["agent", "kind"]
)
AGENT_COST_USD_TOTAL = Counter("genie_agent_cost_usd_total", "Estimated USD cost per agent", ["agent"])

# --- Performance (doc 10.1 "Performance") ---
AGENT_LATENCY_SECONDS = Histogram(
    "genie_agent_latency_seconds", "Per-agent-call latency", ["agent"]
)
INCIDENT_PIPELINE_SECONDS = Histogram(
    "genie_incident_pipeline_seconds", "End-to-end pipeline latency per incident (steps 3-9)"
)
AGENT_ERRORS_TOTAL = Counter("genie_agent_errors_total", "Agent invocation failures", ["agent", "error_type"])

# --- Quality (doc 10.1 "Quality") ---
INCIDENT_OUTCOMES_TOTAL = Counter(
    "genie_incident_outcomes_total", "Terminal/gate outcome per incident", ["outcome"]
)
REVIEW_DECISIONS_TOTAL = Counter("genie_review_decisions_total", "Reviewer decisions", ["decision"])

# --- Safety (doc 10.1 "Safety") ---
GUARDRAIL_EVALUATIONS_TOTAL = Counter(
    "genie_guardrail_evaluations_total", "Guardrail rule evaluations", ["rule_id", "passed"]
)

# --- Drift (doc 10.1 "Drift") ---
NO_PRECEDENT_TOTAL = Counter(
    "genie_retrieval_no_precedent_total", "Retrievals with no qualifying KB match", ["error_category"]
)


def record_agent_invocation(*, agent: str, prompt_tokens: int, completion_tokens: int, latency_ms: float, cost_usd: float) -> None:
    AGENT_TOKENS_TOTAL.labels(agent=agent, kind="prompt").inc(prompt_tokens)
    AGENT_TOKENS_TOTAL.labels(agent=agent, kind="completion").inc(completion_tokens)
    AGENT_COST_USD_TOTAL.labels(agent=agent).inc(cost_usd)
    AGENT_LATENCY_SECONDS.labels(agent=agent).observe(latency_ms / 1000)


def record_agent_error(*, agent: str, error_type: str) -> None:
    AGENT_ERRORS_TOTAL.labels(agent=agent, error_type=error_type).inc()


def record_guardrail_outcome(*, rule_id: str, passed: bool) -> None:
    GUARDRAIL_EVALUATIONS_TOTAL.labels(rule_id=rule_id, passed=str(passed)).inc()


def record_incident_outcome(outcome: str) -> None:
    INCIDENT_OUTCOMES_TOTAL.labels(outcome=outcome).inc()


def record_review_decision(decision: str) -> None:
    REVIEW_DECISIONS_TOTAL.labels(decision=decision).inc()


def record_no_precedent(error_category: str | None) -> None:
    NO_PRECEDENT_TOTAL.labels(error_category=error_category or "unknown").inc()


router = APIRouter()


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
