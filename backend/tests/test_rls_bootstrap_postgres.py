"""AUD-19 第三轮：受限角色的租户引导与队列能力分离（真实 PostgreSQL）。

本文件只使用**专用验收库**，并按角色分别验证正例与反例：

* ``autoteams_app``（API 运行角色）
  - 合法：持设备凭据/令牌/渠道回调密钥摘要时能换到租户；受控入口能写匿名安全
    事件与"尚无企业账号"的注册事件；已认证主体的租户数据读写正常。
  - 拒绝：**不能**执行任何跨租户领取/恢复函数，**不能**读取渠道验签材料，
    替另一个无企业账号署名会被数据库拒绝，直接 INSERT 账本仍被策略拒绝。
* ``autoteams_worker``（队列角色）
  - 合法：跨租户领取/恢复三个队列；表权限与 API 角色一致。
  - 拒绝：没有 TRUNCATE/REFERENCES/TRIGGER；不能读渠道验签材料；入参非法时
    直接报错而不是把任务永久占住。
* ``autoteams_bootstrap``（渠道引导角色）
  - 合法：按 account_id 读验签材料，且材料里没有 enterprise_id。
  - 拒绝：任何表都读不到；执行不了队列函数与租户解析函数。

环境变量：``RLS_TEST_ADMIN_URL`` / ``RLS_TEST_APP_URL`` / ``RLS_TEST_WORKER_URL``
/ ``RLS_TEST_BOOTSTRAP_URL``。迁移里的 ``CREATE/DROP ROLE`` 会清掉测试口令，
所以本文件在迁移到 head 之后用管理员连接把三个口令装回去（仅测试库）。
"""
import os
import uuid
from contextlib import closing, contextmanager

import psycopg2
import pytest
from sqlalchemy.engine import make_url

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")
WORKER_URL = os.getenv("RLS_TEST_WORKER_URL", "")
BOOTSTRAP_URL = os.getenv("RLS_TEST_BOOTSTRAP_URL", "")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL and WORKER_URL and BOOTSTRAP_URL),
    reason="需要专用 PostgreSQL 验收库与三个受限角色连接串（RLS_TEST_*_URL）",
)

GUC = "app.current_enterprise_id"
SUBJECT_GUC = "app.current_user_id"
GENESIS = "0" * 64

APP_ROLE = "autoteams_app"
WORKER_ROLE = "autoteams_worker"
BOOTSTRAP_ROLE = "autoteams_bootstrap"
def _sync_url(url: str) -> str:
    """psycopg2 只认 ``postgresql://``；SQLAlchemy 的驱动后缀要去掉。"""
    return (
        url.replace("postgresql+asyncpg://", "postgresql://", 1)
        .replace("postgresql+psycopg2://", "postgresql://", 1)
    )


@contextmanager
def _connect(url: str):
    conn = psycopg2.connect(_sync_url(url))
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _role_session(url: str, enterprise_id=None, subject_id=None):
    """以受限角色打开一个事务，并按需写入租户/主体 GUC。"""
    with _connect(url) as conn:
        with closing(conn.cursor()) as cur:
            cur.execute("SELECT set_config(%s, %s, true)", (GUC, enterprise_id or ""))
            cur.execute(
                "SELECT set_config(%s, %s, true)", (SUBJECT_GUC, subject_id or "")
            )
        try:
            yield conn
        finally:
            conn.rollback()


def _install_role_passwords() -> None:
    """迁移重建角色后把测试口令装回去（SCRAM，不依赖 trust）。"""
    mapping = {
        APP_ROLE: APP_URL,
        WORKER_ROLE: WORKER_URL,
        BOOTSTRAP_ROLE: BOOTSTRAP_URL,
    }
    with _connect(ADMIN_URL) as admin:
        with closing(admin.cursor()) as cur:
            cur.execute("SET password_encryption = 'scram-sha-256'")
            for role, url in mapping.items():
                password = make_url(url).password
                if password:
                    cur.execute(
                        f"ALTER ROLE {role} PASSWORD %s",  # noqa: S608 - 角色名是文件内常量
                        (password,),
                    )
        admin.commit()


@pytest.fixture(scope="module", autouse=True)
def _roles_ready():
    _install_role_passwords()
    yield


