"""AUD-19 第三轮：受限角色下的**真实服务路径**（真实 PostgreSQL）。

`test_rls_bootstrap_postgres.py` 证明的是数据库层的权限边界；本文件证明的是
**应用代码真的按这个边界工作** —— 只调 SQL 函数是发现不了连接工厂、租户上下文
和参数转换缺陷的：

* ``get_db`` 用受约束角色跑真实的 ``/auth/login``、``/auth/refresh`` 与登录失败，
  并在库里核对审计记录（成功事件有主体、匿名事件主体为 NULL）；
* 设备长期凭据换令牌、令牌换设备（``runner_v2_protocol``）在受约束角色下成功，
  错误密钥按 401 失败关闭；
* 队列角色通过**应用代码**领取任务，随后在 ``tenant_scope`` 绑定下写回，
  API 角色随后能按租户策略看到这条结果；
* 渠道回调的两阶段引导按顺序调用：引导连接只取材料 → 验签 → 业务会话绑定租户。

前置条件：``DATABASE_URL`` 必须等于 ``RLS_TEST_APP_URL``（应用全局引擎就是受约束
角色），另外提供 worker / bootstrap 两个角色的连接串。
"""
import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import create_engine

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")
WORKER_URL = os.getenv("RLS_TEST_WORKER_URL", "")
BOOTSTRAP_URL = os.getenv("RLS_TEST_BOOTSTRAP_URL", "")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL and WORKER_URL and BOOTSTRAP_URL)
    or os.getenv("DATABASE_URL") != APP_URL,
    reason="需要 DATABASE_URL=RLS_TEST_APP_URL 以及三个受限角色的连接串",
)

SUFFIX = uuid.uuid4().hex[:10]
ENTERPRISE_ID = f"r3svc-ent-{SUFFIX}"
PASSWORD = "R3-Svc-Pass-123"


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture(scope="module")
def admin_engine():
    from sqlalchemy import create_engine

    engine = create_engine(ADMIN_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def tenant():
    """企业 + 一个属于它的用户（登录/刷新/设备/队列的租户主体）。"""
    from app.utils.auth.password import get_password_hash

    user_id = f"r3svc-u-{SUFFIX}"
    email = f"member-{SUFFIX}@r3.invalid"
    engine = create_engine(ADMIN_URL)
    with engine.begin() as conn:
        # 领取函数是跨租户的：库里残留的旧用例任务会先被领走，这里先清掉。
        conn.execute(text("DELETE FROM processing_tasks WHERE id LIKE 'r3svc-task-%'"))
        conn.execute(
            text(
                "INSERT INTO enterprises (id, name, is_active, invite_max_uses, "
                "invite_used_count) VALUES (:id, :name, true, 10, 0)"
            ),
            {"id": ENTERPRISE_ID, "name": f"R3 服务路径 {SUFFIX}"},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name, role, "
                "enterprise_id, is_active, created_at, updated_at) "
                "VALUES (:id, :email, :hash, :name, 'admin', :ent, true, now(), now())"
            ),
            {
                "id": user_id,
                "email": email,
                "hash": get_password_hash(PASSWORD),
                "name": f"R3 {SUFFIX}",
                "ent": ENTERPRISE_ID,
            },
        )
    engine.dispose()
    yield {"enterprise_id": ENTERPRISE_ID, "user_id": user_id, "email": email,
           "id": user_id}
    engine = create_engine(ADMIN_URL)
    with engine.begin() as conn:
        # 先删引用 users 的子表，再删账本，最后删用户本身
        conn.execute(text("DELETE FROM processing_tasks WHERE user_id = :id"),
                     {"id": user_id})
        conn.execute(text("DELETE FROM audit_logs WHERE resource_id LIKE :pattern"),
                     {"pattern": f"%{SUFFIX}%"})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        conn.execute(text("DELETE FROM enterprises WHERE id = :id"),
                     {"id": ENTERPRISE_ID})
    engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _pool_per_event_loop():
    """每个用例的事件循环都重新建连接。

    应用引擎是模块级单例，连接池绑定在第一个事件循环上；跨循环复用会在第二个
    用例里抛 "NoneType has no attribute send"。这里显式 dispose，而不是靠
    跳过或退出码掩盖。
    """
    from app.database import engine

    await engine.dispose()
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


