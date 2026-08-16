"""Confidence Gate - doc section 5.4. Deterministic code, not a model call.
Evaluates confidence against the configured threshold, presence of
citations, the insufficient_information flag, and error_category rollout
status. Any failing condition forces the escalation path. Thresholds come
from config/thresholds.yaml and config/taxonomy.yaml - externally
configurable and version controlled, per the doc.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from geniebot.schemas.diagnosis import DiagnosisOutput


@dataclass(frozen=True)
class ConfidenceGateResult:
    passed: bool
    """True -> AUTO_RESOLVE_CANDIDATE path. False -> ESCALATION_DRAFTED path."""
    reasons: list[str] = field(default_factory=list)


def evaluate_confidence_gate(
    diagnosis: DiagnosisOutput,
    *,
    error_category: str | None,
    taxonomy: dict,
    auto_resolve_min: float,
) -> ConfidenceGateResult:
    reasons: list[str] = []

    if diagnosis.confidence < auto_resolve_min:
        reasons.append(f"confidence {diagnosis.confidence:.2f} below threshold {auto_resolve_min:.2f}")

    if not diagnosis.citations:
        reasons.append("no citations present")

    if diagnosis.insufficient_information:
        reasons.append("insufficient_information flag set")

    if diagnosis.resolution_type == "escalate":
        reasons.append("resolution_type is escalate")

    category_entry = next(
        (c for c in taxonomy.get("categories", []) if c["id"] == error_category), None
    )
    rollout_status = category_entry.get("rollout_status") if category_entry else None
    if rollout_status not in ("pilot", "expanded"):
        reasons.append(
            f"error_category {error_category!r} not enabled for auto-resolution "
            f"(rollout_status={rollout_status!r})"
        )

    return ConfidenceGateResult(passed=not reasons, reasons=reasons)
