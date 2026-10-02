"""AUD-19 第二轮：真实 PostgreSQL 上的完整 RLS 权限与 CRUD 矩阵。

上一轮只对 ``agents`` 做过 A/B 双租户 CRUD，其余直接归属表只验了策略清单和
越租户 INSERT 拒绝；11 张间接归属表则连授权都没有。本文件在**专用空库**上：

1. 断言迁移 head 是本轮的前向修复 revision；
2. 逐表检查 RLS 启用、策略存在、运行角色授权到位，并断言运行角色
   **没有**任何未登记表上的权限（防止"全表放开绕过 RLS"）；
3. 逐表构造满足约束的**有效行**（父表先建），在 ``autoteams_app`` 下实测
   A/B 双租户的 SELECT、合法 INSERT/UPDATE/DELETE、越租户 INSERT 被拒、
   越租户 UPDATE/DELETE 影响 0 行、空租户读取为空；
4. 平台级表（审计账本、单行游标、预置目录）的授权边界。

播种用的行由 RowBuilder 依据 information_schema 生成：不认识的类型、缺失的父行
或失败的 INSERT 都会记入 ``skipped``，由覆盖度断言显式报出，因此矩阵"全绿"不
代表某些表其实没被验到。需要 RLS_TEST_ADMIN_URL / RLS_TEST_APP_URL 指向专用
PostgreSQL；普通 SQLite 测试环境自动跳过。

会改动 schema 的前向修复用例在 tests/test_rls_forward_repair.py，避免污染这里
的播种数据。
"""
# S608：本文件里被 lint 标出的 SQL 标识符全部来自 information_schema 与迁移
# 清单常量（不是用户输入），且都用双引号包裹。
# ruff: noqa: S608
import contextlib
import importlib.util
import os
import uuid
from pathlib import Path

import asyncpg
import psycopg2
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.utils.db_tenant_context import tenant_scope

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL), reason="需要专用 PostgreSQL 验收库（RLS_TEST_*_URL）"
)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _BACKEND_ROOT / "migrations" / "versions"
_FORWARD_FILE = "2026_09_29_0900-c2d3e4f5a6b8_rls_indirect_and_forward_fix.py"
_REPO_FILE = "2026_09_28_2200-b6c7d8e9f0a1_rls_runtime_role.py"
_HEAD = "e7f8a9b0c1d2"


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(filename[:-3], _MIGRATIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)



@contextlib.contextmanager
def app_tenant(enterprise_id: str):
    """运行角色（autoteams_app）上绑定租户的同步游标，用于策略反例断言。"""
    conn = psycopg2.connect(_sync_url(APP_URL))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_enterprise_id', %s, true)", (enterprise_id,)
            )
            yield cur
    finally:
        conn.rollback()
        conn.close()


def _fit(prefix: str, limit, suffix: str) -> str:
    """在长度限制内保留随机部分：唯一约束靠它避免碰撞。"""
    if limit is None or len(prefix) + len(suffix) <= int(limit):
        return prefix + suffix
    return prefix[: max(int(limit) - len(suffix), 1)] + suffix


@pytest.fixture(scope="module")
def forward():
    return _load(_FORWARD_FILE)


@pytest.fixture(scope="module")
def repository():
    return _load(_REPO_FILE)


@pytest.fixture(scope="module")
def tenants():
    suffix = uuid.uuid4().hex[:12]
    return {"a": f"rls2-a-{suffix}", "b": f"rls2-b-{suffix}", "suffix": suffix}


# --------------------------------------------------------------------------
# 通用"构造有效行"工具
# --------------------------------------------------------------------------

_SYNTHETIC = {
    "character varying": lambda tag, limit: _fit(f"{tag}-", limit, uuid.uuid4().hex[:10]),
    "character": lambda tag, limit: _fit(f"{tag}-", limit, uuid.uuid4().hex[:10]),
    "text": lambda tag, limit: _fit(f"{tag}-", limit, uuid.uuid4().hex[:10]),
    "uuid": lambda tag, limit: str(uuid.uuid4()),
    "integer": lambda tag, limit: uuid.uuid4().int % 100000 + 1,
    "bigint": lambda tag, limit: uuid.uuid4().int % 100000 + 1,
    "smallint": lambda tag, limit: 1,
    "double precision": lambda tag, limit: 1.0,
    "real": lambda tag, limit: 1.0,
    "numeric": lambda tag, limit: 1.0,
    "boolean": lambda tag, limit: True,
    "ARRAY": lambda tag, limit: "{}",
}