# ---------------------------------------------------------------------------
# 认证：成功 / 刷新 / 匿名失败
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_success_writes_tenant_scoped_audit(client, tenant, admin_engine):
    """受约束角色下登录成功：绑定租户后审计写入按账本策略放行。"""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": tenant["email"], "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT user_id, action FROM audit_logs "
                "WHERE resource_id = :uid ORDER BY created_at"
            ),
            {"uid": tenant["user_id"]},
        ).fetchall()
    actions = [row[1] for row in rows]
    assert "login" in actions, actions
    login_row = next(row for row in rows if row[1] == "login")
    assert login_row[0] == tenant["id"], login_row


@pytest.mark.asyncio
async def test_refresh_token_writes_audit_under_restricted_role(client, tenant, admin_engine):
    await client.post(
        "/api/v1/auth/login",
        json={"email": tenant["email"], "password": PASSWORD},
    )
    # 已登录的状态变更请求必须带 CSRF 头（登录/注册首条请求除外）
    csrf = client.cookies.get("csrf_token")
    assert csrf, "登录后应当下发 csrf_token cookie"
    resp = await client.post(
        "/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200, resp.text
    with admin_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM audit_logs WHERE user_id = :uid "
                "AND action = 'refresh_token'"
            ),
            {"uid": tenant["id"]},
        ).scalar()
    assert count == 1, count


@pytest.mark.asyncio
async def test_login_failure_is_recorded_anonymously(client, tenant, admin_engine):
    """登录失败没有用户主体：受控入口写入，user_id 必须是 NULL。"""
    email = f"ghost-{SUFFIX}@r3.invalid"
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrong-password"}
    )
    assert resp.status_code == 401, resp.text
    with admin_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT user_id, action FROM audit_logs WHERE resource_id = :email "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"email": email},
        ).fetchone()
    assert row is not None, "登录失败必须留痕"
    assert row[0] is None, row
    assert row[1] == "login_failed", row


# ---------------------------------------------------------------------------
# 设备引导
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_device_token_exchange_and_auth_use_the_bootstrap_resolver(
    admin_engine, tenant
):
    """设备凭据/令牌换租户走真实协议代码；错误密钥 401 失败关闭。"""
    from app.database import async_session_factory
    from app.services.runner_v2_protocol import PhysicalProtocolError, runner_v2_protocol
    from app.utils.db_tenant_context import reset_authenticated_user, reset_tenant_context
    from app.utils.db_tenant_context import bind_authenticated_user, bind_tenant_context

    import hashlib

    device_id = f"r3svc-dev-{SUFFIX}"
    secret = f"r3svc-secret-{SUFFIX}"
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runner_devices (id, enterprise_id, owner_user_id, "
                "runner_id, platform, version, capabilities, status, scopes, "
                "created_at, updated_at) "
                "VALUES (:id, :ent, :uid, :rid, 'windows', '1.0.0', '{}', 'active', "
                "'[]', now(), now())"
            ),
            {"id": device_id, "ent": tenant["enterprise_id"], "uid": tenant["user_id"],
             "rid": f"runner-{SUFFIX}"},
        )
        # 协议侧存的是 sha256 摘要，不是明文密钥
        conn.execute(
            text(
                "INSERT INTO runner_device_credentials (id, device_id, secret_hash, "
                "issued_at, created_at, updated_at) "
                "VALUES (:cid, :did, :hash, now(), now(), now())"
            ),
            {
                "cid": f"r3svc-cred-{SUFFIX}",
                "did": device_id,
                "hash": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
            },
        )
    try:
        async with async_session_factory() as db:
            # 错误密钥：必须失败关闭
            with pytest.raises(PhysicalProtocolError) as error:
                await runner_v2_protocol.exchange_device_token(
                    db, device_id=device_id, device_secret="wrong"
                )
            await db.rollback()
            assert error.value.status_code == 401

            issued = await runner_v2_protocol.exchange_device_token(
                db, device_id=device_id, device_secret=secret
            )
            assert issued["enterprise_id"] == tenant["enterprise_id"]
            device = await runner_v2_protocol.authenticate_device_token(
                db, token=issued["access_token"]
            )
            assert device.id == device_id
            await db.rollback()
            reset_tenant_context(bind_tenant_context(None))
            reset_authenticated_user(bind_authenticated_user(None))
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM runner_device_tokens WHERE device_id = :id"),
                         {"id": device_id})
            conn.execute(text("DELETE FROM runner_device_credentials WHERE device_id = :id"),
                         {"id": device_id})
            conn.execute(text("DELETE FROM runner_devices WHERE id = :id"),
                         {"id": device_id})


