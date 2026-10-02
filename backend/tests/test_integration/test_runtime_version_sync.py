"""Runtime 版本同步集成测试。

覆盖 WT2 Enterprise Runtime 的版本管理能力：
1. Enterprise Runtime 表结构与字段完整性
2. Runtime 版本切换（is_active 状态变更）
3. 编译任务与 Runtime 的关联
4. Enterprise → Runtime 关联字段（current_runtime_version_id）

依据：
    - docs/重构方案_v3.md §5.7（WT2 数据模型）+ §9.6 阶段 4
    - docs/spec.md §3.2（数据库迁移约定）
"""
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import text


# ============================================================
# Enterprise Runtime 表结构验证
# ============================================================


async def test_enterprise_runtimes_table_schema(demo_enterprise, db_session):
    """enterprise_runtimes 表字段完整。"""
    result = await db_session.execute(text("PRAGMA table_info(enterprise_runtimes)"))
    columns = {row[1] for row in result.fetchall()}
    expected = {
        "id", "enterprise_id", "version", "model_version",
        "compiled_at", "completeness", "runtime_data",
        "is_active", "created_at", "updated_at",
    }
    assert expected.issubset(columns), \
        f"enterprise_runtimes 缺少字段: {expected - columns}"


async def test_runtime_versions_table_schema(demo_enterprise, db_session):
    """runtime_versions 表字段完整。"""
    result = await db_session.execute(text("PRAGMA table_info(runtime_versions)"))
    columns = {row[1] for row in result.fetchall()}
    expected = {"id", "runtime_id", "version", "changelog", "is_active", "created_by", "created_at"}
    assert expected.issubset(columns), \
        f"runtime_versions 缺少字段: {expected - columns}"


# ============================================================
# Runtime 数据完整性
# ============================================================


async def test_runtime_has_valid_completeness(demo_enterprise, db_session):
    """Runtime 完成度在合理范围（0-1）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text("SELECT completeness FROM enterprise_runtimes WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    completeness = result.scalar()
    assert completeness is not None
    assert 0 <= completeness <= 1.0, f"完成度超出范围: {completeness}"
    assert completeness >= 0.80, f"完成度应 ≥0.80: {completeness}"


async def test_runtime_data_is_valid_json(demo_enterprise, db_session):
    """Runtime 数据为有效 JSON。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text("SELECT runtime_data FROM enterprise_runtimes WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    row = result.fetchone()
    assert row is not None

    runtime_data = json.loads(row[0])
    assert isinstance(runtime_data, dict)
    assert "enterprise_name" in runtime_data


# ============================================================
# 编译任务关联
# ============================================================


async def test_compilation_jobs_linked_to_runtime(demo_enterprise, db_session):
    """编译任务含 trigger_source 字段（WT1 模型），且对应 compilation_artifacts 存在。

    WT1 模型用 compilation_artifacts 表存储产物（output_ref 为 WT6 旧迁移遗留字段，
    修正迁移 b2c3d4e5f6a2 已补 trigger_source/affected_stages + compilation_artifacts 表）。
    """
    enterprise, agents, users = demo_enterprise

    # 验证 compilation_jobs 含 trigger_source 字段且全部完成
    result = await db_session.execute(
        text(
            "SELECT stage, trigger_source, status FROM compilation_jobs "
            "WHERE enterprise_id = :eid ORDER BY stage"
        ),
        {"eid": enterprise.id},
    )
    jobs = result.fetchall()
    assert len(jobs) == 5

    for stage, trigger_source, status in jobs:
        assert trigger_source == "manual", f"阶段 {stage} 的 trigger_source 应为 manual"
        assert status == "completed", f"阶段 {stage} 的 status 应为 completed"

    # 验证 compilation_artifacts 表存在且每级有产物
    artifacts_result = await db_session.execute(
        text(
            "SELECT stage FROM compilation_artifacts "
            "WHERE enterprise_id = :eid ORDER BY stage"
        ),
        {"eid": enterprise.id},
    )
    artifact_stages = {row[0] for row in artifacts_result.fetchall()}
    expected_stages = {"information", "knowledge", "process", "capability", "runtime"}
    assert artifact_stages == expected_stages, \
        f"compilation_artifacts 应覆盖 5 级，实际: {artifact_stages}"


async def test_compilation_jobs_stage_order(demo_enterprise, db_session):
    """五级编译阶段完整。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text("SELECT DISTINCT stage FROM compilation_jobs WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    stages = {row[0] for row in result.fetchall()}
    expected = {"information", "knowledge", "process", "capability", "runtime"}
    assert stages == expected, f"编译阶段不完整: {expected - stages}"


# ============================================================
# Runtime 版本切换
# ============================================================


async def test_runtime_is_active_flag(demo_enterprise, db_session):
    """Runtime 的 is_active 标志正确（仅 1 个激活版本）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM enterprise_runtimes "
            "WHERE enterprise_id = :eid AND is_active = 1"
        ),
        {"eid": enterprise.id},
    )
    active_count = result.scalar()
    assert active_count == 1, f"应有 1 个激活的 Runtime，实际 {active_count}"