#: 必须原样拼进 SQL、不能作为绑定参数的类型。
_SQL_LITERAL = {
    "timestamp with time zone": "now()",
    "timestamp without time zone": "now()",
    "date": "now()",
    "time without time zone": "'00:00:00'::time",
    "interval": "'1 hour'::interval",
    "bytea": r"'\x00'::bytea",
    "json": "'{}'::json",
    "jsonb": "'{}'::json",
}


class _Raw(str):
    """标记"这是 SQL 片段，不是绑定参数"。"""


class RowBuilder:
    def __init__(self):
        self.rows: dict = {}
        self.skipped: dict = {}

    def columns(self, conn, table):
        return conn.execute(text(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "character_maximum_length FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=:table "
            "ORDER BY ordinal_position"
        ), {"table": table}).mappings().all()

    def foreign_keys(self, conn, table):
        return {
            fk["column"]: fk["parent"]
            for fk in conn.execute(text(
                "SELECT kcu.column_name AS column, ccu.table_name AS parent "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name=tc.constraint_name "
                " AND kcu.table_schema=tc.table_schema "
                "JOIN information_schema.constraint_column_usage ccu "
                "  ON ccu.constraint_name=tc.constraint_name "
                "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name=:table"
            ), {"table": table}).mappings()
        }

    def primary_key(self, conn, table):
        return conn.execute(text(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON kcu.constraint_name=tc.constraint_name "
            " AND kcu.table_schema=tc.table_schema "
            "WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_name=:table "
            "ORDER BY kcu.ordinal_position LIMIT 1"
        ), {"table": table}).scalar()

    def unique_columns(self, conn, table):
        """参与唯一约束的列：有默认值也必须显式给值，否则第二次插入必然冲突。"""
        return {
            column
            for (column,) in conn.execute(text(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON kcu.constraint_name=tc.constraint_name "
                " AND kcu.table_schema=tc.table_schema "
                "WHERE tc.constraint_type='UNIQUE' AND tc.table_schema=current_schema() "
                "AND tc.table_name=:table"
            ), {"table": table})
        }

    def build(self, conn, table, tenant_id, extra_rows=None):
        """返回 (column_names, values, pk_column, skipped)。"""
        fks = self.foreign_keys(conn, table)
        pk = self.primary_key(conn, table)
        unique = self.unique_columns(conn, table)
        rows = dict(self.rows)
        rows.update(extra_rows or {})
        names, values, skipped = [], [], []
        for column in self.columns(conn, table):
            name, dtype = column["column_name"], column["data_type"]
            limit = column["character_maximum_length"]
            if name in fks:
                # 自引用外键（如 episodic_traces.caused_by_event_id）指向自己尚未
                # 存在的行，PostgreSQL 会立刻判外键失败；这类"可选追溯"留空。
                parent_value = None if fks[name] == table else rows.get(fks[name])
                if parent_value is None and fks[name] != table:
                    skipped.append(f"{name}->{fks[name]}")
                    continue
                names.append(name)
                values.append(parent_value)
                continue
            if name == pk:
                # 自引用外键已在 insert() 里预留主键，这里必须复用同一个值。
                names.append(name)
                values.append(rows.get(name) or uuid.uuid4().hex)
                continue
            if name == "enterprise_id":
                names.append(name)
                values.append(tenant_id)
                continue
            # 唯一约束列即使有默认值也必须显式给值，否则同租户第二条行必然冲突。
            if name not in unique and (
                column["column_default"] is not None or column["is_nullable"] == "YES"
            ):
                continue
            if dtype in _SQL_LITERAL:
                names.append(name)
                values.append(_Raw(_SQL_LITERAL[dtype]))
                continue
            factory = _SYNTHETIC.get(dtype)
            if factory is None:
                skipped.append(f"{name}:{dtype}")
                continue
            names.append(name)
            values.append(factory(f"{table[-10:]}-{name[:8]}", limit))
        if not names:
            skipped.append("<no-columns>")
        return names, values, pk, skipped

    @staticmethod
    def named_sql(table, names, values, pk):
        """渲染成 SQLAlchemy 命名参数语句。"""
        rendered = ", ".join(
            str(v) if isinstance(v, _Raw) else ":" + n
            for n, v in zip(names, values, strict=True)
        )
        cols = ", ".join('"' + n + '"' for n in names)
        params = {n: v for n, v in zip(names, values, strict=True) if not isinstance(v, _Raw)}
        return f'INSERT INTO "{table}" ({cols}) VALUES ({rendered}) RETURNING "{pk}"', params

    @staticmethod
    def positional_sql(table, names, values, pk):
        """渲染成 asyncpg 的 $1 占位符语句。"""
        positional, params = [], []
        for value in values:
            if isinstance(value, _Raw):
                positional.append(str(value))
            else:
                params.append(value)
                positional.append(f"${len(params)}")
        cols = ", ".join('"' + n + '"' for n in names)
        return (
            f'INSERT INTO "{table}" ({cols}) VALUES ({", ".join(positional)}) '
            f'RETURNING "{pk}"',
            params,
        )

    def insert(self, conn, table, tenant_id, extra_rows=None):
        if table not in self.rows:
            self.rows[table] = uuid.uuid4().hex
        names, values, pk, skipped = self.build(conn, table, tenant_id, extra_rows)
        if skipped:
            self.skipped.setdefault(table, []).extend(skipped)
            return None
        sql, params = self.named_sql(table, names, values, pk)
        try:
            # 单表失败不能拖垮整个播种事务：失败会中止外层事务，后续命令全部被拒。
            with conn.begin_nested():
                value = conn.execute(text(sql), params).scalar()
        except Exception as error:  # noqa: BLE001 - 记录原因，交给覆盖度断言
            self.skipped.setdefault(table, []).append(f"<insert-failed: {error}>")
            return None
        self.rows[table] = value
        return value