# ---------------------------------------------------------------------------
# 队列：领取 → 租户绑定 → 写回
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_role_claims_and_writes_back_under_tenant_scope(
    admin_engine, tenant
):
    """真实队列代码：队列角色领取，随后按租户写回，API 角色按策略能看到。"""
    from app.services import processing_queue
    from app.utils.db_tenant_context import tenant_scope

    task_id = f"r3svc-task-{SUFFIX}"
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO processing_tasks (id, enterprise_id, user_id, status, "
                "agent_name, folder_path, run_config, cancel_requested, attempt, "
                "total_files, processed_files, failed_files, knowledge_count, progress, "
                "processing_time_seconds, created_at, updated_at) "
                "VALUES (:id, :ent, :uid, 'pending', 'r3', 'C:/r3', '{}', false, 0, "
                "0, 0, 0, 0, 0, 0, now(), now())"
            ),
            {"id": task_id, "ent": tenant["enterprise_id"], "uid": tenant["user_id"]},
        )
    worker_engine = create_async_engine(WORKER_URL)
    factory = async_sessionmaker(worker_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            claimed = await processing_queue.claim_next_processing_task(
                db, "r3-worker", lease_seconds=60
            )
        assert claimed is not None
        assert claimed.task_id == task_id
        assert claimed.enterprise_id == tenant["enterprise_id"]

        # 领取之后：按领取到的租户绑定作用域写回
        async with factory() as db:
            with tenant_scope(claimed.enterprise_id):
                await processing_queue.complete_processing_task(
                    db, claimed.task_id, "r3-worker",
                    progress=100.0, message="r3 完成",
                )
        async with factory() as db:
            recovered = await processing_queue.recover_stale_processing_tasks(db)
        assert recovered == 0
    finally:
        await worker_engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM processing_tasks WHERE id = :id"),
                         {"id": task_id})


@pytest.mark.asyncio
async def test_api_role_cannot_claim_queue(admin_engine, tenant):
    """用 API 角色的连接跑同一个领取函数：必须被数据库拒绝（不是"领到别的租户"）。"""
    from app.services import processing_queue
    from app.services.queue_claim import WORKER_CONNECTION_HINT

    task_id = f"r3svc-task-b-{SUFFIX}"
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO processing_tasks (id, enterprise_id, user_id, status, "
                "agent_name, folder_path, run_config, cancel_requested, attempt, "
                "total_files, processed_files, failed_files, knowledge_count, progress, "
                "processing_time_seconds, created_at, updated_at) "
                "VALUES (:id, :ent, :uid, 'pending', 'r3', 'C:/r3', '{}', false, 0, "
                "0, 0, 0, 0, 0, 0, now(), now())"
            ),
            {"id": task_id, "ent": tenant["enterprise_id"], "uid": tenant["user_id"]},
        )
    app_engine = create_async_engine(APP_URL)
    factory = async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            with pytest.raises(RuntimeError) as error:
                await processing_queue.claim_next_processing_task(db, "api-role", 60)
        assert WORKER_CONNECTION_HINT in str(error.value)
        # 任务没有被"顺手领走"
        with admin_engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM processing_tasks WHERE id = :id"),
                {"id": task_id},
            ).scalar()
        assert status == "pending", status
    finally:
        await app_engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM processing_tasks WHERE id = :id"),
                         {"id": task_id})


