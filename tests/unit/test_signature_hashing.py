from geniebot.integrations.dedup import compute_error_signature_id


def test_signature_deterministic():
    a = compute_error_signature_id(exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="bot1")
    b = compute_error_signature_id(exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="bot1")
    assert a == b


def test_signature_case_and_whitespace_insensitive():
    a = compute_error_signature_id(exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="bot1")
    b = compute_error_signature_id(exception_type=" connectionerror ", failing_module="Payments/Loader.py ", bot_id=" BOT1")
    assert a == b


def test_signature_changes_with_exception_type():
    a = compute_error_signature_id(exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="bot1")
    b = compute_error_signature_id(exception_type="TimeoutError", failing_module="payments/loader.py", bot_id="bot1")
    assert a != b


def test_signature_changes_with_bot_id():
    a = compute_error_signature_id(exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="bot1")
    b = compute_error_signature_id(exception_type="ConnectionError", failing_module="payments/loader.py", bot_id="bot2")
    assert a != b
