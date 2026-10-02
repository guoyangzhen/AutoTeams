"""企业画像生成测试（PRD §4.2 数据结构定义 2）。"""
import pytest
from unittest.mock import AsyncMock

from app.services.cognition.enterprise_profiler import EnterpriseProfiler
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.compiler import GraphNode, GraphEdge


async def _setup_graph(db_session, enterprise_id):
    """预设知识图谱数据。"""
    store = PGJSONBGraphStore(db_session, enterprise_id)
    dept = GraphNode(
        node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部",
        attributes={"industry": "科技"},
    )
    role = GraphNode(node_id="role_1", node_type=EntityType.ROLE.value, name="销售经理")
    product = GraphNode(node_id="prod_1", node_type=EntityType.PRODUCT.value, name="产品A")
    customer = GraphNode(
        node_id="cust_1", node_type=EntityType.CUSTOMER.value, name="客户X",
        attributes={"industry": "金融"},
    )
    proc = GraphNode(node_id="proc_1", node_type=EntityType.PROCESS.value, name="销售流程")
    for n in [dept, role, product, customer, proc]:
        await store.add_node(n)
    await store.save()


@pytest.mark.asyncio
async def test_generate_profile(db_session):
    """测试企业画像生成。"""
    await _setup_graph(db_session, "ent_prof_1")
    profiler = EnterpriseProfiler(db_session, "ent_prof_1")
    profile = await profiler.generate_profile()

    assert profile.basic.industry == "科技"
    assert profile.org_summary.department_count == 1
    assert profile.org_summary.headcount == 1
    assert "销售经理" in profile.org_summary.key_roles
    assert "产品A" in profile.business.main_products
    assert "金融" in profile.business.target_industries
    assert "销售流程" in profile.business.core_processes


@pytest.mark.asyncio
async def test_generate_tags(db_session):
    """测试标签生成。"""
    await _setup_graph(db_session, "ent_prof_2")
    profiler = EnterpriseProfiler(db_session, "ent_prof_2")
    profile = await profiler.generate_profile()
    assert len(profile.tags) > 0


@pytest.mark.asyncio
async def test_identify_gaps(db_session):
    """测试知识空白识别。"""
    # 只创建部门，不创建流程/产品/系统
    store = PGJSONBGraphStore(db_session, "ent_prof_3")
    await store.add_node(GraphNode(node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部"))
    await store.save()

    profiler = EnterpriseProfiler(db_session, "ent_prof_3")
    profile = await profiler.generate_profile()
    # 应识别出缺失的流程和产品
    assert len(profile.gaps) > 0


@pytest.mark.asyncio
async def test_save_and_get_profile(db_session):
    """测试画像保存和查询。"""
    await _setup_graph(db_session, "ent_prof_4")
    profiler = EnterpriseProfiler(db_session, "ent_prof_4")

    profile = await profiler.generate_profile()
    record = await profiler.save_profile(profile)
    assert record.id is not None

    # 查询
    retrieved = await profiler.get_profile()
    assert retrieved is not None
    assert retrieved.basic.industry == "科技"


@pytest.mark.asyncio
async def test_get_profile_not_found(db_session):
    """测试查询不存在的画像。"""
    profiler = EnterpriseProfiler(db_session, "ent_prof_5")
    result = await profiler.get_profile()
    assert result is None
