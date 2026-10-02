"""Grant executor authorization and audit writes under the real restricted API role.

Requires the isolated migrated database used by the RLS suite. Network dispatch is
stubbed here; runner-chain-smoke separately covers the actual local processes.
"""
import os
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.audit_log import AuditLog
from app.models.enterprise import Enterprise
from app.models.local_path_grant import LocalPathGrant
from app.models.user import User
from app.utils.db_tenant_context import (
    authenticated_user_scope,
    get_authenticated_user_id,
    get_current_enterprise_id,
    tenant_scope,
)

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="requires isolated RLS PostgreSQL URLs")


@pytest_asyncio.fixture
async def grants(monkeypatch):
    from app.services.mcp import grant_executor as ge

    admin = create_engine(ADMIN_URL)
    business = create_async_engine(APP_URL, poolclass=NullPool)
    factory = async_sessionmaker(business, expire_on_commit=False)
    monkeypatch.setattr(ge, "async_session_factory", factory)
    ent_a, ent_b, user_a, user_b, user_c = [str(uuid.uuid4()) for _ in range(5)]
    grant_ids = {name: str(uuid.uuid4()) for name in ("own", "other_user", "other_tenant", "revoked", "offline")}
    with Session(admin) as db:
        db.add_all([Enterprise(id=ent_a, name="MCP PG A"), Enterprise(id=ent_b, name="MCP PG B")])
        db.flush()
        for uid, eid in ((user_a, ent_a), (user_b, ent_a), (user_c, ent_b)):
            db.add(User(id=uid, enterprise_id=eid, email=f"{uid}@mcp.invalid", password_hash="unused", name="MCP test", role="member", is_active=True))
        db.flush()
        for name, gid in grant_ids.items():
            db.add(LocalPathGrant(
                id=gid, enterprise_id=ent_b if name == "other_tenant" else ent_a,
                user_id=user_c if name == "other_tenant" else user_b if name == "other_user" else user_a,
                local_path="C:/isolated-mcp-test", scope="read",
                status=name if name in ("revoked", "offline") else "connected",
                runner_id="same-runner-name-is-not-identity",
            ))
        db.commit()
    try:
        async with factory() as db:
            role = (await db.execute(text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"))).one()
            assert role[0] == "autoteams_app" and not role[1] and not role[2]
        yield ge, admin, ent_a, user_a, grant_ids
    finally:
        await business.dispose()
        with Session(admin) as db:
            db.execute(delete(AuditLog).where(AuditLog.resource_id.in_(grant_ids.values())))
            db.execute(delete(LocalPathGrant).where(LocalPathGrant.id.in_(grant_ids.values())))
            db.execute(delete(User).where(User.id.in_((user_a, user_b, user_c))))
            db.execute(delete(Enterprise).where(Enterprise.id.in_((ent_a, ent_b))))
            db.commit()
        admin.dispose()


@pytest.mark.asyncio
async def test_restricted_role_reads_and_persists_content_free_audit(grants, monkeypatch):
    ge, admin, ent, user, ids = grants
    dispatch = AsyncMock(return_value={"success": True, "data": {"content": "PRIVATE_FILE_SENTINEL"}})
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner", dispatch)
    with tenant_scope("outer-tenant"), authenticated_user_scope("outer-user"):
        result = await ge.grant_executor(operation="read_file", grant_id=ids["own"], enterprise_id=ent, user_id=user, relative_path="note.txt", max_bytes=4096)
        assert result.content == "PRIVATE_FILE_SENTINEL"
        assert get_current_enterprise_id() == "outer-tenant"
        assert get_authenticated_user_id() == "outer-user"
    dispatch.assert_awaited_once()
    assert dispatch.call_args.args[0] == ids["own"]
    with Session(admin) as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.resource_id == ids["own"])).all()
        assert rows, "audit failure must not silently disappear under restricted role"
        assert all(row.user_id == user for row in rows)
        assert "PRIVATE_FILE_SENTINEL" not in str([row.details for row in rows])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["other_user", "other_tenant", "revoked", "offline"])
async def test_restricted_role_denies_unowned_or_unavailable_grants(grants, monkeypatch, name):
    ge, admin, ent, user, ids = grants
    dispatch = AsyncMock()
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner", dispatch)
    with tenant_scope("outer-tenant"), authenticated_user_scope("outer-user"):
        with pytest.raises(PermissionError):
            await ge.grant_executor(operation="read_file", grant_id=ids[name], enterprise_id=ent, user_id=user, relative_path="note.txt", max_bytes=4096)
        assert get_current_enterprise_id() == "outer-tenant"
        assert get_authenticated_user_id() == "outer-user"
    dispatch.assert_not_awaited()
    with Session(admin) as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.resource_id == ids[name])).all()
        assert rows, "denied read must leave audit evidence for the authenticated caller"
        assert all(row.user_id == user for row in rows)
