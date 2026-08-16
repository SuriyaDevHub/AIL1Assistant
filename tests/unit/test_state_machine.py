import pytest

from geniebot.state_machine import (
    IllegalTransitionError,
    IncidentStatus,
    can_transition,
    is_terminal,
    transition,
)

HAPPY_PATH = [
    (IncidentStatus.INGESTED, IncidentStatus.SCREENED),
    (IncidentStatus.SCREENED, IncidentStatus.PARSED),
    (IncidentStatus.PARSED, IncidentStatus.DIAGNOSED),
    (IncidentStatus.DIAGNOSED, IncidentStatus.AUTO_RESOLVE_CANDIDATE),
    (IncidentStatus.AUTO_RESOLVE_CANDIDATE, IncidentStatus.AWAITING_REVIEW),
    (IncidentStatus.AWAITING_REVIEW, IncidentStatus.SUBMITTED),
    (IncidentStatus.SUBMITTED, IncidentStatus.CLOSED),
]

ESCALATION_PATH = [
    (IncidentStatus.DIAGNOSED, IncidentStatus.ESCALATION_DRAFTED),
    (IncidentStatus.ESCALATION_DRAFTED, IncidentStatus.AWAITING_REVIEW),
]

RERUN_LOOP = [
    (IncidentStatus.AWAITING_REVIEW, IncidentStatus.RERUN_APPROVED),
    (IncidentStatus.RERUN_APPROVED, IncidentStatus.DIAGNOSED),
]

FAIL_CLOSED_ROUTES = [
    (IncidentStatus.INGESTED, IncidentStatus.BLOCKED_BY_GUARDRAIL),
    (IncidentStatus.SCREENED, IncidentStatus.UNPARSEABLE),
    (IncidentStatus.PARSED, IncidentStatus.PLATFORM_UNAVAILABLE),
    # Steps 8-9 (Template Generator, output guardrails) run after the
    # confidence-gate transition and can still fail - both gate outcomes
    # need a fail-closed route, not just the happy path to AWAITING_REVIEW.
    (IncidentStatus.AUTO_RESOLVE_CANDIDATE, IncidentStatus.PLATFORM_UNAVAILABLE),
    (IncidentStatus.AUTO_RESOLVE_CANDIDATE, IncidentStatus.BLOCKED_BY_GUARDRAIL),
    (IncidentStatus.ESCALATION_DRAFTED, IncidentStatus.PLATFORM_UNAVAILABLE),
    (IncidentStatus.ESCALATION_DRAFTED, IncidentStatus.BLOCKED_BY_GUARDRAIL),
    (IncidentStatus.BLOCKED_BY_GUARDRAIL, IncidentStatus.MANUAL_FALLBACK),
    (IncidentStatus.UNPARSEABLE, IncidentStatus.MANUAL_FALLBACK),
    (IncidentStatus.PLATFORM_UNAVAILABLE, IncidentStatus.MANUAL_FALLBACK),
]


@pytest.mark.parametrize("current,target", HAPPY_PATH + ESCALATION_PATH + RERUN_LOOP + FAIL_CLOSED_ROUTES)
def test_legal_transitions_succeed(current, target):
    assert transition(current, target) == target
    assert can_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (IncidentStatus.INGESTED, IncidentStatus.CLOSED),
        (IncidentStatus.AWAITING_REVIEW, IncidentStatus.DIAGNOSED),
        (IncidentStatus.CLOSED, IncidentStatus.SUBMITTED),
        (IncidentStatus.MANUAL_FALLBACK, IncidentStatus.INGESTED),
        (IncidentStatus.SCREENED, IncidentStatus.AWAITING_REVIEW),
        (IncidentStatus.REJECTED, IncidentStatus.SUBMITTED),
    ],
)
def test_illegal_transitions_raise(current, target):
    assert not can_transition(current, target)
    with pytest.raises(IllegalTransitionError):
        transition(current, target)


def test_terminal_states():
    assert is_terminal(IncidentStatus.CLOSED)
    assert is_terminal(IncidentStatus.MANUAL_FALLBACK)
    assert not is_terminal(IncidentStatus.AWAITING_REVIEW)


def test_every_non_terminal_state_has_outgoing_transitions():
    for status in IncidentStatus:
        if not is_terminal(status):
            from geniebot.state_machine import _TRANSITIONS

            assert _TRANSITIONS[status], f"{status} has no outgoing transitions but isn't terminal"
