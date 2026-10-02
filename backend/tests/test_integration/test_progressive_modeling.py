"""渐进式建模集成测试。

覆盖 PRD §9.4 SOP v1/v2 进化对比设计：
1. SOP v1/v2 文件结构一致性
2. v1→v2 差异符合 PRD（金额分级审批 / SLA / 客服分级等）
3. Enterprise Runtime 支持版本切换（v1→v2 进化）

依据：
    - docs/重构方案_v3.md §9.6 阶段 4
    - docs/AutoTeams项目需求重新梳理产品需求文档.md §9.4
"""
import os
from sqlalchemy import text


SAMPLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "sample_data", "example-enterprise",
)


def _read(relpath: str) -> str:
    return open(os.path.join(SAMPLE_DIR, relpath), encoding="utf-8").read()


# ============================================================
# SOP v1/v2 结构一致性
# ============================================================


def test_sales_sop_v1_v2_exist():
    """销售 SOP v1/v2 文件均存在。"""
    assert os.path.isfile(os.path.join(SAMPLE_DIR, "02-sales/sales-sop-v1.md"))
    assert os.path.isfile(os.path.join(SAMPLE_DIR, "02-sales/sales-sop-v2.md"))


def test_service_sop_v1_v2_exist():
    """客服 SOP v1/v2 文件均存在。"""
    assert os.path.isfile(os.path.join(SAMPLE_DIR, "03-customer-service/service-sop-v1.md"))
    assert os.path.isfile(os.path.join(SAMPLE_DIR, "03-customer-service/service-sop-v2.md"))


def test_sop_v1_v2_have_numbered_steps():
    """SOP v1/v2 均包含步骤编号（结构一致便于 diff）。"""
    for relpath in [
        "02-sales/sales-sop-v1.md",
        "02-sales/sales-sop-v2.md",
        "03-customer-service/service-sop-v1.md",
        "03-customer-service/service-sop-v2.md",
    ]:
        content = _read(relpath)
        assert "步骤编号" in content or "## 步骤" in content or "### 步骤" in content, \
            f"{relpath} 缺少步骤编号"


# ============================================================
# v1→v2 差异符合 PRD §9.4
# ============================================================


def test_sales_sop_v2_has_tiered_approval():
    """销售 SOP v2 包含金额分级审批（V2 优化点）。"""
    v2 = _read("02-sales/sales-sop-v2.md")
    # V2 优化点：<5万经理 / 5-20万总监 / >20万 CEO
    assert any(kw in v2 for kw in ["分级", "5万", "20万", "经理", "总监"]), \
        "SOP v2 应包含金额分级审批规则"


def test_sales_sop_v2_has_response_sla():
    """销售 SOP v2 包含询盘响应 SLA（V2 优化点）。"""
    v2 = _read("02-sales/sales-sop-v2.md")
    assert any(kw in v2 for kw in ["4 小时", "24 小时", "SLA", "响应时效"]), \
        "SOP v2 应包含询盘响应 SLA"


def test_service_sop_v2_has_l1_l2_l3():
    """客服 SOP v2 包含 L1/L2/L3 分级（V2 优化点）。"""
    v2 = _read("03-customer-service/service-sop-v2.md")
    assert any(kw in v2 for kw in ["L1", "L2", "L3", "分级"]), \
        "SOP v2 应包含客服分级（L1/L2/L3）"


def test_sop_v1_v2_content_different():
    """SOP v1 与 v2 内容不同（确保有进化差异）。"""
    for v1_path, v2_path in [
        ("02-sales/sales-sop-v1.md", "02-sales/sales-sop-v2.md"),
        ("03-customer-service/service-sop-v1.md", "03-customer-service/service-sop-v2.md"),
    ]:
        v1 = _read(v1_path)
        v2 = _read(v2_path)
        assert v1 != v2, f"{v1_path} 与 {v2_path} 内容相同，缺少进化差异"


# ============================================================
# Enterprise Runtime 版本管理
# ============================================================


async def test_runtime_supports_version_management(demo_enterprise, db_session):
    """Enterprise Runtime 表支持版本管理（version + is_active 字段）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT version, is_active FROM enterprise_runtimes "
            "WHERE enterprise_id = :eid"
        ),
        {"eid": enterprise.id},
    )
    row = result.fetchone()
    assert row is not None, "Runtime 不存在"
    version, is_active = row
    assert version == "v3.0.0"
    assert is_active == 1


async def test_runtime_versions_table_exists(demo_enterprise, db_session):
    """runtime_versions 表存在且可插入。"""
    enterprise, agents, users = demo_enterprise

    # 尝试查询 runtime_versions 表
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM runtime_versions"))
    assert result.scalar() == 0  # 初始为空

    # 验证表结构（runtime_id / version / changelog / is_active）
    result = await db_session.execute(
        text("PRAGMA table_info(runtime_versions)"))
    columns = {row[1] for row in result.fetchall()}
    expected = {"id", "runtime_id", "version", "changelog", "is_active", "created_by", "created_at"}
    assert expected.issubset(columns), \
        f"runtime_versions 表缺少字段: {expected - columns}"


# ============================================================
# 无占位符标记
# ============================================================


def test_no_placeholder_markers():
    """SOP v1/v2 无占位符标记（TODO/TBD/placeholder 等）。"""
    placeholder_patterns = ["测试数据", "示例数据", "TODO", "TBD", "placeholder", "lorem"]
    for relpath in [
        "02-sales/sales-sop-v1.md",
        "02-sales/sales-sop-v2.md",
        "03-customer-service/service-sop-v1.md",
        "03-customer-service/service-sop-v2.md",
    ]:
        content = _read(relpath).lower()
        for p in placeholder_patterns:
            assert p.lower() not in content, f"{relpath} 包含占位符 '{p}'"
