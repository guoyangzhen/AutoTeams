"""rls_indirect_and_forward_fix

Revision ID: c2d3e4f5a6b8
Revises: b6c7d8e9f0a1
Create Date: 2026-09-29 09:00:00.000000

AUD-19 第二轮：把 11 张"间接归属表"和另外 5 张漏掉的直接归属表纳入受约束运行
角色，并为**已经 stamp 过 b6c7d8e9f0a1 的库**提供前向修复。

背景
----
上一轮只做了一件事：给 36 张带 ``enterprise_id`` 的表建策略。真实盘库发现：

* 还有 5 张同样带 ``enterprise_id`` 的业务表从未进入策略清单
  （advisor_suggestions / agent_api_credentials / agent_templates /
  approval_gates / runner_device_commands）—— 运行角色对它们**连权限都没有**；
* 还有 19 张业务表没有 ``enterprise_id``，靠外键归属租户，上一轮把它们整个
  排除在授权之外，Agent 版本、设备令牌、消息、文件等路径在受约束角色下会直接
  权限报错；
* ``b6c7d8e9f0a1`` 自身被就地修过（``ALTER ROLE ... BYPASSRLS = off``）。
  **已 stamp 该 revision 的库不会重跑它**，因此"版本号是 head"并不等于
  角色属性、授权和策略都正确。本迁移对全部清单做幂等重申，并对手工改坏的
  schema 直接报错，而不是默默放行。

策略模型
--------
* 直接归属表：``enterprise_id::text = current_setting('app.current_enterprise_id', true)``。
* 间接归属表：``EXISTS (父表主键 = 本表外键 AND 父表.enterprise_id::text = 同一 GUC)``。
  父表自身也有 RLS 且读同一个 GUC，两层判定方向一致，**不会因为漏设 GUC 而放行**：
  没有租户上下文时子查询看不到任何父行 → 本行不可见 → 失败关闭。
* ``audit_logs`` 是**平台级单链账本**，没有 ``enterprise_id``。读写都按"记录归属的
  用户属于哪个租户"判定：读只看本租户，写必须带租户上下文且 ``user_id`` 属于本租户。
  ``UPDATE``/``DELETE`` 一律不授权，账本只追加。
  **已知缺口（OPEN）**：登录失败、账户锁定这类事件在调用方还没有用户时就要落库
  （``log_audit(db, None, ...)``）。受约束运行角色无法为它们写账本；正确解法是
  独立的受控安全写入通道（专用角色或 SECURITY DEFINER 函数 + 签名校验），而不是
  把插入放开成 ``WITH CHECK (true)`` —— 那会让任何租户都能以别人的 user_id 伪造
  审计记录。切换到 ``autofde_app`` 之前必须先落地该通道。
* 跨租户风险说明：``audit_logs`` 的哈希链是**全平台单链**，链尾游标在
  ``audit_chain_state``（单行、无租户数据）。因此链的推进与校验天然跨租户；
  运行角色对该游标只授予推进链所需的 DML，链的完整性校验应在受控通道里进行。
* ``audit_chain_state``（单行游标）与 ``skill_templates``（平台预置目录）没有
  租户数据，授予运行角色所需 DML / 只读 SELECT，并在此断言运行角色**不得**
  持有其他表上的权限，防止"全表放开绕过 RLS"。
* 历史 revision 用的策略名是 ``{table}_enterprise_isolation``。多条 PERMISSIVE
  策略之间是**或**：其中一条被手工放宽成 ``USING (true)`` 就足以让整张表对所有
  租户可见，而"只新建自己的策略"并不能收紧它。本迁移显式删除历史命名，并断言
  每张受保护表上只存在本迁移建的策略且引用租户 GUC，否则迁移失败。

不修改任何历史 revision；本迁移可重复执行，且 downgrade 对称。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

revision: str = "c2d3e4f5a6b8"
down_revision: Union[str, None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "autofde_app"

GUC = "current_setting('app.current_enterprise_id', true)"

#: b6c7d8e9f0a1 已覆盖的 36 张直接归属表。
PREVIOUS_DIRECT_TABLES = (
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

#: 本轮补齐的直接归属表（同样带 enterprise_id，上一轮漏进清单）。
ADDED_DIRECT_TABLES = (
    "advisor_suggestions",
    "agent_api_credentials",
    "agent_templates",
    "approval_gates",
    "runner_device_commands",
)

DIRECT_TABLES = PREVIOUS_DIRECT_TABLES + ADDED_DIRECT_TABLES

#: 认证路径表：登录前必须可读，由应用层 tenant_scope 负责隔离。
AUTH_PATH_TABLES = (
    "users",
    "enterprises",
    "invitations",
    "local_path_grants",
    "setup_sessions",
    "llm_api_configs",
)

#: 间接归属表 -> 归属判定模板（``{alias}`` 会被替换成表名本身）。
#: 每张表都通过外键一跳或多跳连到带 enterprise_id 的父表。
INDIRECT_TENANT_TABLES: dict = {
    # --- Agent 子资源 ---
    "agent_versions": (
        "EXISTS (SELECT 1 FROM agents p WHERE p.id = {alias}.agent_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "optimization_histories": (
        "EXISTS (SELECT 1 FROM agents p WHERE p.id = {alias}.agent_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "rag_evaluations": (
        "EXISTS (SELECT 1 FROM agents p WHERE p.id = {alias}.agent_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "task_plans": (
        "(EXISTS (SELECT 1 FROM users p WHERE p.id = {alias}.user_id "
        f"AND p.enterprise_id::text = {GUC}) "
        "AND ({alias}.agent_id IS NULL OR EXISTS (SELECT 1 FROM agents a "
        f"WHERE a.id = {{alias}}.agent_id AND a.enterprise_id::text = {GUC})))"
    ),
    "skills": (
        "EXISTS (SELECT 1 FROM agents p WHERE p.id = {alias}.agent_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "skill_executions": (
        "EXISTS (SELECT 1 FROM skills s JOIN agents g ON g.id = s.agent_id "
        f"WHERE s.id = {{alias}}.skill_id AND g.enterprise_id::text = {GUC}) AND "
        "(EXISTS (SELECT 1 FROM users p WHERE p.id = {alias}.user_id "
        f"AND p.enterprise_id::text = {GUC}) "
        "AND ({alias}.agent_id IS NULL OR EXISTS (SELECT 1 FROM agents a "
        f"WHERE a.id = {{alias}}.agent_id AND a.enterprise_id::text = {GUC})))"
    ),
    "conversations": (
        "EXISTS (SELECT 1 FROM agents p WHERE p.id = {alias}.agent_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "messages": (
        "EXISTS (SELECT 1 FROM conversations p WHERE p.id = {alias}.conversation_id "
        "AND EXISTS (SELECT 1 FROM agents g WHERE g.id = p.agent_id "
        f"AND g.enterprise_id::text = {GUC}))"
    ),
    "files": (
        "EXISTS (SELECT 1 FROM agents p WHERE p.id = {alias}.agent_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "operation_snapshots": (
        "EXISTS (SELECT 1 FROM agents p WHERE p.id = {alias}.agent_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "confidential_file_accesses": (
        "EXISTS (SELECT 1 FROM files p WHERE p.id = {alias}.file_id "
        "AND EXISTS (SELECT 1 FROM agents g WHERE g.id = p.agent_id "
        f"AND g.enterprise_id::text = {GUC}))"
    ),
    "interview_questions": (
        "EXISTS (SELECT 1 FROM interview_sessions p WHERE p.id = {alias}.session_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    # --- 工作组矩阵 ---
    "matrix_tasks": (
        "EXISTS (SELECT 1 FROM workgroup_teams p WHERE p.id = {alias}.team_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "task_selection_bids": (
        "EXISTS (SELECT 1 FROM matrix_tasks p WHERE p.id = {alias}.task_id "
        "AND EXISTS (SELECT 1 FROM workgroup_teams g WHERE g.id = p.team_id "
        f"AND g.enterprise_id::text = {GUC}))"
    ),
    "shared_blackboard_entries": (
        "EXISTS (SELECT 1 FROM workgroup_teams p WHERE p.id = {alias}.team_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "runner_device_credentials": (
        "EXISTS (SELECT 1 FROM runner_devices p WHERE p.id = {alias}.device_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "runner_device_tokens": (
        "EXISTS (SELECT 1 FROM runner_devices p WHERE p.id = {alias}.device_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    "runner_task_frames": (
        "EXISTS (SELECT 1 FROM runner_tasks p WHERE p.task_id = {alias}.task_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
    # --- 反事实影子评估 ---
    "counterfactual_diffs": (
        "EXISTS (SELECT 1 FROM shadow_evaluation_sessions p "
        "WHERE p.session_id = {alias}.session_id "
        f"AND p.enterprise_id::text = {GUC})"
    ),
}

#: 平台级单行游标：不含租户数据，链推进必须跨租户可用。
GLOBAL_CURSOR_TABLES = ("audit_chain_state",)

#: 平台预置目录：无租户数据，只读。
GLOBAL_CATALOG_TABLES = ("skill_templates",)


#: 迁移元数据表：就绪探针要读它来比对 head，否则运行角色下健康检查必然失败关闭。
#: 单行表、只有 revision 字符串，不含租户数据，因此只给只读权限。
MIGRATION_METADATA_TABLE = "alembic_version"
LEDGER_TABLE = "audit_logs"

POLICY_SUFFIX = "_tenant_isolation"



#: 历史 revision 使用的策略名。同名遗留策略如果被手工放宽（例如 USING (true)），
#: PERMISSIVE 策略之间是**或**关系：只新建自己的策略并不会收紧它。
LEGACY_POLICY_SUFFIX = "_enterprise_isolation"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _existing_tables() -> set:
    return set(sa_inspect(op.get_bind()).get_table_names())


def _policy_name(table: str) -> str:
    return f"{table}{POLICY_SUFFIX}"


def _legacy_policy_name(table: str) -> str:
    return f"{table}{LEGACY_POLICY_SUFFIX}"


def _apply_direct_policy(table: str) -> None:
    predicate = f"enterprise_id::text = {GUC}"
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" TO {APP_ROLE}')
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    # 历史策略名必须一并删除：两条 PERMISSIVE 策略是"或"，留着就等于没收紧。
    op.execute(f"DROP POLICY IF EXISTS {_legacy_policy_name(table)} ON {table}")
    op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
    op.execute(
        f"CREATE POLICY {_policy_name(table)} ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def _apply_indirect_policy(table: str, template: str) -> None:
    predicate = template.format(alias=table)
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" TO {APP_ROLE}')
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_legacy_policy_name(table)} ON {table}")
    op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
    op.execute(
        f"CREATE POLICY {_policy_name(table)} ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def _assert_policies_are_tenant_scoped(tables: Sequence) -> None:
    """每张受保护表只允许存在本迁移建的策略，且必须真的读租户 GUC。

    两条 PERMISSIVE 策略是"或"：任何一条被放宽（``USING (true)``）都会让整张表
    对所有租户可见。清单之外的多余策略一律视为失败，而不是"先留着"。
    """
    rows = op.get_bind().execute(text(
        "SELECT tablename, policyname, coalesce(qual, '') AS qual, "
        "coalesce(with_check, '') AS with_check "
        "FROM pg_policies WHERE schemaname = current_schema()"
    )).mappings().all()
    by_table: dict = {}
    for row in rows:
        by_table.setdefault(row["tablename"], []).append(row)
    for table in tables:
        policies = by_table.get(table, [])
        unexpected = [p["policyname"] for p in policies if p["policyname"] != _policy_name(table)]
        if unexpected:
            raise RuntimeError(
                f"{table} 存在清单外的 RLS 策略 {unexpected}；PERMISSIVE 策略取并集，"
                "必须先人工确认并删除"
            )
        if not policies:
            raise RuntimeError(f"{table} 没有隔离策略")
        for column in ("qual", "with_check"):
            if "app.current_enterprise_id" not in policies[0][column]:
                raise RuntimeError(
                    f"{table} 的策略 {policies[0]['policyname']}.{column} 没有引用租户 GUC"
                )


def _assert_no_blanket_privileges(protected: Sequence) -> None:
    """运行角色不得持有受保护清单以外表的权限。

    这是"不得用全表授权绕过 RLS"的机械保证：一旦有人为了跑通某条路径给
    ``autofde_app`` 授权到未纳入策略的表，迁移直接失败。
    """
    granted = (
        op.get_bind()
        .execute(
            text(
                "SELECT table_name FROM information_schema.role_table_grants "
                "WHERE grantee = :role AND table_schema = current_schema()"
            ),
            {"role": APP_ROLE},
        )
        .scalars()
        .all()
    )
    unexpected = sorted(set(granted) - set(protected))
    if unexpected:
        raise RuntimeError(
            f"RLS runtime role {APP_ROLE} holds privileges on unprotected tables: {unexpected}"
        )


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
    # 前向修复：已 stamp b6c7d8e9f0a1 的库不会重跑那条 ALTER ROLE。
    op.execute(
        f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    existing = _existing_tables()
    bind = op.get_bind()

    if bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_auth_members m "
            "JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=:role)"
        ),
        {"role": APP_ROLE},
    ).scalar():
        raise RuntimeError(f"RLS runtime role {APP_ROLE} must not be a member of other roles")
    if bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner "
            "WHERE r.rolname=:role AND c.relname = ANY(:tables))"
        ),
        {
            "role": APP_ROLE,
            "tables": list(DIRECT_TABLES) + list(INDIRECT_TENANT_TABLES) + [LEDGER_TABLE],
        },
    ).scalar():
        raise RuntimeError(f"RLS runtime role {APP_ROLE} must not own protected tables")

    # 1) 直接归属表：幂等重申（含上一轮 36 张的前向修复）。
    for table in DIRECT_TABLES:
        if table not in existing:
            raise RuntimeError(f"RLS policy table is missing: {table}")
        _apply_direct_policy(table)

    # 2) 间接归属表：按外键归属建策略。
    for table, template in INDIRECT_TENANT_TABLES.items():
        if table not in existing:
            raise RuntimeError(f"Indirect RLS table is missing: {table}")
        _apply_indirect_policy(table, template)

    # 3) 平台级账本 / 游标 / 目录。
    if LEDGER_TABLE not in existing:
        raise RuntimeError(f"Ledger table is missing: {LEDGER_TABLE}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE {LEDGER_TABLE} TO {APP_ROLE}")
    op.execute(f"ALTER TABLE {LEDGER_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {LEDGER_TABLE}_append ON {LEDGER_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS {_policy_name(LEDGER_TABLE)} ON {LEDGER_TABLE}")
    op.execute(
        f"CREATE POLICY {_policy_name(LEDGER_TABLE)} ON {LEDGER_TABLE} "
        "AS PERMISSIVE FOR SELECT USING ("
        f"EXISTS (SELECT 1 FROM users p WHERE p.id = {LEDGER_TABLE}.user_id "
        f"AND p.enterprise_id::text = {GUC}))"
    )
    # 写入同样按租户：必须存在租户上下文，且 user_id 属于本租户。
    # 曾经用 WITH CHECK (true) 放行"登录失败/账户锁定"这类无用户事件，代价是
    # 任何租户都能以别人的 user_id 伪造审计记录 —— 审计链因此失去可信度。
    # 匿名事件的正确通道是独立的受控写入入口（见 RLS_DEPLOYMENT.md，仍为 OPEN），
    # 不是"把插入放开"。
    op.execute(
        f"CREATE POLICY {_policy_name(LEDGER_TABLE)}_insert ON {LEDGER_TABLE} "
        "AS PERMISSIVE FOR INSERT WITH CHECK ("
        f"{LEDGER_TABLE}.user_id IS NOT NULL AND "
        f"EXISTS (SELECT 1 FROM users p WHERE p.id = {LEDGER_TABLE}.user_id "
        f"AND p.enterprise_id::text = {GUC}))"
    )

    for table in GLOBAL_CURSOR_TABLES:
        if table not in existing:
            raise RuntimeError(f"Global cursor table is missing: {table}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO {APP_ROLE}")
    for table in GLOBAL_CATALOG_TABLES:
        if table not in existing:
            raise RuntimeError(f"Global catalog table is missing: {table}")
        op.execute(f"GRANT SELECT ON TABLE {table} TO {APP_ROLE}")
    # 就绪探针要读迁移版本；缺这条授权会让生产健康检查在运行角色下永远 503。
    op.execute(f"GRANT SELECT ON TABLE {MIGRATION_METADATA_TABLE} TO {APP_ROLE}")
    # 4) 失败关闭：策略清单之外的多余/被放宽策略必须让迁移失败。
    _assert_policies_are_tenant_scoped(
        list(DIRECT_TABLES) + list(INDIRECT_TENANT_TABLES)
    )
    _assert_no_blanket_privileges(
        list(DIRECT_TABLES)
        + list(INDIRECT_TENANT_TABLES)
        + [LEDGER_TABLE]
        + list(GLOBAL_CURSOR_TABLES)
        + list(GLOBAL_CATALOG_TABLES)
        + list(AUTH_PATH_TABLES)
        + [MIGRATION_METADATA_TABLE]
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    existing = _existing_tables()

    for table in INDIRECT_TENANT_TABLES:
        if table in existing:
            op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
    for table in ADDED_DIRECT_TABLES:
        if table in existing:
            op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
    if LEDGER_TABLE in existing:
        op.execute(f"DROP POLICY IF EXISTS {LEDGER_TABLE}_append ON {LEDGER_TABLE}")
        op.execute(
            f"DROP POLICY IF EXISTS {_policy_name(LEDGER_TABLE)}_insert ON {LEDGER_TABLE}"
        )
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(LEDGER_TABLE)} ON {LEDGER_TABLE}")
        op.execute(f"ALTER TABLE {LEDGER_TABLE} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {LEDGER_TABLE} FROM {APP_ROLE}")
    for table in GLOBAL_CURSOR_TABLES + GLOBAL_CATALOG_TABLES:
        if table in existing:
            op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON TABLE {MIGRATION_METADATA_TABLE} FROM {APP_ROLE}")

    # 上一轮 36 张直接归属表由 b6c7d8e9f0a1 自己管理：降级必须**恢复历史策略名**
    # （{table}_enterprise_isolation），否则那条 revision 的 downgrade 找不到它
    # 预期的策略，后续迁移删除 enterprise_id 列时就会因策略依赖而失败。
    legacy_predicate = f"enterprise_id::text = {GUC}"
    for table in PREVIOUS_DIRECT_TABLES:
        if table not in existing:
            continue
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table}")
        op.execute(
            f"CREATE POLICY {_legacy_policy_name(table)} ON {table} "
            f"USING ({legacy_predicate}) WITH CHECK ({legacy_predicate})"
        )