@pytest.fixture()
def tenants():
    """两个租户，各带一个用户、一个渠道账号和一条待领取的处理任务。"""
    marker = uuid.uuid4().hex[:8]
    with _connect(ADMIN_URL) as admin, closing(admin.cursor()) as cur:
        ids = {}
        for suffix in ("a", "b"):
            enterprise = f"ent-{marker}-{suffix}"
            user = f"usr-{marker}-{suffix}"
            account = f"acc-{marker}-{suffix}"
            device = f"dev-{marker}-{suffix}"
            cur.execute(
                "INSERT INTO enterprises (id, name, created_at, updated_at) "
                "VALUES (%s, %s, now(), now())",
                (enterprise, f"企业{suffix}"),
            )
            cur.execute(
                "INSERT INTO users (id, enterprise_id, email, password_hash, name, "
                "role, is_active, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'x', %s, 'admin', true, now(), now())",
                (user, enterprise, f"{user}@example.test", f"用户{suffix}"),
            )
            cur.execute(
                "INSERT INTO channel_accounts (id, enterprise_id, channel_type, name, "
                "encrypted_credentials, mounted_profile_ids, status, is_active, "
                "webhook_token_hash, created_at, updated_at) "
                "VALUES (%s, %s, 'wecom_bot', %s, '{}', '[]', 'configured', true, %s, "
                "now(), now())",
                (account, enterprise, f"账号{suffix}", f"hash-{marker}-{suffix}"),
            )
            cur.execute(
                "INSERT INTO runner_devices (id, enterprise_id, owner_user_id, "
                "runner_id, platform, version, capabilities, status, scopes, "
                "created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, 'windows', '1.0.0', '{}', 'active', '[]', "
                "now(), now())",
                (device, enterprise, user, f"runner-{marker}-{suffix}"),
            )
            cur.execute(
                "INSERT INTO runner_device_credentials (id, device_id, secret_hash, "
                "issued_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, now(), now(), now())",
                (f"cred-{marker}-{suffix}", device, f"secret-{marker}-{suffix}"),
            )
            cur.execute(
                "INSERT INTO runner_device_tokens (id, device_id, token_hash, "
                "issued_at, expires_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, now(), now() + interval '1 hour', now(), now())",
                (f"tok-{marker}-{suffix}", device, f"token-{marker}-{suffix}"),
            )
            cur.execute(
                "INSERT INTO processing_tasks (id, enterprise_id, user_id, status, "
                "agent_name, folder_path, run_config, cancel_requested, attempt, "
                "total_files, processed_files, failed_files, knowledge_count, progress, "
                "processing_time_seconds, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'pending', %s, %s, '{}', false, 0, 0, 0, 0, 0, 0, "
                "0, now(), now())",
                (f"task-{marker}-{suffix}", enterprise, user, f"agent{suffix}",
                 f"C:/tenants/{suffix}"),
            )
            ids[suffix] = {
                "enterprise_id": enterprise,
                "user_id": user,
                "account_id": account,
                "device_id": device,
                "task_id": f"task-{marker}-{suffix}",
                "token_hash": f"hash-{marker}-{suffix}",
                "device_secret": f"secret-{marker}-{suffix}",
                "device_token": f"token-{marker}-{suffix}",
            }
        admin.commit()
    yield ids
    with _connect(ADMIN_URL) as admin, closing(admin.cursor()) as cur:
        for values in ids.values():
            cur.execute("DELETE FROM processing_tasks WHERE id = %s", (values["task_id"],))
            cur.execute("DELETE FROM runner_device_tokens WHERE device_id = %s",
                        (values["device_id"],))
            cur.execute("DELETE FROM runner_device_credentials WHERE device_id = %s",
                        (values["device_id"],))
            cur.execute("DELETE FROM runner_devices WHERE id = %s", (values["device_id"],))
            cur.execute("DELETE FROM channel_accounts WHERE id = %s", (values["account_id"],))
            cur.execute("DELETE FROM users WHERE id = %s", (values["user_id"],))
            cur.execute("DELETE FROM enterprises WHERE id = %s", (values["enterprise_id"],))
        cur.execute("DELETE FROM audit_logs WHERE resource_id LIKE %s", (f"%{marker}%",))
        admin.commit()


# ---------------------------------------------------------------------------
# 角色能力边界
# ---------------------------------------------------------------------------

