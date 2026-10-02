"""RLS 策略清单与 ORM 元数据的一致性测试（AUD-19）。

这些断言不需要 PostgreSQL：策略清单写在迁移里，权威事实是 ``Base.metadata``。
真实权限矩阵由 ``test_rls_postgres.py`` 在专用 PostgreSQL 上跑。

覆盖两类错误：
1. 清单里出现不存在的表/列（``runner_tasks.id`` 就是在建策略时才发现不存在）；
2. 新增业务表却没有任何租户归属声明 —— 静默漏掉 RLS 比报错更危险。
"""
import importlib.util
import pathlib
import re

import pytest

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"

REPO_MIGRATION = "2026_09_28_2200-b6c7d8e9f0a1_rls_runtime_role.py"
FORWARD_MIGRATION = "2026_09_29_0900-c2d3e4f5a6b8_rls_indirect_and_forward_fix.py"

#: 上一轮点名的 11 张间接归属表。audit_logs 以平台账本方式单独处理。
PREVIOUSLY_BLOCKED_INDIRECT = {
    "agent_versions",
    "matrix_tasks",
    "shared_blackboard_entries",
    "task_selection_bids",
    "runner_device_credentials",
    "runner_device_tokens",
    "runner_task_frames",
    "counterfactual_diffs",
    "optimization_histories",
    "rag_evaluations",
}


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(filename[:-3], MIGRATIONS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def forward():
    return _load(FORWARD_MIGRATION)


@pytest.fixture(scope="module")
def base_metadata():
    import app.models  # noqa: F401  确保所有模型已注册
    from app.database import Base

    return Base.metadata


def _parent_references(template: str) -> list:
    """抽出 ``FROM <table> <alias> ... <alias>.<column>`` 形式的父表列引用。"""
    references = []
    for match in re.finditer(r"FROM\s+(\w+)\s+([pg])\b", template):
        table, alias = match.group(1), match.group(2)
        for column in re.finditer(rf"\b{alias}\.(\w+)\s*=", template):
            references.append((table, column.group(1)))
    return references


class TestPolicyRegistryMatchesSchema:
    def test_every_policy_table_exists(self, forward, base_metadata):
        tables = set(base_metadata.tables)
        for table in list(forward.DIRECT_TABLES) + list(forward.INDIRECT_TENANT_TABLES):
            assert table in tables, f"策略清单引用了不存在的表: {table}"
        for table in forward.GLOBAL_CURSOR_TABLES + forward.GLOBAL_CATALOG_TABLES:
            assert table in tables, f"清单引用了不存在的表: {table}"
        assert forward.LEDGER_TABLE in tables

    def test_direct_tables_have_enterprise_id(self, forward, base_metadata):
        for table in forward.DIRECT_TABLES:
            assert "enterprise_id" in base_metadata.tables[table].columns, (
                f"{table} 被登记为直接归属表却没有 enterprise_id 列"
            )

    def test_indirect_tables_have_no_enterprise_id(self, forward, base_metadata):
        """间接归属表的意义就在于"不能直接比较 enterprise_id"。"""
        for table in forward.INDIRECT_TENANT_TABLES:
            assert "enterprise_id" not in base_metadata.tables[table].columns, (
                f"{table} 已有 enterprise_id，应登记为直接归属表"
            )

    def test_policy_templates_reference_real_columns(self, forward, base_metadata):
        """策略引用的每个父表列都必须真实存在（上一轮 runner_tasks.id 就是反例）。"""
        for table, template in forward.INDIRECT_TENANT_TABLES.items():
            references = _parent_references(template.format(alias=table))
            assert references, f"{table} 的策略没有引用任何父表列"
            for parent, column in references:
                assert parent in base_metadata.tables, f"{table}: 未知父表 {parent}"
                assert column in base_metadata.tables[parent].columns, (
                    f"{table}: 父表 {parent} 没有列 {column}"
                )

    def test_indirect_tables_reach_a_tenant_owned_table(self, forward, base_metadata):
        """每个间接归属表都必须能沿外键走到带 enterprise_id 的父表。"""
        direct = set(forward.DIRECT_TABLES)
        for name in forward.INDIRECT_TENANT_TABLES:
            reachable = _fk_closure(name, base_metadata)
            assert reachable & direct, (
                f"{name} 沿外键走不到任何直接归属表，无法按租户做 RLS: {sorted(reachable)}"
            )

    def test_every_business_table_declares_tenant_ownership(self, forward, base_metadata):
        """新增业务表必须显式登记归属，不能静默落在 RLS 之外。"""
        declared = (
            set(forward.DIRECT_TABLES)
            | set(forward.INDIRECT_TENANT_TABLES)
            | set(forward.AUTH_PATH_TABLES)
            | set(forward.GLOBAL_CURSOR_TABLES)
            | set(forward.GLOBAL_CATALOG_TABLES)
            | {forward.LEDGER_TABLE}
        )
        undeclared = sorted(set(base_metadata.tables) - declared)
        assert not undeclared, f"以下业务表没有声明租户归属，请显式登记: {undeclared}"

    def test_forward_migration_reasserts_previous_tables(self, forward):
        """前向修复必须覆盖上一轮的清单，否则 stamp 过的库不会重跑那条 ALTER ROLE。"""
        repo = _load(REPO_MIGRATION)
        assert set(repo.RLS_TENANT_TABLES) == set(forward.PREVIOUS_DIRECT_TABLES)
        assert set(forward.PREVIOUS_DIRECT_TABLES) <= set(forward.DIRECT_TABLES)

    def test_ledger_is_not_registered_as_tenant_table(self, forward):
        """audit_logs 是平台账本：既不能当直接表也不能当间接表登记。"""
        assert forward.LEDGER_TABLE not in forward.DIRECT_TABLES
        assert forward.LEDGER_TABLE not in forward.INDIRECT_TENANT_TABLES


def _fk_closure(table: str, metadata, depth: int = 4) -> set:
    """沿外键向上可到达的表集合（受控深度，避免环）。"""
    seen = set()
    frontier = [table]
    for _ in range(depth):
        nxt: set = set()
        for current in frontier:
            for column in metadata.tables[current].columns:
                for fk in column.foreign_keys:
                    parent = fk.column.table.name
                    if parent not in seen:
                        seen.add(parent)
                        nxt.add(parent)
        frontier = list(nxt)
    return seen