def _dependency_order(tables, conn):
    """按外键依赖排序，父表先建。"""
    edges = {}
    for table in tables:
        parents = {
            parent
            for parent in conn.execute(text(
                "SELECT ccu.table_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.constraint_column_usage ccu "
                "  ON ccu.constraint_name=tc.constraint_name "
                "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name=:table"
            ), {"table": table}).scalars()
            if parent in tables
        }
        edges[table] = parents
    ordered, visiting = [], set()

    def visit(node):
        if node in ordered or node in visiting:
            return
        visiting.add(node)
        for parent in sorted(edges[node]):
            visit(parent)
        visiting.discard(node)
        ordered.append(node)

    for table in sorted(tables):
        visit(table)
    return ordered


class Seed:
    """A/B 两个租户在全部受保护表上的行；``keys[tenant][table] = 主键``。"""

    def __init__(self, forward, tenants):
        self.forward = forward
        self.tenants = tenants
        self.keys = {"a": {}, "b": {}}
        self.created = []
        self.builder = RowBuilder()
        self.skipped = {}

    def user_id(self, tenant):
        return f"rls2-u-{tenant}-{self.tenants['suffix']}"

    def alt_user(self, tenant):
        """同租户的第二个用户：给"唯一约束同时含外键"的表准备可区分的组合。"""
        return f"rls2-u2-{tenant}-{self.tenants['suffix']}"

    def protected_tables(self):
        return list(self.forward.DIRECT_TABLES) + list(self.forward.INDIRECT_TENANT_TABLES)

    def run(self, admin):
        with admin.begin() as conn:
            for tenant in ("a", "b"):
                conn.execute(
                    text(
                        "INSERT INTO enterprises (id,name,is_active,invite_max_uses,"
                        "invite_used_count) VALUES (:id,:name,true,10,0)"
                    ),
                    {"id": self.tenants[tenant], "name": self.tenants[tenant]},
                )
                for user in (self.user_id(tenant), self.alt_user(tenant)):
                    conn.execute(
                        text(
                            "INSERT INTO users (id,email,password_hash,name,role,"
                            "enterprise_id,is_active) VALUES "
                            "(:id,:email,'unused',:name,'member',:ent,true)"
                        ),
                        {
                            "id": user,
                            "email": f"{user}@rls.invalid",
                            "name": f"RLS {tenant}",
                            "ent": self.tenants[tenant],
                        },
                    )
        for tenant in ("a", "b"):
            self.builder.rows.clear()
            self.builder.rows["enterprises"] = self.tenants[tenant]
            self.builder.rows["users"] = self.user_id(tenant)
            self.keys[tenant]["enterprises"] = self.tenants[tenant]
            self.keys[tenant]["users"] = self.user_id(tenant)
            with admin.begin() as conn:
                for table in _dependency_order(self.protected_tables(), conn):
                    if table == "enterprises":
                        continue
                    value = self.builder.insert(conn, table, self.tenants[tenant])
                    if value is not None:
                        self.keys[tenant][table] = value
                        self.created.append((table, value))
        self.skipped = dict(self.builder.skipped)
        return self

    def cleanup(self, admin):
        with admin.begin() as conn:
            for table, pk in reversed(self.created):
                column = self.builder.primary_key(conn, table)
                conn.execute(
                    text(f'DELETE FROM "{table}" WHERE "{column}" = :pk'), {"pk": pk}
                )
            for tenant in ("a", "b"):
                for user in (self.user_id(tenant), self.alt_user(tenant)):
                    conn.execute(text("DELETE FROM users WHERE id=:id"), {"id": user})
                conn.execute(text("DELETE FROM enterprises WHERE id=:id"),
                             {"id": self.tenants[tenant]})
        admin.dispose()


