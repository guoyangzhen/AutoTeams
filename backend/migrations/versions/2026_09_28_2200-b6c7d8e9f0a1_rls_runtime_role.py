"""rls_runtime_role

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f1
Create Date: 2026-09-28 22:00:00.000000

AUD-19：RLS 需要一个**受约束的运行账户**才有意义。

问题：Compose 用 ``POSTGRES_USER`` 初始化数据库，官方镜像会把它建成超级用户；
超级用户与带 ``BYPASSRLS`` 的角色都会绕过 RLS 策略。因此
``2026_07_24_0200`` 建的策略在当前部署下**从未真正生效** —— 这比没有策略更危险，
因为运维会误以为存在第二道租户隔离。

本迁移（仅 PostgreSQL 执行，SQLite 自动跳过）：

1. 创建非超级用户运行角色 ``autofde_app``（``NOSUPERUSER NOBYPASSRLS NOINHERIT``）；
2. 授予连接、schema 使用与**业务表 DML** 权限（迁移所需 DDL 仍归迁移账户）；
3. 对**认证之后才会访问**的租户数据表启用 RLS，策略与
   ``2026_07_24_0200`` 保持同一表达式：
   ``enterprise_id::text = current_setting('app.current_enterprise_id', true)``。

认证路径上的表**不**纳入 RLS
--------------------------
``users`` / ``enterprises`` / ``invitations`` / ``local_path_grants`` /
``setup_sessions`` / ``llm_api_configs`` 是登录前就必须读取的表：应用在验证
凭据时还不知道调用者属于哪个租户，给它们加 RLS 会直接让所有人无法登录 ——
这就是审计所说的"明确无租户维护任务的专用边界"。这些表的隔离由应用层的
``tenant_scope`` 依赖与唯一约束保证，不靠 RLS。

配套改动：``app/utils/db_tenant_context.py`` 在每个事务用 ``SET LOCAL`` 写入
租户上下文；``app/config.py`` 的 ``DATABASE_APP_ROLE`` 声明实际运行角色。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "a5b6c7d8e9f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "autofde_app"

#: 认证之后访问、且带 enterprise_id 列的租户数据表。
RLS_TENANT_TABLES = (
    "agents",
    "agent_kpis",
    "compilation_jobs",
    "compilation_artifacts",
    "agent_build_tasks",
    "enterprise_profiles",
    "enterprise_operating_models",
    "enterprise_runtimes",
    "runtime_versions",
    "org_metrics",
    "workforce_lifecycle",
    "workforce_profiles",
    "flow_cards",
    "flow_runs",
    "flow_approvals",
    "workgroup_teams",
    "strike_teams",
    "channel_accounts",
    "channel_identities",
    "channel_bind_tokens",
    "runner_devices",
    "runner_tasks",
    "runner_challenges",
    "runner_audit_entries",
    "background_jobs",
    "processing_tasks",
    "shadow_tasks",
    "shadow_evaluation_sessions",
    "knowledge_graphs",
    "long_term_memories",
    "entity_memories",
    "episodic_traces",
    "procedural_genes",
    "interview_sessions",
    "collaboration_events",
    "product_events",
)

# These tables have no enterprise_id column.
# They need a separate ownership policy before the runtime role can use them.
INDIRECT_TENANT_TABLES = (
    "agent_versions",
    "matrix_tasks",
    "shared_blackboard_entries",
    "task_selection_bids",
    "runner_device_credentials",
    "runner_device_tokens",
    "runner_task_frames",
    "counterfactual_diffs",
    "optimization_histories",
    "audit_logs",
    "rag_evaluations",
)

#: 登录前必须可读，因此**不**启用 RLS（见模块 docstring）。
NON_RLS_TABLES = (
    "users",
    "enterprises",
    "invitations",
    "local_path_grants",
    "setup_sessions",
    "llm_api_configs",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _existing_tables() -> set:
    """返回当前数据库里真实存在的候选表（避免对不存在的表建策略）。"""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    return set(inspector.get_table_names())


def upgrade() -> None:
    if not _is_postgres():
        # SQLite 没有角色与 RLS；开发路径不需要这一步。
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autofde_app') THEN
                CREATE ROLE autofde_app LOGIN
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    existing = _existing_tables()
    bind = op.get_bind()
    if bind.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_auth_members m "
        "JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=:role)"
    ), {"role": APP_ROLE}).scalar():
        raise RuntimeError(f"RLS runtime role {APP_ROLE} must not be a member of other roles")
    if bind.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner "
        "WHERE r.rolname=:role AND c.relname = ANY(:tables))"
    ), {"role": APP_ROLE, "tables": list(RLS_TENANT_TABLES)}).scalar():
        raise RuntimeError(f"RLS runtime role {APP_ROLE} must not own protected tables")
    for table in NON_RLS_TABLES:
        if table not in existing:
            raise RuntimeError(f"RLS runtime role requires missing auth table: {table}")
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" TO {APP_ROLE}')
    for table in RLS_TENANT_TABLES:
        if table not in existing:
            raise RuntimeError(f"RLS policy table is missing: {table}")
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" TO {APP_ROLE}')
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_enterprise_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_enterprise_isolation ON {table}
            USING (enterprise_id::text = current_setting('app.current_enterprise_id', true))
            WITH CHECK (enterprise_id::text = current_setting('app.current_enterprise_id', true))
            """
        )


def downgrade() -> None:
    if not _is_postgres():
        return
    existing = _existing_tables()
    for table in RLS_TENANT_TABLES:
        if table not in existing:
            continue
        # a8f3d2e7c1b9 already created these two policies. Its own downgrade
        # removes them; keep them active when stepping only one revision back.
        if table not in {"agents", "agent_kpis"}:
            op.execute(f"DROP POLICY IF EXISTS {table}_enterprise_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE {table} FROM {APP_ROLE}")
    for table in NON_RLS_TABLES:
        if table in existing:
            op.execute(f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" FROM {APP_ROLE}')
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {APP_ROLE}")