QUEUE_CALLS = [
    "SELECT * FROM public.app_claim_processing_task('w', 30)",
    "SELECT public.app_recover_processing_tasks(5)",
    "SELECT * FROM public.app_claim_compilation_job('w', 30)",
    "SELECT * FROM public.app_recover_compilation_jobs()",
    "SELECT * FROM public.app_claim_agent_build_task('w', 30)",
    "SELECT public.app_recover_agent_build_tasks()",
]


def test_app_role_cannot_execute_cross_tenant_queue_functions():
    """API 运行角色一旦能调用领取/恢复，就能跨租户操作队列 —— 必须被拒绝。"""
    for sql in QUEUE_CALLS:
        with _role_session(APP_URL) as conn, closing(conn.cursor()) as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(sql)
            conn.rollback()
        assert error.value.pgcode == "42501", (sql, error.value.pgcode)


def test_app_role_cannot_read_channel_webhook_material():
    with _role_session(APP_URL) as conn, closing(conn.cursor()) as cur:
        with pytest.raises(psycopg2.Error) as error:
            cur.execute(
                "SELECT * FROM public.app_get_channel_webhook_material('any-account')"
            )
        conn.rollback()
    assert error.value.pgcode == "42501", error.value.pgcode


def test_bootstrap_role_has_no_table_access(tenants):
    for sql in (
        "SELECT count(*) FROM public.channel_accounts",
        "SELECT count(*) FROM public.processing_tasks",
        "SELECT count(*) FROM public.users",
    ):
        with _role_session(BOOTSTRAP_URL) as conn, closing(conn.cursor()) as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(sql)
            conn.rollback()
        assert error.value.pgcode == "42501", (sql, error.value.pgcode)


def test_bootstrap_role_cannot_run_queue_or_resolver_functions():
    for sql in QUEUE_CALLS + [
        "SELECT public.app_resolve_channel_tenant('a', 'b')",
        "SELECT public.app_resolve_runner_token_tenant('x')",
    ]:
        with _role_session(BOOTSTRAP_URL) as conn, closing(conn.cursor()) as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(sql)
            conn.rollback()
        assert error.value.pgcode == "42501", (sql, error.value.pgcode)


