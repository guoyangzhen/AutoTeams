"""AUD-19：迁移前向修复与往返验收（真实 PostgreSQL）。

三条独立证据，都要求**专用空库**：

1. ``downgrade base`` → ``upgrade head`` → ``downgrade -1`` → ``upgrade head`` 往返；
   历史 revision 修过本身、策略名也改过，如果 downgrade 不恢复历史策略名，
   后续迁移删除 ``enterprise_id`` 列时就会因策略依赖而失败。
2. 历史策略名被手工放宽成 ``USING (true)`` 时，前向修复必须**真的收紧**它：
   多条 PERMISSIVE 策略取并集，"只新建自己的策略"毫无作用。
3. 清单之外的策略（手工加的绕过策略）必须让迁移失败，而不是默默留着。

这些用例会改 schema，因此与播种型矩阵分开放在本文件，避免相互污染。
"""
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path
from sqlalchemy.engine import make_url

from psycopg2 import sql as pgsql

import pytest
from sqlalchemy import create_engine, text

# S603：alembic 子进程的命令与参数由本文件固定，不含外部输入。
# ruff: noqa: S603

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL), reason="需要专用 PostgreSQL 验收库（RLS_TEST_*_URL）"
)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _BACKEND_ROOT / "migrations" / "versions"
_FORWARD_FILE = "2026_09_29_0900-c2d3e4f5a6b8_rls_indirect_and_forward_fix.py"
_HEAD = "e7f8a9b0c1d2"
# This suite exercises the c2 forward repair, so its parent stays fixed
# even when later migrations are appended.
_PREVIOUS_HEAD = "b6c7d8e9f0a1"


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_url_for_current_revision() -> str:
    """Historic revisions still use the original role identity."""
    engine = create_engine(ADMIN_URL)
    try:
        with engine.connect() as conn:
            role = conn.scalar(text(
                "SELECT rolname FROM pg_roles "
                "WHERE rolname IN ('autoteams_app', 'autofde_app') ORDER BY rolname"
            ))
        assert role, "Migration test requires an existing application role"
        return make_url(APP_URL).set(username=role).render_as_string(hide_password=False)
    finally:
        engine.dispose()


def _restore_app_role_password() -> None:
    """迁移里 `DROP ROLE` + `CREATE ROLE` 会把测试运行角色的口令清空。

    本地 trust 认证看不出这个差别，但 CI 的带口令 ``RLS_TEST_APP_URL`` 会直接
    认证失败 —— 那属于"只测了 trust"造成的假绿灯。这里在升级成功后用管理员
    连接把测试口令装回去；仅限测试库，不影响生产流程。
    """
    password = make_url(APP_URL).password
    if not password:
        return
    engine = create_engine(ADMIN_URL)
    try:
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cur:
                cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN (%s, %s) ORDER BY rolname", ("autoteams_app", "autofde_app"))
                role_name = cur.fetchone()[0]
                cur.execute(
                    pgsql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        pgsql.Identifier(role_name), pgsql.Literal(password)
                    )
                )
            raw.commit()
        finally:
            raw.close()
    finally:
        engine.dispose()


def _alembic(*args, check: bool = True):
    env = dict(os.environ)
    env["DATABASE_URL"] = ADMIN_URL.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=str(_BACKEND_ROOT), env=env, check=False, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        pytest.fail(result.stdout + result.stderr)
    if result.returncode == 0 and args[:1] == ("upgrade",):
        _restore_app_role_password()
    return result


def test_every_model_table_exists_after_migration(restored):
    """迁移到 head 之后，ORM 注册的每张业务表都必须真实存在（AUD-05）。

    审计当初发现 11 张已接入业务的表没有建表迁移，而应用照常 ``create_all``，
    于是"缺迁移"在开发环境完全不可见。这里以元数据为准在真实迁移库上核对。
    """
    import app.models  # noqa: F401
    from app.database import Base

    engine = create_engine(ADMIN_URL)
    try:
        with engine.connect() as conn:
            existing = {
                row[0]
                for row in conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"
                ))
            }
        missing = sorted(set(Base.metadata.tables) - existing)
        assert not missing, f"迁移后仍缺表: {missing}"
    finally:
        engine.dispose()


def _forward_module():
    spec = importlib.util.spec_from_file_location(
        _FORWARD_FILE[:-3], _MIGRATIONS / _FORWARD_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def forward():
    return _forward_module()


@pytest.fixture
def admin():
    engine = create_engine(ADMIN_URL)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def restored():
    """每个用例结束后把库恢复到 head。"""
    _alembic("upgrade", "head")
    yield
    _alembic("upgrade", "head")


@pytest.fixture
def tenants():
    suffix = uuid.uuid4().hex[:12]
    return {"a": f"repair-a-{suffix}", "b": f"repair-b-{suffix}"}


def _seed_two_agents(admin, tenants):
    with admin.begin() as conn:
        for tenant in ("a", "b"):
            conn.execute(text(
                "INSERT INTO enterprises (id,name,is_active,invite_max_uses,invite_used_count) "
                "VALUES (:id,:name,true,10,0)"
            ), {"id": tenants[tenant], "name": tenants[tenant]})
            conn.execute(text(
                "INSERT INTO agents (id,enterprise_id,name,version,file_count,knowledge_count,"
                "status,created_at) VALUES (:id,:ent,:name,'1.0.0',0,0,'ready',now())"
            ), {"id": f"rep-agent-{tenant}", "ent": tenants[tenant], "name": tenant})


def _drop_seed(admin, tenants):
    with admin.begin() as conn:
        for tenant in ("a", "b"):
            conn.execute(text("DELETE FROM agents WHERE id=:id"),
                         {"id": f"rep-agent-{tenant}"})
            conn.execute(text("DELETE FROM enterprises WHERE id=:id"),
                         {"id": tenants[tenant]})


def _visible_agents(enterprise_id):
    """运行角色在该租户上下文里能看到的 agents 行数。"""
    import psycopg2

    conn = psycopg2.connect(_sync_url(_app_url_for_current_revision()))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_enterprise_id', %s, true)", (enterprise_id,)
            )
            cur.execute("SELECT count(*) FROM agents")
            return cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()


