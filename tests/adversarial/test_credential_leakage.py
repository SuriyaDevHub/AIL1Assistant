"""doc 9 "Adversarial" row: credential leakage attempts."""
from geniebot.guardrails.input_guardrails import evaluate_input_guardrails


def test_aws_key_never_reaches_redacted_output():
    log = "Uploading with AKIAABCDEFGHIJKLMNOP to s3 bucket\nConnectionError: timeout\n"
    result = evaluate_input_guardrails(log, max_chars=100_000, block_above="restricted")
    assert "AKIAABCDEFGHIJKLMNOP" not in result.redacted_text
    # a credential in the payload pushes classification to restricted and blocks by default
    assert result.blocked


def test_password_in_connection_string_redacted_and_blocked():
    log = "jdbc:postgresql://db01:5432/genie;password=CorrectHorseBattery\nConnectionError: refused\n"
    result = evaluate_input_guardrails(log, max_chars=100_000, block_above="restricted")
    assert "CorrectHorseBattery" not in result.redacted_text
    assert result.blocked


def test_credential_allowed_through_when_threshold_relaxed_but_still_redacted():
    log = "password=hunter2verysecret\nConnectionError: refused\n"
    result = evaluate_input_guardrails(log, max_chars=100_000, block_above="restricted", classification_enabled=False)
    assert not result.blocked
    assert "hunter2verysecret" not in result.redacted_text
