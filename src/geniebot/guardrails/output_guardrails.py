"""Output guardrails - doc section 6.2: grounding check, proposal-only
enforcement, sensitive-data egress re-screening, moderation, budget ceiling,
loop cap. Pure functions; the caller writes outcomes to the audit ledger and
enforces fail-closed routing (doc 1.4 "Fail closed").
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from geniebot.guardrails.input_guardrails import GuardrailOutcome
from geniebot.guardrails.redaction_patterns import redact

# Patterns describing generated text that claims to have *executed*
# remediation against production, rather than merely proposing it - doc 1.4
# "Proposal-only: the assistant recommends; it never executes remediation".
_EXECUTED_REMEDIATION_PATTERNS = [
    re.compile(r"(?i)\bI(?:'ve| have)\s+(restarted|killed|deleted|dropped|truncated|patched|deployed)\b"),
    re.compile(r"(?i)\bI(?:'m| am)\s+(now\s+)?(restarting|deleting|dropping|running|executing)\b"),
    re.compile(r"(?i)\b(executing|running)\s+(this|the following)\s+(command|script|query)\s+now\b"),
    re.compile(r"(?i)\bDROP\s+TABLE\b"),
    re.compile(r"(?i)\bDELETE\s+FROM\s+\w+\s*(;|$)"),
    re.compile(r"(?i)\brm\s+-rf\b"),
    re.compile(r"(?i)\bkill\s+-9\b"),
    re.compile(r"(?i)\bsystemctl\s+(restart|stop)\b"),
    re.compile(r"(?i)\bissue\s+has\s+been\s+resolved\s+automatically\b"),
]


@dataclass(frozen=True)
class OutputGuardrailResult:
    blocked: bool
    outcomes: list[GuardrailOutcome] = field(default_factory=list)

    @property
    def block_reason(self) -> str | None:
        for o in self.outcomes:
            if not o.passed:
                return o.rule_id
        return None


def grounding_check(
    citation_refs: Sequence[tuple[str, str]],
    evidence_line_numbers: set[int],
    kb_source_refs: set[str],
) -> tuple[bool, list[str]]:
    """citation_refs: list of (type, ref) pairs from the diagnosis/template
    output. Returns (all_resolved, unresolved_refs)."""
    unresolved = []
    for ctype, ref in citation_refs:
        if ctype == "log":
            try:
                resolved = int(ref) in evidence_line_numbers
            except ValueError:
                resolved = False
        elif ctype == "kb":
            resolved = ref in kb_source_refs
        else:
            resolved = False
        if not resolved:
            unresolved.append(f"{ctype}:{ref}")
    return (len(unresolved) == 0, unresolved)


def proposal_only_violations(text: str) -> list[str]:
    return [p.pattern for p in _EXECUTED_REMEDIATION_PATTERNS if p.search(text)]


def moderation_flagged(text: str) -> bool:
    """Minimal local stand-in for a real moderation service. Swap for the
    approved content-moderation API before production cutover - see
    docs/runbook.md production cutover checklist."""
    blocklist = ["kill yourself", "build a bomb", "self-harm instructions"]
    lowered = text.lower()
    return any(term in lowered for term in blocklist)


def evaluate_output_guardrails(
    *,
    generated_text: str,
    citation_refs: Sequence[tuple[str, str]],
    evidence_line_numbers: set[int],
    kb_source_refs: set[str],
    cumulative_cost_usd: Decimal,
    cumulative_tokens: int,
    cost_ceiling_usd: Decimal,
    token_ceiling: int,
    rerun_count: int,
    max_reruns: int,
    grounding_enabled: bool = True,
    proposal_only_enabled: bool = True,
    egress_enabled: bool = True,
    moderation_enabled: bool = True,
    budget_enabled: bool = True,
    loop_cap_enabled: bool = True,
    unresolvable_citation_action: str = "block",
) -> OutputGuardrailResult:
    outcomes: list[GuardrailOutcome] = []
    blocked = False

    if grounding_enabled:
        all_resolved, unresolved = grounding_check(citation_refs, evidence_line_numbers, kb_source_refs)
        passed = all_resolved or unresolvable_citation_action != "block"
        outcomes.append(
            GuardrailOutcome(
                "GROUND_UNRESOLVABLE_CITATION",
                passed=passed,
                disposition="grounded" if all_resolved else unresolvable_citation_action,
                detail=",".join(unresolved),
            )
        )
        blocked = blocked or not passed

    if proposal_only_enabled:
        violations = proposal_only_violations(generated_text)
        outcomes.append(
            GuardrailOutcome(
                "PROPOSAL_ONLY_VIOLATION",
                passed=not violations,
                disposition="blocked" if violations else "clean",
                detail=str(violations),
            )
        )
        blocked = blocked or bool(violations)

    if egress_enabled:
        egress_result = redact(generated_text)
        outcomes.append(
            GuardrailOutcome(
                "EGRESS_SENSITIVE_DATA",
                passed=True,
                disposition="redacted" if egress_result.had_matches else "clean",
            )
        )

    if moderation_enabled:
        flagged = moderation_flagged(generated_text)
        outcomes.append(
            GuardrailOutcome("MODERATION_BLOCK", passed=not flagged, disposition="blocked" if flagged else "clean")
        )
        blocked = blocked or flagged

    if budget_enabled:
        breach = cumulative_cost_usd > cost_ceiling_usd or cumulative_tokens > token_ceiling
        outcomes.append(
            GuardrailOutcome(
                "BUDGET_CEILING_BREACH",
                passed=not breach,
                disposition="halted" if breach else "within_budget",
                detail=f"cost={cumulative_cost_usd} tokens={cumulative_tokens}",
            )
        )
        blocked = blocked or breach

    if loop_cap_enabled:
        breach = rerun_count > max_reruns
        outcomes.append(
            GuardrailOutcome(
                "LOOP_CAP_EXCEEDED",
                passed=not breach,
                disposition="halted" if breach else "within_cap",
                detail=f"rerun_count={rerun_count} max={max_reruns}",
            )
        )
        blocked = blocked or breach

    return OutputGuardrailResult(blocked=blocked, outcomes=outcomes)
