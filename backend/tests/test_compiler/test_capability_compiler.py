"""Capability Compiler 测试（PRD §4.3 第四级）。

测试岗位能力矩阵构建 + CapabilityMatrix 契约（spec.md §10.3）。
"""
import pytest
from datetime import datetime

from app.services.compiler.capability_compiler import CapabilityCompiler
from app.services.compiler.base import CompilationContext
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.compiler import GraphNode, GraphEdge


async def _setup_graph_with_roles(db_session, enterprise_id):
    """辅助函数：预设角色和关联节点。"""
    store = PGJSONBGraphStore(db_session, enterprise_id)
    dept = GraphNode(node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部")
    role = GraphNode(
        node_id="role_1",
        node_type=EntityType.ROLE.value,
        name="销售经理",
        attributes={
            "level": "L2",
            "required_skills": ["客户沟通", "合同管理"],
            "knowledge_domains": ["产品知识"],
        },
    )
    proc = GraphNode(node_id="proc_1", node_type=EntityType.PROCESS.value, name="销售流程")
    kpi = GraphNode(node_id="kpi_1", node_type=EntityType.KPI.value, name="月销售额")
    perm = GraphNode(node_id="perm_1", node_type=EntityType.PERMISSION.value, name="合同审批")
    system = GraphNode(node_id="sys_1", node_type=EntityType.SYSTEM.value, name="CRM")

    for n in [dept, role, proc, kpi, perm, system]:
        await store.add_node(n)

    # 关系
    await store.add_edge(GraphEdge(source_id="role_1", target_id="dept_1", relation=RelationType.BELONGS_TO.value))
    await store.add_edge(GraphEdge(source_id="role_1", target_id="proc_1", relation=RelationType.EXECUTES.value))
    await store.add_edge(GraphEdge(source_id="role_1", target_id="kpi_1", relation=RelationType.OWES.value))
    await store.add_edge(GraphEdge(source_id="role_1", target_id="perm_1", relation=RelationType.HAS_PERMISSION.value))
    await store.add_edge(GraphEdge(source_id="proc_1", target_id="sys_1", relation=RelationType.USES.value))
    await store.save()


@pytest.mark.asyncio
async def test_capability_compiler_basic(db_session):
    """测试基本能力矩阵构建。"""
    await _setup_graph_with_roles(db_session, "ent_cap_1")

    compiler = CapabilityCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_cap_1",
        db=db_session,
        upstream={},
    )
    result = await compiler.compile(ctx)

    assert result.stage == "capability"
    assert len(result.output.capability_matrix.positions) == 1

    pos = result.output.capability_matrix.positions[0]
    assert pos.position_id == "role_1"
    assert pos.position_name == "销售经理"
    assert pos.department == "销售部"
    assert pos.level == "L2"
    assert len(pos.required_skills) >= 2
    assert "kpi_1" in pos.kpi_ids
    assert "合同审批" in pos.required_permissions
    assert "proc_1" in pos.main_processes


@pytest.mark.asyncio
async def test_capability_matrix_contract(db_session):
    """验证 CapabilityMatrix 契约（spec.md §10.3）。"""
    await _setup_graph_with_roles(db_session, "ent_cap_2")

    compiler = CapabilityCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_cap_2",
        db=db_session,
        upstream={},
    )
    result = await compiler.compile(ctx)

    cm = result.output.capability_matrix
    # 契约字段验证
    assert hasattr(cm, "enterprise_id")
    assert hasattr(cm, "positions")
    assert hasattr(cm, "compiled_at")
    assert hasattr(cm, "confidence")
    assert cm.enterprise_id == "ent_cap_2"
    assert isinstance(cm.compiled_at, datetime)
    assert cm.confidence > 0

    # PositionCapability 字段验证
    pos = cm.positions[0]
    assert hasattr(pos, "position_id")
    assert hasattr(pos, "position_name")
    assert hasattr(pos, "required_skills")
    assert hasattr(pos, "required_knowledge")
    assert hasattr(pos, "required_tools")
    assert hasattr(pos, "required_permissions")
    assert hasattr(pos, "kpi_ids")
    assert hasattr(pos, "main_processes")
    assert hasattr(pos, "priority")


