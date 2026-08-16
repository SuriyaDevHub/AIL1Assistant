import pytest


@pytest.mark.asyncio
async def test_kb_search_with_no_active_index_returns_no_precedent(client):
    r = await client.post(
        "/kb/search",
        json={"error_signature": "ConnectionError", "failing_module": "m.py", "error_category": "CONNECTIVITY"},
    )
    assert r.status_code == 200
    assert r.json()["no_precedent"] is True


@pytest.mark.asyncio
async def test_submit_document_without_active_index_returns_409(client):
    r = await client.post(
        "/kb/documents",
        json={"doc_id": "d1", "doc_type": "sop", "source_ref": "SOP-X", "raw_text": "Problem:\nx\n\nResolution:\ny\n"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_list_sop_drafts_empty_by_default(client):
    r = await client.get("/kb/sop-drafts")
    assert r.status_code == 200
    assert r.json() == []
