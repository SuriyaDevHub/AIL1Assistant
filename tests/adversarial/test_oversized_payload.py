"""doc 9 "Adversarial" row: oversized payloads."""
from geniebot.guardrails.input_guardrails import evaluate_input_guardrails


def test_oversized_log_is_truncated_not_blocked():
    log = "ConnectionError: could not connect to host db01\n" + ("padding line\n" * 100_000)
    assert len(log) > 200_000

    result = evaluate_input_guardrails(log, max_chars=200_000)
    assert result.truncated
    assert len(result.redacted_text) <= 200_000
    assert not result.blocked


def test_payload_within_limit_is_not_truncated():
    log = "ConnectionError: could not connect to host db01\n"
    result = evaluate_input_guardrails(log, max_chars=200_000)
    assert not result.truncated
    assert result.redacted_text == log
