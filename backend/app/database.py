import asyncio
import logging
import os
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from app.config import settings
from app.utils.metrics import errors_total
from app.utils.branding import default_sqlite_url

logger = logging.getLogger(__name__)

# 数据库选择（AUD-12）
#
# 历史缺陷：这里只读 `os.getenv()`，而项目统一把配置放在**项目根目录的 .env**，
# 由 `Settings`（pydantic-settings）读取。`os.environ` 看不到 .env 的内容，于是
# `.env` 里明明写了 `DATABASE_URL=postgresql://...`，只要没有额外导出同名环境变量，
# 就会静默落到 SQLite 分支 —— 配置与实际连接的数据库不一致。
#
# 现在统一从 `settings` 决策，环境变量只是"更具体的覆盖"：
#   1. USE_SQLITE=true（env 或 .env）→ SQLite（本地开发的显式选择，优先级最高）
#   2. DATABASE_URL 环境变量          → 使用它
#   3. settings.DATABASE_URL（.env）   → 使用它
#   4. 都没有                          → SQLite（开发默认值）
_use_sqlite_env = os.getenv("USE_SQLITE", "").strip().lower()
_settings_use_sqlite = str(getattr(settings, "USE_SQLITE", "") or "").strip().lower()
_explicit_db_url = os.getenv("DATABASE_URL", "").strip()
_settings_db_url = str(getattr(settings, "DATABASE_URL", "") or "").strip()

if _use_sqlite_env == "true" or _settings_use_sqlite == "true":
    # 显式要求 SQLite：本地开发/测试的唯一合法选择。
    _resolved_db_url = default_sqlite_url()
    _connect_args = {"timeout": 60}
    _is_sqlite = True
    _poolclass = NullPool
elif _explicit_db_url:
    _resolved_db_url = _explicit_db_url
    _connect_args = {}
    _is_sqlite = False
    _poolclass = None
elif _settings_db_url:
    # .env 里配置的数据库（Compose 通过 environment 注入的 DATABASE_URL 会走上一分支；
    # 直接从 .env 启动后端时走这里）。
    _resolved_db_url = _settings_db_url
    _connect_args = {}
    _is_sqlite = False
    _poolclass = None
else:
    _resolved_db_url = default_sqlite_url()
    _connect_args = {"timeout": 60}
    _is_sqlite = True
    _poolclass = NullPool

DATABASE_URL = _resolved_db_url
if not _is_sqlite and settings.DEBUG is False and _use_sqlite_env != "true":
    # 非 DEBUG 下连接 SQLite 是生产事故（AUD-12）：config.py 已有启动校验，
    # 这里再挡一道，避免绕过 Settings 直接 import 本模块的脚本误连本地库。
    if "sqlite" in _resolved_db_url.lower():
        raise RuntimeError(
            "拒绝连接 SQLite：生产环境（DEBUG=false）必须配置 PostgreSQL DATABASE_URL。"
        )