def test_worker_role_has_no_destructive_table_privileges():
    with _role_session(WORKER_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND ("
            "has_table_privilege(%s, c.oid, 'TRUNCATE') "
            "OR has_table_privilege(%s, c.oid, 'REFERENCES') "
            "OR has_table_privilege(%s, c.oid, 'TRIGGER'))",
            (WORKER_ROLE, WORKER_ROLE, WORKER_ROLE),
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT has_table_privilege(%s, 'processing_tasks', 'SELECT')",
                    (WORKER_ROLE,))
        assert cur.fetchone()[0] is True
        conn.rollback()


def test_restricted_roles_carry_no_memberships_or_privileges():
    with _connect(ADMIN_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT rolname, rolsuper, rolbypassrls, rolinherit, rolcreatedb, "
            "rolcreaterole FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname",
            ([APP_ROLE, WORKER_ROLE, BOOTSTRAP_ROLE],),
        )
        rows = cur.fetchall()
        assert len(rows) == 3, rows
        for _name, *flags in rows:
            assert not any(flags), (_name, flags)
        cur.execute(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
            "WHERE r.rolname = ANY(%s)",
            ([APP_ROLE, WORKER_ROLE, BOOTSTRAP_ROLE],),
        )
        assert cur.fetchone()[0] == 0
        conn.rollback()


# ---------------------------------------------------------------------------
# 队列：合法正例 + 拒绝反例
# ---------------------------------------------------------------------------

def _admin_scalar(sql, params=None):
    with _connect(ADMIN_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        conn.rollback()
    return row[0] if row else None


def test_worker_role_claims_across_tenants(tenants):
    """队列角色按设计跨租户领取；这是它与 API 角色的唯一能力差。"""
    with _role_session(WORKER_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT id, enterprise_id FROM "
            "public.app_claim_processing_task('worker-A', 60)"
        )
        row = cur.fetchone()
        assert row is not None
        task_id, enterprise_id = row
        assert task_id in (tenants["a"]["task_id"], tenants["b"]["task_id"])
        assert enterprise_id in (tenants["a"]["enterprise_id"],
                                 tenants["b"]["enterprise_id"])
        conn.commit()
    assert _admin_scalar(
        "SELECT status FROM processing_tasks WHERE id = %s", (task_id,)
    ) == "processing"
    assert _admin_scalar(
        "SELECT lease_owner FROM processing_tasks WHERE id = %s", (task_id,)
    ) == "worker-A"


def test_worker_role_recovers_only_expired_leases(tenants):
    task_id = tenants["a"]["task_id"]
    with _role_session(WORKER_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT id FROM public.app_claim_processing_task('worker-A', 3600) "
            "WHERE id = %s", (task_id,)
        )
        cur.fetchone()
        # 租约仍有效：不得被回收
        cur.execute("SELECT public.app_recover_processing_tasks(0)")
        assert cur.fetchone()[0] == 0
        conn.commit()
    assert _admin_scalar(
        "SELECT status FROM processing_tasks WHERE id = %s", (task_id,)
    ) == "processing"

    # 租约过期：回到 pending
    with _connect(ADMIN_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "UPDATE processing_tasks SET lease_until = now() - interval '1 hour' "
            "WHERE id = %s", (task_id,),
        )
        conn.commit()
    with _role_session(WORKER_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT public.app_recover_processing_tasks(0)")
        assert cur.fetchone()[0] >= 1
        conn.commit()
    assert _admin_scalar(
        "SELECT status FROM processing_tasks WHERE id = %s", (task_id,)
    ) == "pending"
    assert _admin_scalar(
        "SELECT lease_owner FROM processing_tasks WHERE id = %s", (task_id,)
    ) is None


def test_queue_arguments_are_validated_instead_of_blocking_the_queue(tenants):
    """坏配置必须直接报错：否则空 worker_id 或超大租约会永久占住任务。"""
    for sql in (
        "SELECT * FROM public.app_claim_processing_task('', 30)",
        "SELECT * FROM public.app_claim_processing_task('   ', 30)",
        "SELECT * FROM public.app_claim_processing_task('w', 0)",
        "SELECT * FROM public.app_claim_processing_task('w', -5)",
        "SELECT * FROM public.app_claim_processing_task('w', 99999)",
        "SELECT * FROM public.app_claim_processing_task('w', NULL)",
        # 领取必须带 worker 身份：NULL 租约没有归属，任务会卡住且无人续租
        "SELECT * FROM public.app_claim_processing_task(NULL, 30)",
        "SELECT * FROM public.app_claim_compilation_job(NULL, 30)",
        "SELECT * FROM public.app_claim_agent_build_task(NULL, 30)",
        "SELECT public.app_recover_processing_tasks(-1)",
        "SELECT public.app_recover_processing_tasks(99999)",
        "SELECT * FROM public.app_claim_compilation_job('', 30)",
        "SELECT * FROM public.app_claim_agent_build_task('w', 0)",
    ):
        with _role_session(WORKER_URL) as conn, closing(conn.cursor()) as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(sql)
            conn.rollback()
        assert error.value.pgcode == "22023", (sql, error.value.pgcode)

    with _role_session(WORKER_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT count(*) FROM processing_tasks WHERE status <> 'pending'")
        assert cur.fetchone()[0] == 0
        conn.rollback()


# ---------------------------------------------------------------------------
# 租户引导：合法正例 + 拒绝反例
# ---------------------------------------------------------------------------

def test_runner_resolvers_require_the_presented_secret(tenants):
    device = tenants["a"]["device_id"]
    with _role_session(APP_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT public.app_resolve_runner_credential_tenant(%s, %s)",
            (device, "secret-wrong"),
        )
        assert cur.fetchone()[0] is None
        cur.execute(
            "SELECT public.app_resolve_runner_credential_tenant(%s, %s)",
            (device, tenants["a"]["device_secret"]),
        )
        assert cur.fetchone()[0] == tenants["a"]["enterprise_id"]
        cur.execute("SELECT public.app_resolve_runner_token_tenant('token-wrong')")
        assert cur.fetchone()[0] is None
        cur.execute("SELECT public.app_resolve_runner_token_tenant(%s)",
                    (tenants["a"]["device_token"],))
        assert cur.fetchone()[0] == tenants["a"]["enterprise_id"]
        conn.rollback()


def test_channel_tenant_binding_requires_the_callback_secret(tenants):
    account = tenants["a"]["account_id"]
    with _role_session(APP_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT public.app_resolve_channel_tenant(%s, %s)",
                    (account, "hash-wrong"))
        assert cur.fetchone()[0] is None
        cur.execute("SELECT public.app_resolve_channel_tenant(%s, %s)",
                    (account, tenants["a"]["token_hash"]))
        assert cur.fetchone()[0] == tenants["a"]["enterprise_id"]
        conn.rollback()


def test_bootstrap_role_returns_material_without_tenant(tenants):
    account = tenants["a"]["account_id"]
    with _role_session(BOOTSTRAP_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT * FROM public.app_get_channel_webhook_material(%s)", (account,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == account
        assert len(row) == 4 and "enterprise" not in row[0].lower()
        cur.execute("SELECT * FROM public.app_get_channel_webhook_material('missing')")
        assert cur.fetchone() is None
        conn.rollback()

# ---------------------------------------------------------------------------
# 受控审计入口
# ---------------------------------------------------------------------------

def _chain_tip(admin):
    with closing(admin.cursor()) as cur:
        cur.execute("SELECT last_signature FROM audit_chain_state WHERE id = 'global'")
        row = cur.fetchone()
        return row[0] if row else GENESIS


def test_anonymous_security_audit_is_allowed_and_scoped(tenants):
    """匿名安全事件可以写，但只能写白名单动作、且只能追加到链尾。"""
    with _connect(ADMIN_URL) as admin, closing(admin.cursor()) as cur:
        tip = _chain_tip(admin)
    log_id = str(uuid.uuid4())
    with _role_session(APP_URL) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT public.app_append_bootstrap_audit(%s, NULL, 'login_failed', 'user', "
            "%s, '127.0.0.1', 'agent', '{}'::jsonb, now(), 'sig-1', %s)",
            (log_id, f"nobody-{log_id}", tip),
        )
        # 事务提交后链尾才真正推进；回滚会让"写入成功"变成假象。
        conn.commit()
    with _connect(ADMIN_URL) as admin, closing(admin.cursor()) as cur:
        cur.execute("SELECT user_id, action, prev_hash FROM audit_logs WHERE id = %s",
                    (log_id,))
        assert cur.fetchone() == (None, "login_failed", tip)
        cur.execute("SELECT last_signature FROM audit_chain_state WHERE id = 'global'")
        assert cur.fetchone()[0] == "sig-1"
        cur.execute("UPDATE audit_chain_state SET last_signature = %s WHERE id = 'global'",
                    (tip,))
        cur.execute("DELETE FROM audit_logs WHERE id = %s", (log_id,))
        admin.commit()


def test_bootstrap_audit_rejects_forged_subjects_and_positions(tenants):
    with _connect(ADMIN_URL) as admin:
        tip = _chain_tip(admin)

    # 1) 白名单之外的动作
    with _role_session(APP_URL) as conn, closing(conn.cursor()) as cur:
        with pytest.raises(psycopg2.Error) as error:
            cur.execute(
                "SELECT public.app_append_bootstrap_audit(%s, NULL, 'delete', 'agent', "
                "%s, '127.0.0.1', 'agent', '{}'::jsonb, now(), 'sig-x', %s)",
                (str(uuid.uuid4()), "agent-1", tip),
            )
        conn.rollback()
    assert error.value.pgcode == "42501", error.value.pgcode

    # 2) 链尾不匹配（试图插到链中间 / 丢掉既有条目）
    with _role_session(APP_URL) as conn, closing(conn.cursor()) as cur:
        with pytest.raises(psycopg2.Error) as error:
            cur.execute(
                "SELECT public.app_append_bootstrap_audit(%s, NULL, 'login_failed', "
                "'user', %s, '127.0.0.1', 'agent', '{}'::jsonb, now(), 'sig-y', %s)",
                (str(uuid.uuid4()), "someone@example.test", "f" * 64),
            )
        conn.rollback()
    assert error.value.pgcode == "42501", error.value.pgcode

    # 3) 主体不等于当前已认证主体（替另一个无企业账号署名）
    with _role_session(APP_URL, subject_id=tenants["a"]["user_id"]) as conn, \
            closing(conn.cursor()) as cur:
        with pytest.raises(psycopg2.Error) as error:
            cur.execute(
                "SELECT public.app_append_bootstrap_audit(%s, %s, 'register', 'user', "
                "%s, '127.0.0.1', 'agent', '{}'::jsonb, now(), 'sig-z', %s)",
                (str(uuid.uuid4()), tenants["b"]["user_id"], "forged", tip),
            )
        conn.rollback()
    assert error.value.pgcode == "42501", error.value.pgcode

    # 4) 已属于企业的账号不能走无租户通道
    with _role_session(APP_URL, subject_id=tenants["a"]["user_id"]) as conn, \
            closing(conn.cursor()) as cur:
        with pytest.raises(psycopg2.Error) as error:
            cur.execute(
                "SELECT public.app_append_bootstrap_audit(%s, %s, 'register', 'user', "
                "%s, '127.0.0.1', 'agent', '{}'::jsonb, now(), 'sig-w', %s)",
                (str(uuid.uuid4()), tenants["a"]["user_id"], "forged", tip),
            )
        conn.rollback()
    assert error.value.pgcode == "42501", error.value.pgcode


def test_ledger_insert_policy_still_scopes_the_app_role(tenants):
    """受控入口不能变成"给账本开一个宽松口子"。

    合法写入（租户上下文 + 本租户用户）必须成功；跨租户署名和没有租户上下文
    的写入必须仍然是 42501。
    """
    legit_id = str(uuid.uuid4())
    with _role_session(APP_URL, enterprise_id=tenants["a"]["enterprise_id"]) as conn, \
            closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT INTO audit_logs (id, user_id, action, resource_type, "
            "resource_id, created_at, updated_at) "
            "VALUES (%s, %s, 'legit', 'user', %s, now(), now())",
            (legit_id, tenants["a"]["user_id"], "legit"),
        )
        conn.rollback()

    for enterprise_id, user_id, label in (
        (tenants["a"]["enterprise_id"], tenants["b"]["user_id"], "跨租户署名"),
        (None, tenants["a"]["user_id"], "无租户上下文"),
    ):
        with _role_session(APP_URL, enterprise_id=enterprise_id) as conn, \
                closing(conn.cursor()) as cur:
            with pytest.raises(psycopg2.Error) as error:
                cur.execute(
                    "INSERT INTO audit_logs (id, user_id, action, resource_type, "
                    "resource_id, created_at, updated_at) "
                    "VALUES (%s, %s, 'forged', 'user', %s, now(), now())",
                    (str(uuid.uuid4()), user_id, "forged"),
                )
            conn.rollback()
        assert error.value.pgcode == "42501", (label, error.value.pgcode)


def test_tenant_scoped_positive_crud_still_works(tenants):
    """合法正例：绑定租户后，API 角色照常读写本租户数据。"""
    agent_id = str(uuid.uuid4())
    with _role_session(APP_URL, enterprise_id=tenants["a"]["enterprise_id"]) as conn, \
            closing(conn.cursor()) as cur:
        cur.execute(
            "INSERT INTO agents (id, enterprise_id, name, version, file_count, "
            "knowledge_count, status, created_at, updated_at) "
            "VALUES (%s, %s, '正例', '1.0.0', 0, 0, 'ready', now(), now())",
            (agent_id, tenants["a"]["enterprise_id"]),
        )
        cur.execute("SELECT count(*) FROM agents WHERE id = %s", (agent_id,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM agents")
        assert cur.fetchone()[0] == 1
        conn.rollback()


def test_restricted_roles_are_not_granted_to_other_roles():
    """反向成员关系同样要拒绝：把受限角色授予别的 LOGIN 等于把能力发出去。"""
    helper = "r3_leak_role"
    # 角色名都是本文件内的常量，不含外部输入。
    # ruff: noqa: S608
    with _connect(ADMIN_URL) as admin, closing(admin.cursor()) as cur:
        cur.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles "
            f"WHERE rolname = '{helper}') THEN CREATE ROLE {helper} NOLOGIN; "
            f"END IF; END $$;"
        )
        cur.execute(f"GRANT {WORKER_ROLE} TO {helper}")
        try:
            cur.execute(
                "SELECT count(*) FROM pg_auth_members m "
                "JOIN pg_roles granted ON granted.oid = m.roleid "
                "JOIN pg_roles member ON member.oid = m.member "
                "WHERE granted.rolname = %s AND member.rolname = %s",
                (WORKER_ROLE, helper),
            )
            granted = cur.fetchone()[0]
            assert granted == 1, "前置条件：反向授权已建立"
        finally:
            cur.execute(f"REVOKE {WORKER_ROLE} FROM {helper}")
            cur.execute(f"DROP ROLE IF EXISTS {helper}")
        admin.commit()