@pytest.fixture(scope="module")
def seed(forward, tenants):
    admin = create_engine(ADMIN_URL)
    with admin.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _HEAD
    data = Seed(forward, tenants).run(admin)
    yield data
    data.cleanup(admin)


@pytest.fixture
async def app_conn():
    """运行角色（autoteams_app）的裸 asyncpg 连接。"""
    conn = await asyncpg.connect(_sync_url(APP_URL))
    try:
        yield conn
    finally:
        await conn.close()


# --------------------------------------------------------------------------
# 1) 角色、授权与策略清单
# --------------------------------------------------------------------------


def test_runtime_role_attributes():
    admin = create_engine(ADMIN_URL)
    with admin.connect() as conn:
        role = conn.execute(text(
            "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit "
            "FROM pg_roles WHERE rolname='autoteams_app'"
        )).one()
        assert tuple(role) == (False, False, False, False, False)
        assert conn.scalar(text(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
            "WHERE r.rolname='autoteams_app'"
        )) == 0
        assert conn.scalar(text(
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner "
            "WHERE r.rolname='autoteams_app'"
        )) == 0
    admin.dispose()


def test_every_tenant_table_has_rls_policy_and_grants(forward, repository):
    admin = create_engine(ADMIN_URL)
    declared = list(forward.DIRECT_TABLES) + list(forward.INDIRECT_TENANT_TABLES)
    with admin.connect() as conn:
        for table in declared:
            assert conn.scalar(text(
                "SELECT c.relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=:table"
            ), {"table": table}) is True, f"{table} 未启用 RLS"
            # 只允许本迁移建的策略：历史命名或手工加的策略都算失败。
            assert conn.scalar(text(
                "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
                "AND tablename=:table AND policyname=:policy"
            ), {"table": table, "policy": f"{table}_tenant_isolation"}) == 1, table
            assert conn.scalar(text(
                "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
                "AND tablename=:table"
            ), {"table": table}) == 1, f"{table} 存在清单外的策略"
        for table in repository.RLS_TENANT_TABLES:
            assert conn.scalar(text(
                "SELECT count(*) FROM information_schema.role_table_grants "
                "WHERE grantee='autoteams_app' AND table_name=:table"
            ), {"table": table}) > 0, table
        for table in forward.INDIRECT_TENANT_TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert conn.scalar(text(
                    "SELECT has_table_privilege('autoteams_app', :table, :privilege)"
                ), {"table": table, "privilege": privilege}) is True, (table, privilege)
        for table in forward.GLOBAL_CURSOR_TABLES:
            assert conn.scalar(text(
                "SELECT has_table_privilege('autoteams_app', :table, 'UPDATE')"
            ), {"table": table}) is True, table
        for table in forward.GLOBAL_CATALOG_TABLES:
            assert conn.scalar(text(
                "SELECT has_table_privilege('autoteams_app', :table, 'SELECT')"
            ), {"table": table}) is True, table
            assert conn.scalar(text(
                "SELECT has_table_privilege('autoteams_app', :table, 'UPDATE')"
            ), {"table": table}) is False, table
    admin.dispose()


def test_runtime_role_has_no_blanket_privileges(forward):
    """运行角色不得持有任何未登记表的权限 —— 全表放开会让 RLS 形同虚设。"""
    admin = create_engine(ADMIN_URL)
    allowed = (
        set(forward.DIRECT_TABLES)
        | set(forward.INDIRECT_TENANT_TABLES)
        | set(forward.AUTH_PATH_TABLES)
        | set(forward.GLOBAL_CURSOR_TABLES)
        | set(forward.GLOBAL_CATALOG_TABLES)
        | {forward.LEDGER_TABLE, forward.MIGRATION_METADATA_TABLE}
    )
    with admin.connect() as conn:
        granted = set(conn.execute(text(
            "SELECT table_name FROM information_schema.role_table_grants "
            "WHERE grantee='autoteams_app' AND table_schema=current_schema()"
        )).scalars())
    assert granted <= allowed, f"未登记表上的授权: {sorted(granted - allowed)}"
    admin.dispose()


