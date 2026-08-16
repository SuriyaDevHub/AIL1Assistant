"""Input guardrails - doc section 6.1: redaction, data classification,
injection screening, payload capping, all before any model call. Pure
functions with no I/O so they're exhaustively unit- and adversarial-testable
(doc section 9, "Adversarial" and "Guardrail negative" rows); the caller
(orchestration/pipeline.py) is responsible for writing each outcome to the
audit ledger and for the "fail closed" routing to MANUAL_FALLBACK on error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from geniebot.guardrails.redaction_patterns import RedactionFinding, redact

CLASSIFICATION_ORDER = ["public", "internal", "confidential", "restricted"]

# Rule ids that, if triggered during redaction, push classification up.
_RESTRICTED_TRIGGERS = {"REDACT_CREDENTIAL", "REDACT_CONNECTION_STRING"}
_CONFIDENTIAL_TRIGGERS = {"REDACT_PII", "REDACT_ACCOUNT_ID"}

_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore (all )?(previous|prior|above) instructions"),
    re.compile(r"(?i)disregard (the )?(above|previous|prior)"),
    re.compile(r"(?i)you are now (a|an) "),
    re.compile(r"(?i)\bsystem\s*:\s*"),
    re.compile(r"(?i)new instructions?\s*:"),
    re.compile(r"(?i)reveal (your|the) (system )?prompt"),
    re.compile(r"(?i)act as (a|an) "),
    re.compile(r"(?i)</?(system|assistant|user)>"),
    re.compile(r"(?i)```\s*system"),
]


@dataclass(frozen=True)
class GuardrailOutcome:
    rule_id: str
    passed: bool
    disposition: str
    detail: str = ""


@dataclass(frozen=True)
class InputGuardrailResult:
    redacted_text: str
    classification: str
    blocked: bool
    truncated: bool
    outcomes: list[GuardrailOutcome] = field(default_factory=list)

    @property
    def block_reason(self) -> str | None:
        for o in self.outcomes:
            if not o.passed:
                return o.rule_id
        return None


def classify(findings: list[RedactionFinding]) -> str:
    rule_ids = {f.rule_id for f in findings}
    if rule_ids & _RESTRICTED_TRIGGERS:
        return "restricted"
    if rule_ids & _CONFIDENTIAL_TRIGGERS:
        return "confidential"
    return "internal"


def screen_injection(text: str) -> list[str]:
    """Returns the list of matched injection-pattern descriptions, empty if clean."""
    matches = []
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            matches.append(pattern.pattern)
    return matches


def neutralise_injection(text: str) -> str:
    """Wraps suspicious spans so a downstream model treats them as quoted
    data rather than instructions, rather than deleting evidence outright."""
    neutralised = text
    for pattern in _INJECTION_PATTERNS:
        neutralised = pattern.sub(lambda m: f"[NEUTRALISED_INSTRUCTION: {m.group(0)!r}]", neutralised)
    return neutralised


def cap_payload(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def evaluate_input_guardrails(
    raw_text: str,
    *,
    max_chars: int,
    block_above: str = "restricted",
    injection_action: str = "neutralise",
    redaction_enabled: bool = True,
    classification_enabled: bool = True,
    injection_screening_enabled: bool = True,
    payload_capping_enabled: bool = True,
) -> InputGuardrailResult:
    outcomes: list[GuardrailOutcome] = []

    text = raw_text
    findings: list[RedactionFinding] = []
    if redaction_enabled:
        redaction_result = redact(text)
        text = redaction_result.redacted_text
        findings = redaction_result.findings
        outcomes.append(
            GuardrailOutcome(
                "REDACTION",
                passed=True,
                disposition="redacted" if redaction_result.had_matches else "clean",
                detail=str([(f.rule_id, f.count) for f in findings]),
            )
        )

    classification = classify(findings) if classification_enabled else "internal"
    blocked = False
    if classification_enabled:
        block_now = CLASSIFICATION_ORDER.index(classification) >= CLASSIFICATION_ORDER.index(
            block_above
        )
        outcomes.append(
            GuardrailOutcome(
                "CLASSIFY_BLOCK_THRESHOLD",
                passed=not block_now,
                disposition=classification,
            )
        )
        blocked = blocked or block_now

    if injection_screening_enabled:
        matches = screen_injection(text)
        if matches:
            if injection_action == "route_manual":
                outcomes.append(
                    GuardrailOutcome(
                        "INJECTION_INSTRUCTION_LIKE",
                        passed=False,
                        disposition="route_manual",
                        detail=f"{len(matches)} pattern(s) matched",
                    )
                )
                blocked = True
            else:
                text = neutralise_injection(text)
                outcomes.append(
                    GuardrailOutcome(
                        "INJECTION_INSTRUCTION_LIKE",
                        passed=True,
                        disposition="neutralised",
                        detail=f"{len(matches)} pattern(s) matched",
                    )
                )
        else:
            outcomes.append(GuardrailOutcome("INJECTION_INSTRUCTION_LIKE", passed=True, disposition="clean"))

    truncated = False
    if payload_capping_enabled:
        text, truncated = cap_payload(text, max_chars)
        outcomes.append(
            GuardrailOutcome(
                "PAYLOAD_TRUNCATED",
                passed=True,
                disposition="truncated" if truncated else "within_limit",
            )
        )

    return InputGuardrailResult(
        redacted_text=text,
        classification=classification,
        blocked=blocked,
        truncated=truncated,
        outcomes=outcomes,
    )
