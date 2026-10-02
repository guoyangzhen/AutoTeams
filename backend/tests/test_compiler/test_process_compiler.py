"""Process Compiler 测试（PRD §4.3 第三级）。

测试 SOP 提取 + 审批流建模 + KPI 关联。
LLM 调用全部 mock。
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.compiler.process_compiler import ProcessCompiler
from app.services.compiler.base import CompilationContext
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.compiler import GraphNode, GraphEdge


async def _setup_graph_with_process(db_session, enterprise_id):
    """辅助函数：在知识图谱中预设流程节点。"""
    store = PGJSONBGraphStore(db_session, enterprise_id)
    # 添加流程节点
    proc = GraphNode(
        node_id="proc_1",
        node_type=EntityType.PROCESS.value,
        name="采购审批流程",
        attributes={
            "description": "采购申请提交后，由部门经理审批，再由财务确认。",
            "type": "approval",
            "kpi_ids": ["kpi_1"],
        },
    )
    role = GraphNode(
        node_id="role_1",
        node_type=EntityType.ROLE.value,
        name="采购经理",
    )
    await store.add_node(proc)
    await store.add_node(role)
    # role EXECUTES proc
    await store.add_edge(GraphEdge(
        source_id="role_1",
        target_id="proc_1",
        relation=RelationType.EXECUTES.value,
    ))
    await store.save()


@pytest.mark.asyncio
async def test_process_compiler_no_processes(db_session):
    """测试无流程节点时的编译。"""
    compiler = ProcessCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_proc_1",
        db=db_session,
        upstream={},
    )
    result = await compiler.compile(ctx)

    assert result.stage == "process"
    assert len(result.output.processes) == 0


@pytest.mark.asyncio
async def test_process_compiler_with_sop(db_session):
    """测试 SOP 提取。"""
    await _setup_graph_with_process(db_session, "ent_proc_2")

    # Mock LLM 返回 SOP 步骤
    llm_response = '''[
        {"name": "提交采购申请", "order": 1, "approval_required": false},
        {"name": "部门经理审批", "order": 2, "approval_required": true, "approver_role": "采购经理"},
        {"name": "财务确认", "order": 3, "approval_required": true}
    ]'''

    compiler = ProcessCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_proc_2",
        db=db_session,
        upstream={},
    )

    with patch("app.services.compiler.process_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = llm_response
        result = await compiler.compile(ctx)

    assert result.stage == "process"
    assert len(result.output.processes) == 1
    proc = result.output.processes[0]
    assert proc.name == "采购审批流程"
    assert len(proc.steps) == 3
    assert proc.owner_role_id == "role_1"
    assert "kpi_1" in proc.kpi_ids


@pytest.mark.asyncio
async def test_process_compiler_approval_modeling(db_session):
    """测试审批流建模。"""
    await _setup_graph_with_process(db_session, "ent_proc_3")

    llm_response = '''[
        {"name": "步骤1", "order": 1, "approval_required": true},
        {"name": "步骤2", "order": 2, "approval_required": false}
    ]'''

    compiler = ProcessCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_proc_3",
        db=db_session,
        upstream={},
    )

    with patch("app.services.compiler.process_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = llm_response
        result = await compiler.compile(ctx)

    proc = result.output.processes[0]
    # 审批步骤应有审批人
    approval_step = next(s for s in proc.steps if s.get("approval_required"))
    assert approval_step.get("approver_role") == "采购经理"


@pytest.mark.asyncio
async def test_process_compiler_llm_failure(db_session):
    """测试 LLM 失败降级。"""
    await _setup_graph_with_process(db_session, "ent_proc_4")

    compiler = ProcessCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_proc_4",
        db=db_session,
        upstream={},
    )

    with patch("app.services.compiler.process_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = Exception("LLM 不可用")
        result = await compiler.compile(ctx)

    # 降级：LLM 失败且流程属性无显式步骤时，用规则模板生成可执行的 SOP 步骤，
    # 保证流程引擎的业务流程非空、可正常呈现（而非 0 步）。
    assert len(result.output.processes) == 1
    assert len(result.output.processes[0].steps) > 0
    # 采购审批流程命中的是「采购」模板：含审批步骤
    assert any(s.get("approval_required") for s in result.output.processes[0].steps)