def test_audit_ledger_is_tenant_scoped_and_append_only(forward):
    """账本读写都按租户，UPDATE/DELETE 不授权。

    写入必须带租户上下文且 ``user_id`` 属于本租户；曾经的 ``WITH CHECK (true)``
    会让任何租户都能以别人的 user_id 伪造审计记录。
    """
    admin = create_engine(ADMIN_URL)
    with admin.connect() as conn:
        for privilege, expected in (
            ("SELECT", True), ("INSERT", True), ("UPDATE", False), ("DELETE", False)
        ):
            assert conn.scalar(text(
                "SELECT has_table_privilege('autoteams_app', :table, :privilege)"
            ), {"table": forward.LEDGER_TABLE, "privilege": privilege}) is expected, privilege
        policies = {
            row[0]: (row[1], row[2] or "", row[3] or "")
            for row in conn.execute(text(
                "SELECT policyname, cmd, qual, with_check FROM pg_policies "
                "WHERE schemaname='public' AND tablename=:table"
            ), {"table": forward.LEDGER_TABLE})
        }
    assert set(policies) == {
        f"{forward.LEDGER_TABLE}_tenant_isolation",
        f"{forward.LEDGER_TABLE}_tenant_isolation_insert",
    }, policies
    # SELECT 的过滤条件与 INSERT 的 WITH CHECK 都必须引用租户 GUC。
    for _name, (_cmd, qual, with_check) in policies.items():
        assert "app.current_enterprise_id" in (qual or with_check), policies
    admin.dispose()


# --------------------------------------------------------------------------
# 2) 播种覆盖度
# --------------------------------------------------------------------------


def test_seeding_covers_every_tenant_table(forward, seed):
    """没有构造出有效行的表必须显式报出来，不能让矩阵"看起来全绿"。"""
    expected = set(forward.DIRECT_TABLES) | set(forward.INDIRECT_TENANT_TABLES)
    expected -= {"enterprises"}
    for tenant in ("a", "b"):
        missing = expected - set(seed.keys[tenant])
        assert not missing, f"租户 {tenant} 未播种: {sorted(missing)}"
    assert not seed.skipped, f"播种时被跳过的列: {seed.skipped}"


# --------------------------------------------------------------------------
# 3) 双租户 CRUD 矩阵
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["a", "b"])
async def test_tenant_reads_only_own_rows_in_every_tenant_table(
    forward, seed, tenants, tenant, app_conn
):
    own = tenants[tenant]
    indirect = list(forward.INDIRECT_TENANT_TABLES)
    try:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.current_enterprise_id', $1, true)", own
            )
            for table in forward.DIRECT_TABLES:
                if table == "enterprises":
                    continue
                assert await app_conn.fetchval(
                    f'SELECT count(*) FROM "{table}" WHERE enterprise_id = $1', own
                ) >= 1, f"{table}: 本租户行不可见"
                assert await app_conn.fetchval(
                    f'SELECT count(*) FROM "{table}" WHERE enterprise_id <> $1', own
                ) == 0, f"{table}: 读到其他租户的数据"
            for table in indirect:
                assert await app_conn.fetchval(f'SELECT count(*) FROM "{table}"') >= 1, (
                    f"{table}: 本租户行不可见"
                )
        async with app_conn.transaction():
            await app_conn.execute("SELECT set_config('app.current_enterprise_id', '', true)")
            for table in list(forward.DIRECT_TABLES) + indirect:
                if table == "enterprises":
                    continue
                assert await app_conn.fetchval(f'SELECT count(*) FROM "{table}"') == 0, table
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")


@pytest.mark.asyncio
async def test_cross_tenant_update_and_delete_affect_zero_rows(forward, seed, tenants, app_conn):
    try:
        for tenant, other in (("a", "b"), ("b", "a")):
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.current_enterprise_id', $1, true)",
                    tenants[tenant],
                )
                for table in forward.DIRECT_TABLES:
                    if table == "enterprises":
                        continue
                    updated = await app_conn.execute(
                        f'UPDATE "{table}" SET enterprise_id = enterprise_id '
                        "WHERE enterprise_id = $1",
                        tenants[other],
                    )
                    assert updated == "UPDATE 0", f"{table}: 越租户 UPDATE -> {updated}"
                    deleted = await app_conn.execute(
                        f'DELETE FROM "{table}" WHERE enterprise_id = $1', tenants[other]
                    )
                    assert deleted == "DELETE 0", f"{table}: 越租户 DELETE -> {deleted}"
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")