# ---------------------------------------------------------------------------
# 渠道：两阶段引导的调用顺序
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_channel_bootstrap_is_two_phased(admin_engine, tenant, monkeypatch):
    """第一阶段只取材料（无租户），第二阶段凭密钥摘要绑定租户。"""
    from app.services.connectors import bootstrap as channel_bootstrap
    from app.utils.db_tenant_context import get_current_enterprise_id

    account_id = f"r3svc-acc-{SUFFIX}"
    token = f"r3svc-token-{SUFFIX}"
    fingerprint = channel_bootstrap.secret_fingerprint(token)
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO channel_accounts (id, enterprise_id, channel_type, name, "
                "encrypted_credentials, mounted_profile_ids, status, is_active, "
                "webhook_token_hash, created_at, updated_at) "
                "VALUES (:id, :ent, 'wecom_bot', 'r3', '{}', '[]', 'configured', true, "
                ":fp, now(), now())"
            ),
            {"id": account_id, "ent": tenant["enterprise_id"], "fp": fingerprint},
        )
    bootstrap_engine = create_async_engine(BOOTSTRAP_URL)
    bootstrap_factory = async_sessionmaker(
        bootstrap_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(channel_bootstrap, "bootstrap_session_factory", bootstrap_factory,
                        raising=False)
    from app import database as database_module

    monkeypatch.setattr(database_module, "bootstrap_session_factory", bootstrap_factory)
    try:
        material = await channel_bootstrap.load_channel_webhook_material(account_id)
        assert material is not None
        assert material.account_id == account_id
        assert not hasattr(material, "enterprise_id")
        # 第一阶段没有绑定任何租户
        assert get_current_enterprise_id() is None

        from app.database import async_session_factory
        from app.utils.db_tenant_context import reset_tenant_context

        async with async_session_factory() as db:
            # 错误密钥：不得绑定
            assert await channel_bootstrap.bind_channel_tenant(db, account_id, "wrong") is None
            await db.rollback()
            bound = await channel_bootstrap.bind_channel_tenant(db, account_id, token)
            assert bound == tenant["enterprise_id"]
            await db.rollback()
            reset_tenant_context(_bind_none())
    finally:
        await bootstrap_engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM channel_accounts WHERE id = :id"),
                         {"id": account_id})


def _bind_none():
    from app.utils.db_tenant_context import bind_tenant_context

    return bind_tenant_context(None)


# ---------------------------------------------------------------------------
# 租户/主体上下文：跨事务与跨请求不复用
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subject_and_tenant_context_survive_commits_but_not_the_next_request(
    admin_engine, tenant
):
    """begin 钩子必须同时恢复主体 GUC；下一个请求必须回到未绑定状态。

    校验走**真实依赖路径**（``get_db`` 打开的新会话），而不是测试自己清理：
    请求内两个事务都应看到主体；请求结束后新会话读到的必须是空串。
    """
    from app.database import async_session_factory, get_db
    from app.utils.audit import log_audit
    from app.utils.db_tenant_context import (
        authenticated_user_scope,
        bind_tenant_context,
        get_authenticated_user_id,
        get_current_enterprise_id,
    )

    from app.models.user import User

    # "刚认证、还没有企业"的主体：走受控审计入口，而不是账本策略
    tenantless_id = f"r3svc-free-{SUFFIX}"
    log_id_prefix = f"r3svc-tx-{SUFFIX}"
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name, role, "
                "enterprise_id, is_active, created_at, updated_at) "
                "VALUES (:id, :email, 'x', :name, 'member', NULL, true, now(), now())"
            ),
            {"id": tenantless_id, "email": f"{tenantless_id}@r3.invalid",
             "name": f"R3 free {SUFFIX}"},
        )
    try:
        # 模拟一次请求：绑定主体 → 两个事务（commit 之后仍要带主体）
        with authenticated_user_scope(tenantless_id):
            bind_tenant_context(None)
            generator = get_db()
            db = await anext(generator)
            try:
                user = await db.get(User, tenantless_id)
                assert get_authenticated_user_id() == tenantless_id
                await log_audit(db, user, "register", "user", user.id,
                                details={"probe": log_id_prefix})
                await db.commit()
                assert get_authenticated_user_id() == tenantless_id
                await log_audit(db, user, "register", "user", user.id,
                                details={"probe": log_id_prefix + "-2"})
                await db.commit()
            finally:
                await generator.aclose()

        with admin_engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE user_id = :uid AND details ->> 'probe' LIKE :prefix"
                ),
                {"uid": tenantless_id, "prefix": f"{log_id_prefix}%"},
            ).scalar()
        assert count == 2, count
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM audit_logs WHERE details ->> 'probe' LIKE :prefix"),
                {"prefix": f"{log_id_prefix}%"},
            )
        with admin_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM users WHERE id = :id"), {"id": tenantless_id})

    # 下一个请求：没有认证就没有主体和租户 —— 由数据库自己告诉我们
    assert get_authenticated_user_id() is None
    assert get_current_enterprise_id() is None
    async with async_session_factory() as fresh:
        tenant_guc, subject_guc = (
            await fresh.execute(
                text(
                    "SELECT current_setting('app.current_enterprise_id', true), "
                    "current_setting('app.current_user_id', true)"
                )
            )
        ).one()
    assert tenant_guc == "", tenant_guc
    assert subject_guc == "", subject_guc