# P1/P2-INFRA: 外置数据库使用可调优连接池；SQLite 使用 NullPool 避免 WAL 快照隔离问题
_pool_kwargs = {}
if not _is_sqlite:
    _pool_kwargs = {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "pool_pre_ping": settings.DB_POOL_PRE_PING,
    }

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    poolclass=_poolclass,
    **_pool_kwargs,
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """SQLite 连接时启用 WAL 模式，支持读写并发。

    P1-03: 同时启用 PRAGMA foreign_keys=ON，使 ondelete CASCADE 生效。
    SQLite 默认关闭外键约束检查，必须每个连接显式开启。
    """
    if _is_sqlite:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def verify_postgres_runtime_role(
    dbapi_conn,
    expected_role: str,
    *,
    require_execute: tuple = (),
    forbid_table_access: bool = False,
) -> None:
    """Check the actual PostgreSQL identity, not merely the configured role label.

    ``require_execute`` / ``forbid_table_access`` 表达"这个角色除了受限函数之外
    不该有别的能力"：队列角色必须能执行跨租户领取函数，引导角色则完全不该碰表。
    """
    expected_role = expected_role.strip()
    if not expected_role:
        raise RuntimeError("Production PostgreSQL requires DATABASE_APP_ROLE")
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute(
            "SELECT current_user, r.rolsuper, r.rolbypassrls, r.rolcreatedb, "
            "r.rolcreaterole, r.rolinherit, "
            "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid), "
            # 反向也要拒绝：本角色被授予给别的 LOGIN，等于把它的能力发出去。
            "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.roleid = r.oid), "
            "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relowner = r.oid AND c.relkind IN ('r', 'p') "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%') "
            "OR EXISTS (SELECT 1 FROM pg_database d WHERE d.datdba = r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspowner = r.oid "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'), "
            "current_setting('row_security') "
            "FROM pg_roles r WHERE r.rolname = current_user"
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
    if not row or row[0] != expected_role:
        raise RuntimeError("PostgreSQL runtime identity does not match DATABASE_APP_ROLE")
    if any(row[1:6]):
        raise RuntimeError("PostgreSQL runtime role has forbidden privileged attributes")
    if row[6]:
        raise RuntimeError("PostgreSQL runtime role must not belong to other roles")
    if row[7]:
        raise RuntimeError(
            "PostgreSQL runtime role must not be granted to other roles"
        )
    if row[8]:
        raise RuntimeError("PostgreSQL runtime role must not own databases, application schemas or tables")
    if row[9] != "on":
        raise RuntimeError("PostgreSQL runtime connection must enable row_security")
    if require_execute or forbid_table_access:
        _verify_extra_role_privileges(dbapi_conn, require_execute, forbid_table_access)


def _verify_extra_role_privileges(
    dbapi_conn, require_execute: tuple, forbid_table_access: bool
) -> None:
    cursor = dbapi_conn.cursor()
    try:
        # The asyncpg DBAPI adapter uses numeric-dollar parameters; psycopg2
        # uses pyformat. Connect events execute below SQLAlchemy's compiler.
        paramstyle = getattr(getattr(dbapi_conn, "dbapi", None), "paramstyle", "pyformat")
        privilege_sql = (
            "SELECT has_function_privilege(current_user, $1, 'EXECUTE')"
            if paramstyle == "numeric_dollar"
            else "SELECT has_function_privilege(current_user, %s, 'EXECUTE')"
        )
        for signature in require_execute:
            cursor.execute(privilege_sql, (signature,))
            if not cursor.fetchone()[0]:
                raise RuntimeError(
                    f"PostgreSQL runtime role cannot execute required function {signature}"
                )
        if forbid_table_access:
            # 用 has_table_privilege 而不是扫 ACL：PUBLIC 授权、列级授权都会被算进来。
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                      AND (has_table_privilege(current_user, c.oid, 'SELECT')
                           OR has_table_privilege(current_user, c.oid, 'INSERT')
                           OR has_table_privilege(current_user, c.oid, 'UPDATE')
                           OR has_table_privilege(current_user, c.oid, 'DELETE')
                           OR has_any_column_privilege(current_user, c.oid, 'SELECT'))
                )
                """
            )
            if cursor.fetchone()[0]:
                raise RuntimeError(
                    "PostgreSQL bootstrap role must not hold any table privileges"
                )
    finally:
        cursor.close()


@event.listens_for(engine.sync_engine, "connect")
def _verify_production_runtime_role(dbapi_conn, connection_record):
    # Only this application's engine is guarded. Alembic creates its own admin engine.
    if settings.DEBUG or engine.dialect.name != "postgresql":
        return
    verify_postgres_runtime_role(dbapi_conn, settings.DATABASE_APP_ROLE)


@event.listens_for(engine.sync_engine, "begin")
def _apply_tenant_context_on_begin(conn):
    """AUD-19：事务开始时写入 `app.current_enterprise_id`（SET LOCAL）。

    PostgreSQL 的 RLS 策略读这个 GUC。用 SET LOCAL 而不是 SET：事务结束即失效，
    连接归还连接池后不会把上一个请求的租户带给下一个请求（连接池复用不串租户）。
    未认证请求绑定的是 None，会显式写空串 —— RLS 策略据此拒绝所有行，
    而不是"没有设置变量"时按策略默认值放行。

    SQLite 没有 set_config，本钩子在非 PostgreSQL 上是空操作。
    """
    from app.utils.db_tenant_context import (
        AUTH_USER_GUC,
        NO_SUBJECT,
        NO_TENANT,
        TENANT_GUC,
        get_authenticated_user_id,
        get_current_enterprise_id,
    )

    if conn.dialect.name != "postgresql":
        return
    enterprise_id = get_current_enterprise_id()
    value = str(enterprise_id) if enterprise_id is not None else NO_TENANT
    subject = get_authenticated_user_id()
    # SQLAlchemy 编译绑定参数为驱动对应的占位符；PostgreSQL 出错必须阻断请求。
    # 主体 GUC 必须一起恢复：commit 之后的新事务如果只剩租户、没有主体，
    # 受控审计入口会拒绝"已认证但尚无企业"的账号写入（例如首家企业创建）。
    conn.execute(
        text(
            f"SELECT set_config('{TENANT_GUC}', :value, true), "
            f"set_config('{AUTH_USER_GUC}', :subject, true)"
        ),
        {"value": value, "subject": NO_SUBJECT if subject is None else str(subject)},
    )


# ---------------------------------------------------------------------------
# 受限角色连接（AUD-19）
# ---------------------------------------------------------------------------
# 队列领取/恢复是**跨租户**能力，只授予 autoteams_worker；渠道验签材料读取只授予
# 没有任何表权限的 autoteams_bootstrap。两者都不能挂在 API 连接上，因此各自使用
# 独立引擎、独立会话工厂，并在每次物理连接时核验实际角色。
#
# 角色之间不建立成员关系、也不镜像 INHERIT：否则"通过成员关系拿权限"会绕过上面
# 的最小权限校验（database.verify_postgres_runtime_role 会直接拒绝成员关系）。

WORKER_QUEUE_FUNCTIONS = (
    "public.app_claim_processing_task(text, integer)",
    "public.app_recover_processing_tasks(integer)",
    "public.app_claim_compilation_job(text, integer)",
    "public.app_recover_compilation_jobs()",
    "public.app_claim_agent_build_task(text, integer)",
    "public.app_recover_agent_build_tasks()",
)

BOOTSTRAP_FUNCTIONS = ("public.app_get_channel_webhook_material(text)",)


def _create_restricted_engine(url: str):
    if not url:
        return None
    return create_async_engine(url, echo=False, **_pool_kwargs)


worker_engine = _create_restricted_engine(str(settings.DATABASE_WORKER_URL or "").strip())
bootstrap_engine = _create_restricted_engine(
    str(settings.DATABASE_BOOTSTRAP_URL or "").strip()
)

worker_session_factory = (
    async_sessionmaker(worker_engine, class_=AsyncSession, expire_on_commit=False)
    if worker_engine is not None
    else async_session_factory
)
bootstrap_session_factory = (
    async_sessionmaker(bootstrap_engine, class_=AsyncSession, expire_on_commit=False)
    if bootstrap_engine is not None
    else None
)

def _verify_worker_connection(dbapi_conn, connection_record) -> None:
    if settings.DEBUG or worker_engine is None or worker_engine.dialect.name != "postgresql":
        return
    verify_postgres_runtime_role(
        dbapi_conn,
        settings.DATABASE_WORKER_ROLE,
        require_execute=WORKER_QUEUE_FUNCTIONS,
    )


def _verify_bootstrap_connection(dbapi_conn, connection_record) -> None:
    if (
        settings.DEBUG
        or bootstrap_engine is None
        or bootstrap_engine.dialect.name != "postgresql"
    ):
        return
    verify_postgres_runtime_role(
        dbapi_conn,
        settings.DATABASE_BOOTSTRAP_ROLE,
        require_execute=BOOTSTRAP_FUNCTIONS,
        forbid_table_access=True,
    )


# 只有真的配置了独立连接才注册监听：未配置时引擎为 None，
# `event.listens_for(None.sync_engine)` 会在 import 阶段直接崩溃，
# 从而连默认的 SQLite / 普通 API 部署都起不来。
if worker_engine is not None:
    event.listen(worker_engine.sync_engine, "connect", _verify_worker_connection)
    # Worker 也要按事务写租户上下文：领取到的任务属于哪个租户，后续写回就受哪个
    # 租户的策略约束。与 API 引擎共用同一个钩子实现。
    event.listen(worker_engine.sync_engine, "begin", _apply_tenant_context_on_begin)
if bootstrap_engine is not None:
    # 引导连接不绑定租户：它只能调用验签材料读取函数，没有任何表权限。
    event.listen(bootstrap_engine.sync_engine, "connect", _verify_bootstrap_connection)


def ensure_worker_database_ready() -> None:
    """生产 PostgreSQL 下，队列 Worker 必须持有自己的受限连接。

    没有独立连接时 Worker 只能用 API 角色，而 API 角色**没有**跨租户领取函数的
    执行权限（42501）。与其在领取循环里反复报错，不如启动即拒绝。
    """
    if settings.DEBUG or engine.dialect.name != "postgresql":
        return
    if worker_engine is None:
        raise RuntimeError(
            "拒绝启动队列 Worker：生产环境必须配置 DATABASE_WORKER_URL 与 "
            "DATABASE_WORKER_ROLE。跨租户领取/恢复函数只授予队列角色，"
            "API 运行角色调用会被数据库拒绝（42501）。"
        )


def ensure_bootstrap_database_ready() -> None:
    """渠道回调验签需要一条只有验签材料读取权的独立连接。"""
    if settings.DEBUG or engine.dialect.name != "postgresql":
        return
    if bootstrap_engine is None:
        raise RuntimeError(
            "渠道回调引导不可用：生产环境必须配置 DATABASE_BOOTSTRAP_URL 与 "
            "DATABASE_BOOTSTRAP_ROLE。API 运行角色没有任何表权限，"
            "不能在没有引导连接的情况下读取渠道账号。"
        )


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI 依赖：提供 AsyncSession 并在请求结束时清理。

    3.3.7: 关于 commit 语义的说明
    ---------------------------------------------------------------
    项目硬约束要求所有写操作显式 `await db.commit()`（见 project_memory.md）。
    此函数在 yield 后仅检测未提交的变更并记录 warning 日志，不再 auto-commit：
    - 端点已显式 commit → session.new/dirty/deleted 为空，无操作
    - 端点忘记 commit → 记录 warning 日志，变更不持久化（强制开发者修正为显式 commit）
    - 端点抛异常 → except 分支 rollback

    安全修复：移除 auto-commit 兜底，避免静默提交部分事务导致数据不一致。
    """
    async with async_session_factory() as session:
        try:
            yield session
            # 3.3.7: 检测是否有未显式 commit 的变更
            has_pending = bool(session.new or session.dirty or session.deleted)
            if has_pending:
                logger.warning(
                    "get_db: 检测到未显式 commit 的变更，变更不会被持久化。"
                    "请修正端点为显式 `await db.commit()` 以符合项目硬约束"
                )
        except (Exception, asyncio.CancelledError) as e:
            # P1-4 + P2-T5b: 任何异常（包括 CancelledError）都必须回滚事务，
            # 防止 SSE 流中断等场景下未提交的事务残留导致连接泄漏
            if not isinstance(e, asyncio.CancelledError):
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            await session.rollback()
            # 运行影响审计 Fix4: 重新抛出所有异常（含 CancelledError），
            # 保持 asyncio 取消语义传播，避免吞掉取消信号影响任务清理。
            raise
        finally:
            # AUD-19：请求结束必须把租户/主体上下文清回未绑定状态。同一个任务里
            # 如果残留了上一个请求的 ContextVar，后续事务的 begin 钩子会把它当成
            # 当前请求的租户写进 GUC —— 连接侧的 SET LOCAL 会自己失效，上下文侧不会。
            from app.utils.db_tenant_context import (
                bind_authenticated_user,
                bind_tenant_context,
            )

            bind_tenant_context(None)
            bind_authenticated_user(None)