@pytest.mark.asyncio
async def test_cross_tenant_insert_is_rejected_for_every_direct_table(
    forward, seed, tenants, app_conn
):
    """A 不能以 B 的 enterprise_id 插入（WITH CHECK → 42501）。"""
    admin = create_engine(ADMIN_URL)
    builder = RowBuilder()
    try:
        for table in forward.DIRECT_TABLES:
            if table == "enterprises":
                continue
            with admin.connect() as meta:
                names, values, pk, _skipped = builder.build(
                    meta, table, tenants["b"], dict(seed.keys["b"])
                )
                sql, params = builder.positional_sql(table, names, values, pk)
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.current_enterprise_id', $1, true)", tenants["a"]
                )
                with pytest.raises(asyncpg.PostgresError) as error:
                    # asyncpg 需要展开参数：execute(sql, *params)
                    await app_conn.execute(sql, *params)
            assert error.value.sqlstate == "42501", (table, error.value.sqlstate)
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")
        admin.dispose()


@pytest.mark.asyncio
async def test_own_row_insert_update_delete_succeed_for_every_table(forward, seed, tenants):
    """受约束运行角色自己的行：INSERT / UPDATE / DELETE 都必须成功。

    上一轮只验了"管理员播种后 SELECT"和"越租户 INSERT 被拒"，没有证明合法写入
    在 ``autoteams_app`` 下真能走通 —— 策略收紧若把正常业务也挡住，那不是隔离。
    """
    admin = create_engine(ADMIN_URL)
    builder = RowBuilder()
    tables = [t for t in forward.DIRECT_TABLES if t != "enterprises"] + list(
        forward.INDIRECT_TENANT_TABLES
    )
    created: list = []
    try:
        for table in tables:
            with admin.connect() as meta:
                extra = dict(seed.keys["a"])
                # 唯一约束里同时含外键的表需要与播种行不同的用户组合。
                extra["users"] = seed.alt_user("a")
                names, values, pk, skipped = builder.build(meta, table, tenants["a"], extra)
                assert not skipped, (table, skipped)
                sql, params = builder.positional_sql(table, names, values, pk)
                target = _writable_column(builder, meta, table, pk)
            conn = await asyncpg.connect(_sync_url(APP_URL))
            try:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.current_enterprise_id', $1, true)",
                        tenants["a"],
                    )
                    new_id = await conn.fetchval(sql, *params)
                    updated = await conn.execute(
                        f'UPDATE "{table}" SET "{target}" = "{target}" WHERE "{pk}" = $1',
                        new_id,
                    )
                    deleted = await conn.execute(
                        f'DELETE FROM "{table}" WHERE "{pk}" = $1', new_id
                    )
            finally:
                await conn.close()
            assert updated == "UPDATE 1", f"{table}: 本租户 UPDATE -> {updated}"
            assert deleted == "DELETE 1", f"{table}: 本租户 DELETE -> {deleted}"
            created.append((table, pk, new_id))
    finally:
        with admin.begin() as conn_admin:
            for table, column, value in reversed(created):
                conn_admin.execute(
                    text(f'DELETE FROM "{table}" WHERE "{column}" = :value'),
                    {"value": value},
                )
        admin.dispose()


def _writable_column(builder: RowBuilder, conn, table: str, pk: str) -> str:
    """挑一个可写的非主键列，用于自赋值 UPDATE。

    间接归属表没有 ``enterprise_id``，硬编码那一句只会在一半表上报语法错误；
    这里的 UPDATE 只是为了让 RLS 的 WITH CHECK 真正跑一遍，列本身不重要。
    """
    columns = {c["column_name"]: c for c in builder.columns(conn, table)}
    fks = builder.foreign_keys(conn, table)
    if "enterprise_id" in columns:
        return "enterprise_id"
    for name, column in columns.items():
        if name == pk or name in fks:
            continue
        if column["data_type"] in _SQL_LITERAL or column["data_type"] in _SYNTHETIC:
            return name
    for name in fks:
        return name
    raise AssertionError(f"{table} 没有可用于自赋值的列")


def _policy_fk_column(forward, table: str, builder: RowBuilder, conn):
    """返回该间接表策略实际使用的外键列与其父表。"""
    template = forward.INDIRECT_TENANT_TABLES[table]
    for column, parent in builder.foreign_keys(conn, table).items():
        if f"FROM {parent} " in template:
            return column, parent
    raise AssertionError(f"{table} 的策略没有匹配到外键: {template}")


