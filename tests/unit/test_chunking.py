from geniebot.kb.chunking import chunk_document, truncate_stack_trace


def test_truncate_stack_trace_keeps_head_and_tail():
    frames = "\n".join(f"    at module.func_{i}(file.py:{i})" for i in range(20))
    text = f"Exception: boom\n{frames}\n"
    result = truncate_stack_trace(text)
    assert "func_0" in result
    assert "func_19" in result
    assert "func_10" not in result
    assert "frames omitted" in result


def test_truncate_stack_trace_leaves_short_trace_untouched():
    text = "Exception: boom\n    at module.func_0(file.py:0)\n    at module.func_1(file.py:1)"
    assert truncate_stack_trace(text) == text


def test_chunk_document_keeps_signature_with_resolution_together():
    text = (
        "Problem:\nConnectionError could not connect to host db01\n\n"
        "Resolution:\nConfirm connectivity, then rerun.\n"
    )
    chunks = chunk_document(
        doc_id="d1", doc_type="resolved_incident", source_ref="GENIE-1",
        error_category="CONNECTIVITY", classification="internal", raw_text=text,
    )
    assert len(chunks) == 1
    assert "ConnectionError" in chunks[0].content
    assert "rerun" in chunks[0].content


def test_chunk_document_splits_cause_into_separate_chunk():
    text = (
        "Problem:\nConnectionError could not connect to host db01\n\n"
        "Root Cause:\nScheduled maintenance on db01\n\n"
        "Resolution:\nConfirm connectivity, then rerun.\n"
    )
    chunks = chunk_document(
        doc_id="d1", doc_type="resolved_incident", source_ref="GENIE-1",
        error_category="CONNECTIVITY", classification="internal", raw_text=text,
    )
    assert len(chunks) == 2
    assert any("maintenance" in c.content for c in chunks)
    assert all(c.parent_summary for c in chunks)


def test_chunk_document_no_headers_returns_single_chunk():
    text = "Just a plain unstructured note about a failure."
    chunks = chunk_document(
        doc_id="d1", doc_type="runbook", source_ref="NOTE-1",
        error_category="UNKNOWN", classification="internal", raw_text=text,
    )
    assert len(chunks) == 1
    assert chunks[0].content == text


def test_chunk_document_empty_text_returns_no_chunks():
    chunks = chunk_document(
        doc_id="d1", doc_type="runbook", source_ref="NOTE-1",
        error_category="UNKNOWN", classification="internal", raw_text="   ",
    )
    assert chunks == []
