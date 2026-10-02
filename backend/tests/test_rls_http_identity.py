"""AUD-19：应用身份 → 租户上下文 → 数据库策略的端到端链路（真实 PostgreSQL）。

策略和授权都验证过之后，剩下的问题是"应用到底会不会把租户带进去"。这里用
**受约束运行角色**跑真实的 ``get_current_user`` 与 HTTP 请求，证明：

* 认证成功后数据库里的 ``app.current_enterprise_id`` 就是该用户的企业；
* HTTP 列表接口只返回本租户的行；
* 同一物理连接先后服务两个租户、以及"无租户"请求时，不会串租户。

需要 ``DATABASE_URL`` 等于 ``RLS_TEST_APP_URL``（应用全局引擎必须连受约束角色），
普通 SQLite 单测环境自动跳过。运行顺序：本文件应在 test_rls_forward_repair.py 与
test_rls_postgres_matrix.py 之后执行，避免与它们的 schema 操作互相干扰。
"""
import os
import uuid

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from app.database import async_session_factory, engine
from app.main import app
from app.utils.auth.deps import get_current_user
from app.utils.auth.tokens import create_access_token
from app.utils.db_tenant_context import tenant_scope

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL or os.getenv("DATABASE_URL") != APP_URL,
    reason="需要 DATABASE_URL=RLS_TEST_APP_URL 的受约束运行角色连接串",
)


@pytest.fixture
def identity():
    """租户 A 及其下的一个用户、两个 Agent（管理员写入，运行角色只读）。"""
    suffix = uuid.uuid4().hex[:12]
    enterprise = f"rls3-a-{suffix}"
    user_id = f"rls3-u-{suffix}"
    agents = [f"rls3-agent-{i}-{suffix}" for i in range(2)]
    admin = create_engine(ADMIN_URL)
    with admin.begin() as conn:
        conn.execute(text(
            "INSERT INTO enterprises (id,name,is_active,invite_max_uses,invite_used_count) "
            "VALUES (:id,:name,true,10,0)"
        ), {"id": enterprise, "name": enterprise})
        conn.execute(text(
            "INSERT INTO users (id,email,password_hash,name,role,enterprise_id,is_active) "
            "VALUES (:id,:email,'unused','RLS HTTP','member',:ent,true)"
        ), {"id": user_id, "email": f"{user_id}@rls.invalid", "ent": enterprise})
        for agent_id in agents:
            conn.execute(text(
                "INSERT INTO agents (id,enterprise_id,name,version,file_count,knowledge_count,"
                "status,created_at) VALUES (:id,:ent,:name,'1.0.0',0,0,'ready',now())"
            ), {"id": agent_id, "ent": enterprise, "name": agent_id})
    yield {"enterprise": enterprise, "user": user_id, "agents": agents}
    with admin.begin() as conn:
        for agent_id in agents:
            conn.execute(text("DELETE FROM agents WHERE id=:id"), {"id": agent_id})
        conn.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
        conn.execute(text("DELETE FROM enterprises WHERE id=:id"), {"id": enterprise})
    admin.dispose()


@pytest.mark.asyncio
async def test_authenticated_identity_binds_tenant_for_database(identity):
    """真实 get_current_user：令牌里的用户决定数据库看到的租户。"""
    token = create_access_token({"sub": identity["user"]})
    request = Request({"type": "http", "headers": [], "path": "/"})
    with tenant_scope(None):
        async with async_session_factory() as session:
            user = await get_current_user(
                request,
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
                session,
            )
            assert user.enterprise_id == identity["enterprise"]
            assert (await session.execute(text(
                "SELECT current_setting('app.current_enterprise_id', true)"
            ))).scalar_one() == identity["enterprise"]
            assert (await session.execute(
                text("SELECT count(*) FROM agents")
            )).scalar_one() == len(identity["agents"])
    await engine.dispose()


@pytest.mark.asyncio
async def test_pooled_connection_switches_tenant_and_never_leaks(identity):
    """同一物理连接先服务 A、再服务"无租户"，不能把 A 的租户带给下一个请求。"""
    seen_pids = []
    async with engine.connect() as conn:
        with tenant_scope(identity["enterprise"]):
            seen_pids.append(
                (await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            )
            assert (await conn.execute(text(
                "SELECT current_setting('app.current_enterprise_id', true)"
            ))).scalar_one() == identity["enterprise"]
            assert (await conn.execute(
                text("SELECT count(*) FROM agents")
            )).scalar_one() == len(identity["agents"])
            await conn.commit()
        with tenant_scope(None):
            # 未认证上下文写空串，策略据此拒绝所有行。
            assert (await conn.execute(text(
                "SELECT current_setting('app.current_enterprise_id', true)"
            ))).scalar_one() == ""
            assert (await conn.execute(
                text("SELECT count(*) FROM agents")
            )).scalar_one() == 0
            await conn.commit()
        with tenant_scope(identity["enterprise"]):
            seen_pids.append(
                (await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            )
            await conn.commit()
    assert seen_pids[0] == seen_pids[1], "连接池应复用同一物理连接"
    await engine.dispose()


@pytest.mark.asyncio
async def test_http_agents_endpoint_returns_only_own_tenant(identity):
    """HTTP 身份链路：列表接口在受约束角色下只返回本租户的 Agent。"""
    token = create_access_token({"sub": identity["user"]})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/agents", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["data"]}
    assert ids == set(identity["agents"]), ids
    await engine.dispose()