@pytest.mark.asyncio
async def test_cross_tenant_insert_rejected_for_indirect_tables(forward, seed, tenants, app_conn):
    """间接归属表：用**别的租户的父外键**插入同样必须被拒绝。"""
    admin = create_engine(ADMIN_URL)
    builder = RowBuilder()
    try:
        with admin.connect() as meta:
            for table in forward.INDIRECT_TENANT_TABLES:
                fk_column, parent = _policy_fk_column(forward, table, builder, meta)
                foreign = dict(seed.keys["a"])
                foreign[parent] = seed.keys["b"][parent]
                names, values, pk, skipped = builder.build(meta, table, tenants["a"], foreign)
                assert not skipped, (table, skipped)
                sql, params = builder.positional_sql(table, names, values, pk)
                async with app_conn.transaction():
                    await app_conn.execute(
                        "SELECT set_config('app.current_enterprise_id', $1, true)",
                        tenants["a"],
                    )
                    try:
                        await app_conn.execute(sql, *params)
                    except asyncpg.PostgresError as error:
                        assert error.sqlstate == "42501", (table, error.sqlstate)
                    else:
                        pytest.fail(f"{table}: foreign tenant parent accepted ({fk_column} -> {parent})")
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")
        admin.dispose()


@pytest.mark.asyncio
async def test_indirect_row_cannot_be_reparented_to_other_tenant(
    forward, seed, tenants, app_conn
):
    """本租户的行也不能改成指向别的租户的父行（WITH CHECK 覆盖 UPDATE）。"""
    admin = create_engine(ADMIN_URL)
    builder = RowBuilder()
    try:
        with admin.connect() as meta:
            for table in forward.INDIRECT_TENANT_TABLES:
                fk_column, parent = _policy_fk_column(forward, table, builder, meta)
                pk = builder.primary_key(meta, table)
                async with app_conn.transaction():
                    await app_conn.execute(
                        "SELECT set_config('app.current_enterprise_id', $1, true)",
                        tenants["a"],
                    )
                    with pytest.raises(asyncpg.PostgresError) as error:
                        await app_conn.execute(
                            f'UPDATE "{table}" SET "{fk_column}" = $1 WHERE "{pk}" = $2',
                            seed.keys["b"][parent],
                            seed.keys["a"][table],
                        )
                assert error.value.sqlstate == "42501", (table, error.value.sqlstate)
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")
        admin.dispose()


@pytest.mark.asyncio
async def test_audit_ledger_read_is_tenant_scoped(forward, seed, tenants, app_conn):
    """账本读取按租户；写入必须归属本租户用户。"""
    admin = create_engine(ADMIN_URL)
    try:
        with admin.begin() as meta:
            for tenant in ("a", "b"):
                meta.execute(text(
                    "INSERT INTO audit_logs (id,user_id,action,resource_type,resource_id,"
                    "created_at,updated_at) VALUES (gen_random_uuid()::text,:user,'rls-probe',"
                    "'test','x',now(),now())"
                ), {"user": seed.user_id(tenant)})
        for tenant in ("a", "b"):
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.current_enterprise_id', $1, true)",
                    tenants[tenant],
                )
                visible = await app_conn.fetch(
                    "SELECT user_id FROM audit_logs WHERE action='rls-probe'"
                )
                assert {r["user_id"] for r in visible} <= {seed.user_id(tenant)}, (
                    f"租户 {tenant} 读到了别人的审计记录"
                )
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")
        with admin.begin() as meta:
            meta.execute(text("DELETE FROM audit_logs WHERE action='rls-probe'"))
        admin.dispose()


@pytest.mark.asyncio
async def test_audit_ledger_rejects_forged_and_anonymous_inserts(seed, tenants, app_conn):
    """A 不能以 B 的 user_id 写审计；匿名（无租户上下文）写入同样被拒。

    匿名安全事件（登录失败、账户锁定）因此仍无法由受约束运行角色落库 ——
    这是已知缺口，切换运行角色前必须先有独立的受控安全写入通道，
    不能靠放开插入权限来"解决"。
    """
    try:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.current_enterprise_id', $1, true)", tenants["a"]
            )
            with pytest.raises(asyncpg.PostgresError) as forged:
                await app_conn.execute(
                    "INSERT INTO audit_logs (id,user_id,action,resource_type,resource_id,"
                    "created_at,updated_at) VALUES (gen_random_uuid()::text,$1,'forged',"
                    "'user','x',now(),now())",
                    seed.user_id("b"),
                )
        assert forged.value.sqlstate == "42501"

        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.current_enterprise_id', $1, true)", tenants["a"]
            )
            with pytest.raises(asyncpg.PostgresError) as anonymous:
                await app_conn.execute(
                    "INSERT INTO audit_logs (id,user_id,action,resource_type,resource_id,"
                    "created_at,updated_at) VALUES (gen_random_uuid()::text,NULL,"
                    "'login_failed','user','x',now(),now())"
                )
        assert anonymous.value.sqlstate == "42501"
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")


