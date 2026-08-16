"""doc 9 "Guardrail negative" row: remediation-instruction outputs, to
confirm blocking - doc 1.4 "Proposal-only: the assistant recommends; it
never executes remediation"."""
from decimal import Decimal

from geniebot.guardrails.output_guardrails import evaluate_output_guardrails

_BASE_KWARGS = {
    "citation_refs": [], "evidence_line_numbers": set(), "kb_source_refs": set(),
    "cumulative_cost_usd": Decimal("0.01"), "cumulative_tokens": 100,
    "cost_ceiling_usd": Decimal("1.0"), "token_ceiling": 10_000,
    "rerun_count": 0, "max_reruns": 2, "grounding_enabled": False,
}


def test_claims_execution_are_blocked():
    result = evaluate_output_guardrails(
        generated_text="I have restarted the production service and the issue is now resolved.",
        **_BASE_KWARGS,
    )
    assert result.blocked
    assert result.block_reason == "PROPOSAL_ONLY_VIOLATION"


def test_destructive_sql_in_output_is_blocked():
    result = evaluate_output_guardrails(
        generated_text="Recommended fix: DROP TABLE payments_staging; then rerun the job.",
        **_BASE_KWARGS,
    )
    assert result.blocked


def test_shell_destructive_command_is_blocked():
    result = evaluate_output_guardrails(
        generated_text="Run rm -rf /data/staging on the host to clear the lock.",
        **_BASE_KWARGS,
    )
    assert result.blocked


def test_proposal_only_phrasing_passes():
    result = evaluate_output_guardrails(
        generated_text="Recommend a controlled rerun once connectivity to db01 is confirmed restored.",
        **_BASE_KWARGS,
    )
    assert not result.blocked
