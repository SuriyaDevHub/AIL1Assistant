"""doc 9 "Guardrail negative" row: deliberate ungrounded outputs, to confirm blocking."""
from decimal import Decimal

from geniebot.guardrails.output_guardrails import evaluate_output_guardrails


def test_citation_to_nonexistent_log_line_is_blocked():
    result = evaluate_output_guardrails(
        generated_text="Root cause is a connectivity blip, see line 99.",
        citation_refs=[("log", "99")],
        evidence_line_numbers={1, 2, 3},
        kb_source_refs=set(),
        cumulative_cost_usd=Decimal("0.01"), cumulative_tokens=100,
        cost_ceiling_usd=Decimal("1.0"), token_ceiling=10_000,
        rerun_count=0, max_reruns=2,
    )
    assert result.blocked
    assert result.block_reason == "GROUND_UNRESOLVABLE_CITATION"


def test_citation_to_nonexistent_kb_chunk_is_blocked():
    result = evaluate_output_guardrails(
        generated_text="Per SOP-999, restart the loader.",
        citation_refs=[("kb", "SOP-999")],
        evidence_line_numbers={1},
        kb_source_refs={"SOP-1"},
        cumulative_cost_usd=Decimal("0.01"), cumulative_tokens=100,
        cost_ceiling_usd=Decimal("1.0"), token_ceiling=10_000,
        rerun_count=0, max_reruns=2,
    )
    assert result.blocked


def test_grounded_citations_pass():
    result = evaluate_output_guardrails(
        generated_text="Root cause per line 2 and SOP-1.",
        citation_refs=[("log", "2"), ("kb", "SOP-1")],
        evidence_line_numbers={1, 2, 3},
        kb_source_refs={"SOP-1"},
        cumulative_cost_usd=Decimal("0.01"), cumulative_tokens=100,
        cost_ceiling_usd=Decimal("1.0"), token_ceiling=10_000,
        rerun_count=0, max_reruns=2,
    )
    assert not result.blocked


def test_flag_instead_of_block_when_configured():
    result = evaluate_output_guardrails(
        generated_text="Root cause per line 99.",
        citation_refs=[("log", "99")],
        evidence_line_numbers={1},
        kb_source_refs=set(),
        cumulative_cost_usd=Decimal("0.01"), cumulative_tokens=100,
        cost_ceiling_usd=Decimal("1.0"), token_ceiling=10_000,
        rerun_count=0, max_reruns=2,
        unresolvable_citation_action="flag",
    )
    assert not result.blocked
    outcome = next(o for o in result.outcomes if o.rule_id == "GROUND_UNRESOLVABLE_CITATION")
    assert outcome.disposition == "flag"