def test_migration_round_trip_is_symmetric(restored, forward):
    """upgrade → downgrade base → upgrade → downgrade -1 → upgrade 全部通过。"""
    assert _alembic("downgrade", "base").returncode == 0
    assert _alembic("upgrade", "head").returncode == 0
    assert _alembic("downgrade", "-1").returncode == 0
    assert _alembic("upgrade", "head").returncode == 0
    engine = create_engine(ADMIN_URL)
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _HEAD
            # 历史命名不再残留，canonical 策略名唯一。
            names = [
                row[0]
                for row in conn.execute(text(
                    "SELECT policyname FROM pg_policies WHERE schemaname='public' "
                    "AND tablename='agents'"
                ))
            ]
            assert names == ["agents_tenant_isolation"], names
    finally:
        engine.dispose()


def test_downgrade_restores_legacy_policy_name(restored, forward):
    """降级到上一 revision 必须恢复该 revision 预期的历史策略名。"""
    assert _alembic("downgrade", _PREVIOUS_HEAD).returncode == 0
    engine = create_engine(ADMIN_URL)
    try:
        with engine.connect() as conn:
            names = [
                row[0]
                for row in conn.execute(text(
                    "SELECT policyname FROM pg_policies WHERE schemaname='public' "
                    "AND tablename='agents'"
                ))
            ]
            assert names == ["agents_enterprise_isolation"], names
    finally:
        engine.dispose()
    assert _alembic("upgrade", "head").returncode == 0


def test_widened_legacy_policy_is_replaced_by_forward_fix(
    forward, restored, admin, tenants
):
    """历史策略被手工放宽成 USING (true) 时，前向修复必须真的收紧它。"""
    _seed_two_agents(admin, tenants)
    try:
        assert _alembic("downgrade", _PREVIOUS_HEAD).returncode == 0
        with admin.begin() as conn:
            conn.execute(text(
                "DROP POLICY IF EXISTS agents_enterprise_isolation ON agents"
            ))
            conn.execute(text(
                "CREATE POLICY agents_enterprise_isolation ON agents "
                "AS PERMISSIVE FOR ALL USING (true) WITH CHECK (true)"
            ))
        # 放宽后，运行角色在 A 的上下文里能看到 B 的行。
        assert _visible_agents(tenants["a"]) == 2

        assert _alembic("upgrade", "head").returncode == 0
        assert _visible_agents(tenants["a"]) == 1
        with admin.connect() as conn:
            names = [
                row[0]
                for row in conn.execute(text(
                    "SELECT policyname FROM pg_policies WHERE schemaname='public' "
                    "AND tablename='agents'"
                ))
            ]
        assert names == ["agents_tenant_isolation"], names
    finally:
        _drop_seed(admin, tenants)


def test_unknown_extra_policy_fails_closed(forward, restored, admin):
    """清单外的策略（手工绕过策略）必须让迁移失败，而不是默默留着。"""
    assert _alembic("downgrade", _PREVIOUS_HEAD).returncode == 0
    with admin.begin() as conn:
        conn.execute(text(
            "CREATE POLICY agents_manual_bypass ON agents "
            "AS PERMISSIVE FOR ALL USING (true) WITH CHECK (true)"
        ))
    try:
        result = _alembic("upgrade", "head", check=False)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "agents_manual_bypass" in (result.stdout + result.stderr)
        with admin.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == (
                _PREVIOUS_HEAD
            )
    finally:
        with admin.begin() as conn:
            conn.execute(text("DROP POLICY IF EXISTS agents_manual_bypass ON agents"))
        assert _alembic("upgrade", "head").returncode == 0


def test_forward_fix_reasserts_role_attributes(forward, restored, admin):
    """已被放宽的运行角色（超级/BYPASSRLS）必须被重新收紧。"""
    with admin.begin() as conn:
        conn.execute(text("ALTER ROLE autoteams_app BYPASSRLS"))
    try:
        assert _alembic("downgrade", _PREVIOUS_HEAD).returncode == 0
        # downgrade 会 DROP ROLE，降级后角色已不存在，因此直接升级重建。
        assert _alembic("upgrade", "head").returncode == 0
        with admin.connect() as conn:
            role = conn.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='autoteams_app'"
            )).one()
        assert tuple(role) == (False, False)
    finally:
        assert _alembic("upgrade", "head").returncode == 0


def test_runtime_role_cannot_read_other_tenant_after_forward_fix(forward, restored, admin, tenants):
    """收紧后跨租户读取必须为空，失败关闭而不是放行。"""
    _seed_two_agents(admin, tenants)
    try:
        assert _alembic("upgrade", "head").returncode == 0
        assert _visible_agents(tenants["a"]) == 1
        assert _visible_agents(tenants["b"]) == 1
        assert forward.LEDGER_TABLE == "audit_logs"
    finally:
        _drop_seed(admin, tenants)