@pytest.mark.asyncio
async def test_capability_priority_determination(db_session):
    """测试岗位优先级判定。"""
    store = PGJSONBGraphStore(db_session, "ent_cap_3")
    # 添加一个执行 3 个流程的岗位 → P0
    role = GraphNode(node_id="role_heavy", node_type=EntityType.ROLE.value, name="主管")
    await store.add_node(role)
    for i in range(3):
        proc = GraphNode(node_id=f"proc_{i}", node_type=EntityType.PROCESS.value, name=f"流程{i}")
        await store.add_node(proc)
        await store.add_edge(GraphEdge(
            source_id="role_heavy", target_id=f"proc_{i}", relation=RelationType.EXECUTES.value
        ))
    await store.save()

    compiler = CapabilityCompiler()
    ctx = CompilationContext(enterprise_id="ent_cap_3", db=db_session, upstream={})
    result = await compiler.compile(ctx)

    pos = result.output.capability_matrix.positions[0]
    assert pos.priority == "P0"


@pytest.mark.asyncio
async def test_capability_compiler_no_roles(db_session):
    """测试无角色时的编译。"""
    compiler = CapabilityCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_cap_4",
        db=db_session,
        upstream={},
    )
    result = await compiler.compile(ctx)

    assert result.stage == "capability"
    assert len(result.output.capability_matrix.positions) == 0


@pytest.mark.asyncio
async def test_capability_compiler_dedup_same_position(db_session):
    """#14 岗位去重：同一岗位名+部门的多份文档应聚合为一个岗位，而非逐节点生成。

    知识图谱中「销售代表」可能因组织架构、SOP、权限表反复出现而生成多个 ROLE 节点
    （此前导致 117 个岗位）。按「岗位名+部门」聚合后应合并权限/流程，收敛为 1 个。
    """
    store = PGJSONBGraphStore(db_session, "ent_cap_5")
    dept = GraphNode(node_id="dept_s", node_type=EntityType.DEPARTMENT.value, name="销售部")
    role1 = GraphNode(node_id="role_s1", node_type=EntityType.ROLE.value, name="销售代表")
    role2 = GraphNode(node_id="role_s2", node_type=EntityType.ROLE.value, name="销售代表")
    proc1 = GraphNode(node_id="proc_s1", node_type=EntityType.PROCESS.value, name="客户接洽流程")
    proc2 = GraphNode(node_id="proc_s2", node_type=EntityType.PROCESS.value, name="报价审批流程")
    perm1 = GraphNode(node_id="perm_s1", node_type=EntityType.PERMISSION.value, name="查看客户")
    perm2 = GraphNode(node_id="perm_s2", node_type=EntityType.PERMISSION.value, name="报价审批")
    for n in [dept, role1, role2, proc1, proc2, perm1, perm2]:
        await store.add_node(n)
    # 两个同岗位节点都归属销售部
    await store.add_edge(GraphEdge(source_id="role_s1", target_id="dept_s", relation=RelationType.BELONGS_TO.value))
    await store.add_edge(GraphEdge(source_id="role_s2", target_id="dept_s", relation=RelationType.BELONGS_TO.value))
    # 各自关联不同流程/权限
    await store.add_edge(GraphEdge(source_id="role_s1", target_id="proc_s1", relation=RelationType.EXECUTES.value))
    await store.add_edge(GraphEdge(source_id="role_s2", target_id="proc_s2", relation=RelationType.EXECUTES.value))
    await store.add_edge(GraphEdge(source_id="role_s1", target_id="perm_s1", relation=RelationType.HAS_PERMISSION.value))
    await store.add_edge(GraphEdge(source_id="role_s2", target_id="perm_s2", relation=RelationType.HAS_PERMISSION.value))
    await store.save()

    compiler = CapabilityCompiler()
    ctx = CompilationContext(enterprise_id="ent_cap_5", db=db_session, upstream={})
    result = await compiler.compile(ctx)

    positions = result.output.capability_matrix.positions
    # 两个 ROLE 节点同岗位名+部门 → 聚合为 1 个岗位
    assert len(positions) == 1
    pos = positions[0]
    assert pos.position_name == "销售代表"
    assert pos.department == "销售部"
    # 合并了两节点的流程与权限
    assert "proc_s1" in pos.main_processes
    assert "proc_s2" in pos.main_processes
    assert "查看客户" in pos.required_permissions
    assert "报价审批" in pos.required_permissions