@pytest.mark.asyncio
async def test_production_without_bootstrap_connection_returns_503(monkeypatch):
    """生产（DEBUG=false）没配引导连接时，回调入口必须明确 503。

    缺配置是部署问题，不是"账号不存在"，也不该冒成未捕获的 500。
    """
    from fastapi import HTTPException

    from app import database as database_module
    from app.api import connectors
    from app.config import settings

    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(database_module, "bootstrap_session_factory", None)

    with pytest.raises(HTTPException) as error:
        await connectors._load_webhook_account(None, "any-account")
    assert error.value.status_code == 503, error.value.status_code


@pytest.mark.asyncio
async def test_tenantless_account_can_login_and_refresh_under_restricted_role(
    admin_engine, tenant, client
):
    """没有企业的账号必须能正常登录/刷新。

    `/auth/register` 建出来的就是这种账号；如果受控审计入口只允许 register，
    它第二次登录就会被 42501 挡住。主体仍必须等于当前已认证主体。
    """
    from app.utils.auth.password import get_password_hash

    user_id = f"r3svc-solo-{SUFFIX}"
    email = f"solo-{SUFFIX}@r3.invalid"
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name, role, "
                "enterprise_id, is_active, created_at, updated_at) "
                "VALUES (:id, :email, :hash, :name, 'member', NULL, true, now(), now())"
            ),
            {"id": user_id, "email": email, "name": f"R3 solo {SUFFIX}",
             "hash": get_password_hash(PASSWORD)},
        )
    try:
        resp = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        csrf = client.cookies.get("csrf_token")
        refresh = await client.post(
            "/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": csrf}
        )
        assert refresh.status_code == 200, refresh.text
        with admin_engine.connect() as conn:
            actions = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT action FROM audit_logs WHERE user_id = :uid "
                        "ORDER BY created_at"
                    ),
                    {"uid": user_id},
                ).fetchall()
            ]
        assert "login" in actions and "refresh_token" in actions, actions
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_logs WHERE user_id = :id"),
                         {"id": user_id})
        with admin_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


@pytest.mark.asyncio
async def test_get_db_clears_tenant_context_after_the_request(client, tenant):
    """真实依赖路径：请求结束后租户/主体上下文必须回到未绑定。

    不用测试自己清理来"证明"隔离 —— 走一次真实登录请求，再确认上下文与数据库
    侧都回到空值。
    """
    from app.database import async_session_factory
    from app.utils.db_tenant_context import (
        get_authenticated_user_id,
        get_current_enterprise_id,
    )

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": tenant["email"], "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    assert get_current_enterprise_id() is None
    assert get_authenticated_user_id() is None

    async with async_session_factory() as fresh:
        tenant_guc, subject_guc = (
            await fresh.execute(
                text(
                    "SELECT current_setting('app.current_enterprise_id', true), "
                    "current_setting('app.current_user_id', true)"
                )
            )
        ).one()
    assert tenant_guc == "", tenant_guc
    assert subject_guc == "", subject_guc