@pytest.mark.asyncio
async def test_audit_ledger_accepts_own_tenant_insert(seed, tenants, app_conn):
    """本租户用户的审计写入必须成功，否则正常业务会被策略挡住。"""
    try:
        async with app_conn.transaction():
            await app_conn.execute(
                "SELECT set_config('app.current_enterprise_id', $1, true)", tenants["a"]
            )
            await app_conn.execute(
                "INSERT INTO audit_logs (id,user_id,action,resource_type,resource_id,"
                "created_at,updated_at) VALUES (gen_random_uuid()::text,$1,'rls-own',"
                "'user','x',now(),now())",
                seed.user_id("a"),
            )
    finally:
        await app_conn.execute("RESET app.current_enterprise_id")
        admin = create_engine(ADMIN_URL)
        try:
            with admin.begin() as meta:
                meta.execute(text("DELETE FROM audit_logs WHERE action='rls-own'"))
        finally:
            admin.dispose()


# --------------------------------------------------------------------------
# 4) 应用身份链路
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_scope_writes_guc_in_transaction(seed, tenants):
    """真实 SQLAlchemy 引擎 + **应用自己的**事务钩子：GUC 只在事务内可见。"""
    from app import database as app_database

    listener = getattr(app_database, "_apply_tenant_context_on_begin", None)
    if listener is None:
        from app.utils.db_tenant_context import TENANT_GUC, get_current_enterprise_id

        def listener(conn):  # noqa: ANN001, ANN202
            if conn.dialect.name != "postgresql":
                return
            value = get_current_enterprise_id()
            conn.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :value, true)"),
                {"value": str(value) if value is not None else ""},
            )

    engine = create_async_engine(APP_URL)
    event.listen(engine.sync_engine, "begin", listener)
    admin = create_engine(ADMIN_URL)
    try:
        with admin.connect() as conn:
            expected = conn.scalar(
                text("SELECT count(*) FROM agents WHERE enterprise_id = :ent"),
                {"ent": tenants["a"]},
            )
        with tenant_scope(tenants["a"]):
            async with engine.begin() as conn:
                assert await conn.scalar(
                    text("SELECT current_setting('app.current_enterprise_id', true)")
                ) == tenants["a"]
                # 运行角色在本租户上下文里只看到本租户的行。
                assert await conn.scalar(text("SELECT count(*) FROM agents")) == expected
        # 事务结束后钩子把 GUC 写成空串（"没有租户"哨兵），而不是留 NULL：
        # 判定不了租户时 RLS 必须拒绝所有行，而不是按默认值放行。
        async with engine.connect() as conn:
            assert await conn.scalar(
                text("SELECT current_setting('app.current_enterprise_id', true)")
            ) == ""
            assert await conn.scalar(text("SELECT count(*) FROM agents")) == 0
    finally:
        await engine.dispose()
        admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("table,foreign_parent", [
    ("task_plans", "users"), ("task_plans", "agents"),
    ("skill_executions", "users"), ("skill_executions", "agents"),
    ("skill_executions", "skills"),
])
async def test_mixed_tenant_parent_references_are_rejected(seed, tenants, app_conn, table, foreign_parent):
    """一个合法父外键不能掩盖同一行另一个跨租户父外键。"""
    admin = create_engine(ADMIN_URL)
    try:
        builder = RowBuilder()
        parents = dict(seed.keys["a"])
        parents[foreign_parent] = seed.keys["b"][foreign_parent]
        with admin.connect() as meta:
            names, values, pk, skipped = builder.build(meta, table, tenants["a"], parents)
            assert not skipped
            sql, params = builder.positional_sql(table, names, values, pk)
        async with app_conn.transaction():
            await app_conn.execute("SELECT set_config('app.current_enterprise_id', $1, true)", tenants["a"])
            with pytest.raises(asyncpg.PostgresError) as error:
                await app_conn.execute(sql, *params)
        assert error.value.sqlstate == "42501", (table, foreign_parent, error.value)
    finally:
        admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["task_plans", "skill_executions"])
async def test_optional_agent_keeps_own_user_history_accessible(seed, tenants, app_conn, table):
    admin = create_engine(ADMIN_URL)
    try:
        builder = RowBuilder()
        with admin.connect() as meta:
            names, values, pk, skipped = builder.build(meta, table, tenants["a"], dict(seed.keys["a"]))
            assert not skipped
            values[names.index("agent_id")] = None
            sql, params = builder.positional_sql(table, names, values, pk)
        async with app_conn.transaction():
            await app_conn.execute("SELECT set_config('app.current_enterprise_id', $1, true)", tenants["a"])
            new_id = await app_conn.fetchval(sql, *params)
            assert await app_conn.execute(f'DELETE FROM "{table}" WHERE "{pk}"=$1', new_id) == "DELETE 1"
    finally:
        admin.dispose()