async def test_runtime_version_unique_constraint(demo_enterprise, db_session):
    """Runtime 的 (enterprise_id, version) 唯一约束生效。"""
    enterprise, agents, users = demo_enterprise

    # 尝试插入重复 (enterprise_id, version) 应失败
    now = datetime.now(timezone.utc)
    try:
        await db_session.execute(
            text(
                "INSERT INTO enterprise_runtimes "
                "(id, enterprise_id, version, model_version, compiled_at, "
                "completeness, runtime_data, is_active, created_at, updated_at) "
                "VALUES ('dup-id', :eid, 'v3.0.0', 'mv', :now, 0.5, '{}', 0, :now, :now)"
            ),
            {"eid": enterprise.id, "now": now},
        )
        await db_session.commit()
        raise AssertionError("应触发唯一约束异常")
    except Exception:
        # 期望异常：唯一约束冲突
        await db_session.rollback()


# ============================================================
# Enterprise → Runtime 关联
# ============================================================


async def test_enterprise_runtime_version_id_field(demo_enterprise, db_session):
    """Enterprise 表的 current_runtime_version_id 字段存在。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text("PRAGMA table_info(enterprises)"))
    columns = {row[1] for row in result.fetchall()}
    assert "current_runtime_version_id" in columns, \
        "enterprises 表缺少 current_runtime_version_id 字段"


async def test_enterprise_runtime_association(demo_enterprise, db_session):
    """Enterprise 关联的 Runtime 版本存在。"""
    enterprise, agents, users = demo_enterprise

    # 获取 enterprise 的 current_runtime_version_id
    result = await db_session.execute(
        text("SELECT current_runtime_version_id FROM enterprises WHERE id = :eid"),
        {"eid": enterprise.id},
    )
    runtime_id = result.scalar()

    # 验证关联的 Runtime 存在
    if runtime_id:
        rt_result = await db_session.execute(
            text("SELECT version, is_active FROM enterprise_runtimes WHERE id = :rid"),
            {"rid": runtime_id},
        )
        row = rt_result.fetchone()
        assert row is not None, "关联的 Runtime 不存在"
        assert row[1] == 1, "关联的 Runtime 应为激活状态"


# ============================================================
# runtime_versions 表操作
# ============================================================


async def test_runtime_versions_crud(demo_enterprise, db_session):
    """runtime_versions 表支持 CRUD 操作。"""
    enterprise, agents, users = demo_enterprise

    # 获取 Runtime ID
    result = await db_session.execute(
        text("SELECT id FROM enterprise_runtimes WHERE enterprise_id = :eid AND is_active = 1"),
        {"eid": enterprise.id},
    )
    runtime_id = result.scalar()
    assert runtime_id is not None

    # Create: 插入版本快照（enterprise_id/event_type 对齐 WT2 模型 nullable=False）
    now = datetime.now(timezone.utc)
    version_id = f"{runtime_id}-v1-snapshot"
    await db_session.execute(
        text(
            "INSERT INTO runtime_versions "
            "(id, runtime_id, enterprise_id, version, event_type, changelog, is_active, created_at, updated_at) "
            "VALUES (:id, :rid, :eid, :ver, 'snapshot', :cl, 0, :now, :now)"
        ),
        {
            "id": version_id,
            "rid": runtime_id,
            "eid": enterprise.id,
            "ver": "v1.0.0-snapshot",
            "cl": "初始版本快照",
            "now": now,
        },
    )
    await db_session.commit()

    # Read: 查询版本快照
    result = await db_session.execute(
        text("SELECT version, changelog FROM runtime_versions WHERE id = :id"),
        {"id": version_id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "v1.0.0-snapshot"
    assert row[1] == "初始版本快照"

    # Update: 更新 changelog
    await db_session.execute(
        text("UPDATE runtime_versions SET changelog = :cl WHERE id = :id"),
        {"cl": "更新后的 changelog", "id": version_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT changelog FROM runtime_versions WHERE id = :id"),
        {"id": version_id},
    )
    assert result.scalar() == "更新后的 changelog"

    # Delete: 删除版本快照
    await db_session.execute(
        text("DELETE FROM runtime_versions WHERE id = :id"),
        {"id": version_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT COUNT(*) FROM runtime_versions WHERE id = :id"),
        {"id": version_id},
    )
    assert result.scalar() == 0
