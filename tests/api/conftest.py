from __future__ import annotations

import httpx
import pytest_asyncio

from geniebot.api.deps import create_dev_token, get_session
from geniebot.main import app


@pytest_asyncio.fixture
async def client(session):
    async def _override_get_session():
        yield session

    app.dependency_overrides[get_session] = _override_get_session
    # Default identity is full-visibility (admin) so the many existing
    # tests that don't care about role-scoped visibility (GenieBot L1
    # Enhancement Plan, "Scope what each role can see") don't need their
    # own token. A test exercising the ownership rule passes its own
    # Authorization header per-request, which httpx merges over this
    # client-level default.
    default_token = create_dev_token("test-admin", role="admin")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {default_token}"}
    ) as c:
        yield c
    app.dependency_overrides.clear()
