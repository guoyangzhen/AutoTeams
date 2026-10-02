"""Exercise production connection guards with real asyncpg connections."""
import os

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["worker", "bootstrap"])
async def test_production_role_guard_accepts_restricted_asyncpg_connection(kind, monkeypatch):
    url = os.getenv(f"RLS_TEST_{kind.upper()}_URL", "")
    if not url:
        pytest.skip("Requires a migrated PostgreSQL database and role credentials")
    from app import database
    from app.config import settings

    engine = create_async_engine(url, poolclass=NullPool)
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, f"DATABASE_{kind.upper()}_ROLE", f"autoteams_{kind}")
    monkeypatch.setattr(database, f"{kind}_engine", engine)
    event.listen(engine.sync_engine, "connect", getattr(database, f"_verify_{kind}_connection"))
    try:
        async with engine.connect() as connection:
            role = (await connection.execute(text("SELECT current_user"))).scalar_one()
        assert role == f"autoteams_{kind}"
    finally:
        await engine.dispose()
