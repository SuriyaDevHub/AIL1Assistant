"""Incident state machine - doc section 3.2.

INGESTED -> SCREENED -> PARSED -> DIAGNOSED -> (AUTO_RESOLVE_CANDIDATE |
ESCALATION_DRAFTED) -> AWAITING_REVIEW -> (SUBMITTED | REJECTED |
RERUN_APPROVED) -> CLOSED. RERUN_APPROVED returns to DIAGNOSED (doc 2.2 step
11). Terminal failure states BLOCKED_BY_GUARDRAIL, UNPARSEABLE and
PLATFORM_UNAVAILABLE each route to MANUAL_FALLBACK.

This module is deterministic, pure Python - no I/O - so the transition graph
can be unit tested exhaustively without a database.
"""
from __future__ import annotations

import enum


class IncidentStatus(str, enum.Enum):
    INGESTED = "INGESTED"
    SCREENED = "SCREENED"
    PARSED = "PARSED"
    DIAGNOSED = "DIAGNOSED"
    AUTO_RESOLVE_CANDIDATE = "AUTO_RESOLVE_CANDIDATE"
    ESCALATION_DRAFTED = "ESCALATION_DRAFTED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    RERUN_APPROVED = "RERUN_APPROVED"
    CLOSED = "CLOSED"
    BLOCKED_BY_GUARDRAIL = "BLOCKED_BY_GUARDRAIL"
    UNPARSEABLE = "UNPARSEABLE"
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    MANUAL_FALLBACK = "MANUAL_FALLBACK"


TERMINAL_FAILURE_STATES = frozenset(
    {
        IncidentStatus.BLOCKED_BY_GUARDRAIL,
        IncidentStatus.UNPARSEABLE,
        IncidentStatus.PLATFORM_UNAVAILABLE,
    }
)

TERMINAL_STATES = frozenset(
    {IncidentStatus.CLOSED, IncidentStatus.MANUAL_FALLBACK} | TERMINAL_FAILURE_STATES
)

# Any non-terminal state may fall closed to a terminal failure state, plus
# each state's specific forward transitions.
_FAIL_CLOSED_TARGETS = {
    IncidentStatus.BLOCKED_BY_GUARDRAIL,
    IncidentStatus.PLATFORM_UNAVAILABLE,
}

_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.INGESTED: {IncidentStatus.SCREENED} | _FAIL_CLOSED_TARGETS,
    IncidentStatus.SCREENED: (
        {IncidentStatus.PARSED, IncidentStatus.UNPARSEABLE} | _FAIL_CLOSED_TARGETS
    ),
    IncidentStatus.PARSED: {IncidentStatus.DIAGNOSED} | _FAIL_CLOSED_TARGETS,
    IncidentStatus.DIAGNOSED: (
        {IncidentStatus.AUTO_RESOLVE_CANDIDATE, IncidentStatus.ESCALATION_DRAFTED}
        | _FAIL_CLOSED_TARGETS
    ),
    IncidentStatus.AUTO_RESOLVE_CANDIDATE: {IncidentStatus.AWAITING_REVIEW},
    IncidentStatus.ESCALATION_DRAFTED: {IncidentStatus.AWAITING_REVIEW},
    IncidentStatus.AWAITING_REVIEW: {
        IncidentStatus.SUBMITTED,
        IncidentStatus.REJECTED,
        IncidentStatus.RERUN_APPROVED,
    },
    IncidentStatus.RERUN_APPROVED: {IncidentStatus.DIAGNOSED} | _FAIL_CLOSED_TARGETS,
    IncidentStatus.SUBMITTED: {IncidentStatus.CLOSED},
    IncidentStatus.REJECTED: {IncidentStatus.CLOSED},
    IncidentStatus.BLOCKED_BY_GUARDRAIL: {IncidentStatus.MANUAL_FALLBACK},
    IncidentStatus.UNPARSEABLE: {IncidentStatus.MANUAL_FALLBACK},
    IncidentStatus.PLATFORM_UNAVAILABLE: {IncidentStatus.MANUAL_FALLBACK},
    IncidentStatus.CLOSED: set(),
    IncidentStatus.MANUAL_FALLBACK: set(),
}


class IllegalTransitionError(ValueError):
    def __init__(self, current: IncidentStatus, target: IncidentStatus):
        super().__init__(f"Illegal transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: IncidentStatus, target: IncidentStatus) -> bool:
    return target in _TRANSITIONS.get(current, set())


def transition(current: IncidentStatus, target: IncidentStatus) -> IncidentStatus:
    """Validate and return the target state, or raise IllegalTransitionError.

    Callers are responsible for persisting the new state and writing the
    audit ledger entry - this function is pure and has no side effects so
    it can be exhaustively unit tested.
    """
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)
    return target


def is_terminal(status: IncidentStatus) -> bool:
    return status in TERMINAL_STATES
