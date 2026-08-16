"""Pattern-based redaction rules shared by input guardrails (runtime, doc 6.1)
and the knowledge-base build pipeline (doc 4.2 step 3) - the doc explicitly
requires "the same redaction engine used at runtime", so both call this
module rather than maintaining separate rule sets.

Entity-based (NER-style) scrubbing beyond these deterministic patterns is a
platform-specific capability the doc leaves unspecified (data classification
service, doc 6.1) - RULES below cover the pattern-detectable classes
(credentials, connection strings, tokens, common PII shapes); wire in the
approved entity-detection service for full coverage before production
cutover.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionRule:
    rule_id: str
    pattern: re.Pattern[str]
    replacement: str


RULES: list[RedactionRule] = [
    RedactionRule(
        "REDACT_CONNECTION_STRING",
        re.compile(
            r"(?i)\b(?:jdbc|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|Server=)\S*"
            r"(?:://|;)\S*(?:password|pwd)=\S+",
        ),
        "[REDACTED_CONNECTION_STRING]",
    ),
    RedactionRule(
        "REDACT_CREDENTIAL",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|"
            r"client[_-]?secret)\b\s*[:=]\s*['\"]?[^\s'\"]{3,}"
        ),
        r"\1=[REDACTED]",
    ),
    RedactionRule(
        "REDACT_CREDENTIAL",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.=]{10,}"),
        "Bearer [REDACTED_TOKEN]",
    ),
    RedactionRule(
        "REDACT_CREDENTIAL",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED_AWS_ACCESS_KEY]",
    ),
    RedactionRule(
        "REDACT_ACCOUNT_ID",
        re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b"),
        "[REDACTED_ACCOUNT_NUMBER]",
    ),
    RedactionRule(
        "REDACT_PII",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED_SSN]",
    ),
    RedactionRule(
        "REDACT_PII",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    RedactionRule(
        "REDACT_PII",
        re.compile(r"\b(?:\+?\d{1,3}[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b"),
        "[REDACTED_PHONE]",
    ),
]


@dataclass(frozen=True)
class RedactionFinding:
    rule_id: str
    count: int


@dataclass(frozen=True)
class RedactionResult:
    redacted_text: str
    findings: list[RedactionFinding]

    @property
    def had_matches(self) -> bool:
        return any(f.count > 0 for f in self.findings)


def redact(text: str) -> RedactionResult:
    counts: dict[str, int] = {}
    result = text
    for rule in RULES:
        result, n = rule.pattern.subn(rule.replacement, result)
        if n:
            counts[rule.rule_id] = counts.get(rule.rule_id, 0) + n
    findings = [RedactionFinding(rule_id=k, count=v) for k, v in sorted(counts.items())]
    return RedactionResult(redacted_text=result, findings=findings)
