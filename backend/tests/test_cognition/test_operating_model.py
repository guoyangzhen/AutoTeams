"""企业运行模型测试（PRD §4.2 数据结构定义 3）。"""
import pytest
from unittest.mock import AsyncMock

from app.services.cognition.operating_model import OperatingModelBuilder
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.compiler import GraphNode, GraphEdge


async def _setup_graph_with_full_data(db_session, enterprise_id):
    """预设完整的知识图谱数据。"""
    store = PGJSONBGraphStore(db_session, enterprise_id)
    dept = GraphNode(
        node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部",
        attributes={"parent_id": None, "responsibilities": ["销售管理"]},
    )
    role = GraphNode(
        node_id="role_1", node_type=EntityType.ROLE.value, name="销售经理",
        attributes={
            "level": "L2",
            "required_skills": ["客户沟通"],
            "knowledge_sources": ["产品手册"],
            "tools": ["CRM"],
            "responsibilities": ["管理销售团队"],
        },
    )
    proc = GraphNode(
        node_id="proc_1", node_type=EntityType.PROCESS.value, name="采购流程",
        attributes={
            "type": "sop",
            "steps": [{"step_id": "s1", "name": "申请", "order": 1}],
            "participants": ["role_1"],
            "trigger_event": "manual",
            "system_ids": ["sys_1"],
        },
    )
    perm = GraphNode(node_id="perm_1", node_type=EntityType.PERMISSION.value, name="合同审批")
    kpi = GraphNode(node_id="kpi_1", node_type=EntityType.KPI.value, name="月销售额")
    system = GraphNode(node_id="sys_1", node_type=EntityType.SYSTEM.value, name="CRM系统")

    for n in [dept, role, proc, perm, kpi, system]:
        await store.add_node(n)

    # 关系
    await store.add_edge(GraphEdge(source_id="role_1", target_id="dept_1", relation=RelationType.BELONGS_TO.value))
    await store.add_edge(GraphEdge(source_id="role_1", target_id="proc_1", relation=RelationType.EXECUTES.value))
    await store.add_edge(GraphEdge(source_id="role_1", target_id="kpi_1", relation=RelationType.OWES.value))
    await store.add_edge(GraphEdge(source_id="role_1", target_id="perm_1", relation=RelationType.HAS_PERMISSION.value))
    await store.add_edge(GraphEdge(source_id="proc_1", target_id="sys_1", relation=RelationType.USES.value))
    await store.save()


@pytest.mark.asyncio
async def test_build_model(db_session):
    """测试运行模型构建。"""
    await _setup_graph_with_full_data(db_session, "ent_om_1")
    builder = OperatingModelBuilder(db_session, "ent_om_1")
    model = await builder.build_model()

    assert model.version == "v1.0.0"
    assert len(model.roles) == 1
    assert model.roles[0].title == "销售经理"
    assert model.roles[0].department == "销售部"
    assert "kpi_1" in model.roles[0].kpi_ids
    assert "perm_1" in model.roles[0].permission_ids
    assert len(model.processes) == 1
    assert len(model.capabilities) == 1


@pytest.mark.asyncio
async def test_build_organization(db_session):
    """测试组织架构构建。"""
    await _setup_graph_with_full_data(db_session, "ent_om_2")
    builder = OperatingModelBuilder(db_session, "ent_om_2")
    model = await builder.build_model()

    assert "departments" in model.organization
    assert len(model.organization["departments"]) == 1
    assert model.organization["departments"][0]["name"] == "销售部"


@pytest.mark.asyncio
async def test_save_and_get_model(db_session):
    """测试模型保存和查询。"""
    await _setup_graph_with_full_data(db_session, "ent_om_3")
    builder = OperatingModelBuilder(db_session, "ent_om_3")

    model = await builder.build_model()
    record = await builder.save_model(model)
    assert record.id is not None

    # 查询
    retrieved = await builder.get_active_model()
    assert retrieved is not None
    assert len(retrieved.roles) == 1


@pytest.mark.asyncio
async def test_get_model_not_found(db_session):
    """测试查询不存在的模型。"""
    builder = OperatingModelBuilder(db_session, "ent_om_4")
    result = await builder.get_active_model()
    assert result is None


@pytest.mark.asyncio
async def test_identify_gaps(db_session):
    """测试知识空白识别。"""
    # 只创建部门，不创建流程
    store = PGJSONBGraphStore(db_session, "ent_om_5")
    await store.add_node(GraphNode(node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部"))
    await store.save()

    builder = OperatingModelBuilder(db_session, "ent_om_5")
    model = await builder.build_model()
    # 应识别出缺失的流程
    assert len(model.gaps) > 0
