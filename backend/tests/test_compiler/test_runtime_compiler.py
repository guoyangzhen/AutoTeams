"""Runtime Compiler 测试（PRD §4.3 第五级 + §4.6）。

测试 Enterprise Runtime 整合 + RuntimeCompileResult 契约（spec.md §10.2）。
"""
import pytest
from datetime import datetime

from app.services.compiler.runtime_compiler import RuntimeCompiler
from app.services.compiler.base import CompilationContext
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.compiler import (
    GraphNode,
    GraphEdge,
    CapabilityMatrix,
    CapabilityCompileOutput,
    ProcessCompileOutput,
    ProcessDefinition,
    KnowledgeCompileOutput,
)
from app.utils.time import utcnow


async def _setup_graph_with_departments(db_session, enterprise_id):
    """预设部门节点。"""
    store = PGJSONBGraphStore(db_session, enterprise_id)
    dept = GraphNode(node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部")
    await store.add_node(dept)
    await store.save()


def _make_capability_output(enterprise_id):
    """构造 Capability 级输出。"""
    from app.schemas.compiler import PositionCapability, SkillRequirement
    cm = CapabilityMatrix(
        enterprise_id=enterprise_id,
        positions=[PositionCapability(
            position_id="role_1",
            position_name="销售经理",
            department="销售部",
            level="L2",
            required_skills=[SkillRequirement(skill_name="客户沟通")],
            main_processes=["proc_1"],
            priority="P1",
        )],
        compiled_at=utcnow(),
        confidence=0.8,
    )
    return CapabilityCompileOutput(enterprise_id=enterprise_id, capability_matrix=cm)


def _make_process_output(enterprise_id):
    """构造 Process 级输出。"""
    return ProcessCompileOutput(
        enterprise_id=enterprise_id,
        processes=[ProcessDefinition(
            process_id="proc_1",
            name="销售流程",
            process_type="sop",
            steps=[{"step_id": "s1", "name": "接洽", "order": 1, "approval_required": False}],
            owner_role_id="role_1",
            participants=["role_1"],
        )],
        confidence=0.7,
    )


def _make_knowledge_output(enterprise_id):
    """构造 Knowledge 级输出。"""
    return KnowledgeCompileOutput(
        enterprise_id=enterprise_id,
        nodes=[GraphNode(node_id="n1", node_type="Role", name="角色1")],
        edges=[],
        vector_collection=f"enterprise_{enterprise_id}",
        confidence=0.75,
    )


@pytest.mark.asyncio
async def test_runtime_compiler_basic(db_session):
    """测试基本 Runtime 编译。"""
    enterprise_id = "ent_rt_1"
    await _setup_graph_with_departments(db_session, enterprise_id)

    compiler = RuntimeCompiler()
    ctx = CompilationContext(
        enterprise_id=enterprise_id,
        db=db_session,
        upstream={
            "knowledge": _make_knowledge_output(enterprise_id),
            "process": _make_process_output(enterprise_id),
            "capability": _make_capability_output(enterprise_id),
        },
    )
    result = await compiler.compile(ctx)

    assert result.stage == "runtime"
    runtime = result.output.runtime
    assert runtime is not None
    assert len(runtime.agents) == 1
    assert runtime.agents[0].agent_name == "销售经理"
    assert len(runtime.process_engines) == 1
    assert len(runtime.organization.departments) == 1


@pytest.mark.asyncio
async def test_runtime_compile_result_contract(db_session):
    """验证 RuntimeCompileResult 契约（spec.md §10.2）。"""
    enterprise_id = "ent_rt_2"
    await _setup_graph_with_departments(db_session, enterprise_id)

    compiler = RuntimeCompiler()
    ctx = CompilationContext(
        enterprise_id=enterprise_id,
        db=db_session,
        upstream={
            "knowledge": _make_knowledge_output(enterprise_id),
            "process": _make_process_output(enterprise_id),
            "capability": _make_capability_output(enterprise_id),
        },
    )
    result = await compiler.compile(ctx)
    runtime = result.output.runtime

    # §10.2 契约字段验证
    assert hasattr(runtime, "version")
    assert hasattr(runtime, "model_version")
    assert hasattr(runtime, "compiled_at")
    assert hasattr(runtime, "completeness")
    assert hasattr(runtime, "organization")
    assert hasattr(runtime, "agents")
    assert hasattr(runtime, "process_engines")
    assert hasattr(runtime, "collaboration_graph")
    assert hasattr(runtime, "knowledge_index")
    assert hasattr(runtime, "tool_registry")

    assert isinstance(runtime.compiled_at, datetime)
    assert runtime.version == "v1.0.0"

    # Agent 配置模板字段验证
    agent = runtime.agents[0]
    assert hasattr(agent, "agent_id")
    assert hasattr(agent, "agent_name")
    assert hasattr(agent, "role_id")
    assert hasattr(agent, "system_prompt")
    assert hasattr(agent, "skills")
    assert hasattr(agent, "tools")
    assert hasattr(agent, "memory_config")
    assert hasattr(agent, "status")

    # 流程引擎字段验证
    engine = runtime.process_engines[0]
    assert hasattr(engine, "engine_id")
    assert hasattr(engine, "process_id")
    assert hasattr(engine, "steps")
    assert hasattr(engine, "triggers")


@pytest.mark.asyncio
async def test_runtime_system_prompt_generation(db_session):
    """测试 Agent 系统 Prompt 生成。"""
    enterprise_id = "ent_rt_3"
    await _setup_graph_with_departments(db_session, enterprise_id)

    compiler = RuntimeCompiler()
    ctx = CompilationContext(
        enterprise_id=enterprise_id,
        db=db_session,
        upstream={
            "knowledge": _make_knowledge_output(enterprise_id),
            "process": _make_process_output(enterprise_id),
            "capability": _make_capability_output(enterprise_id),
        },
    )
    result = await compiler.compile(ctx)

    agent = result.output.runtime.agents[0]
    assert "销售经理" in agent.system_prompt
    assert "销售部" in agent.system_prompt


@pytest.mark.asyncio
async def test_runtime_collaboration_graph(db_session):
    """测试协作关系图构建。"""
    enterprise_id = "ent_rt_4"
    await _setup_graph_with_departments(db_session, enterprise_id)

    # 构造两个同部门岗位
    from app.schemas.compiler import PositionCapability
    cm = CapabilityMatrix(
        enterprise_id=enterprise_id,
        positions=[
            PositionCapability(position_id="r1", position_name="经理", department="销售部", priority="P1"),
            PositionCapability(position_id="r2", position_name="专员", department="销售部", priority="P2"),
        ],
        compiled_at=utcnow(),
        confidence=0.8,
    )

    compiler = RuntimeCompiler()
    ctx = CompilationContext(
        enterprise_id=enterprise_id,
        db=db_session,
        upstream={
            "knowledge": _make_knowledge_output(enterprise_id),
            "process": _make_process_output(enterprise_id),
            "capability": CapabilityCompileOutput(enterprise_id=enterprise_id, capability_matrix=cm),
        },
    )
    result = await compiler.compile(ctx)

    # 同部门应有协作关系
    collab_edges = result.output.runtime.collaboration_graph.edges
    assert len(collab_edges) >= 1
    assert any(e.relation == "collaborates_with" for e in collab_edges)


@pytest.mark.asyncio
async def test_runtime_process_engines_dedup(db_session):
    """#14 流程去重：同一 process_id 在多文档中重复抽取时只生成一个流程引擎。

    此前因未按 process_id 去重，相同流程在不同文档/片段中重复抽取导致流程引擎
    数量虚高（如 190）。去重后应仅保留首个定义。
    """
    enterprise_id = "ent_rt_5"
    await _setup_graph_with_departments(db_session, enterprise_id)

    # 两个 process_id 相同、一个不同的流程定义
    process_output = ProcessCompileOutput(
        enterprise_id=enterprise_id,
        processes=[
            ProcessDefinition(
                process_id="proc_dup", name="销售流程A", process_type="sop",
                steps=[{"step_id": "s1", "name": "接洽", "order": 1, "approval_required": False}],
                owner_role_id="role_1", participants=["role_1"],
            ),
            ProcessDefinition(
                process_id="proc_dup", name="销售流程B", process_type="sop",
                steps=[{"step_id": "s1", "name": "接洽", "order": 1, "approval_required": False}],
                owner_role_id="role_1", participants=["role_1"],
            ),
            ProcessDefinition(
                process_id="proc_uniq", name="采购流程", process_type="approval",
                steps=[{"step_id": "s1", "name": "申请", "order": 1, "approval_required": True}],
                owner_role_id="role_1", participants=["role_1"],
            ),
        ],
        confidence=0.7,
    )

    compiler = RuntimeCompiler()
    ctx = CompilationContext(
        enterprise_id=enterprise_id,
        db=db_session,
        upstream={
            "knowledge": _make_knowledge_output(enterprise_id),
            "process": process_output,
            "capability": _make_capability_output(enterprise_id),
        },
    )
    result = await compiler.compile(ctx)

    engines = result.output.runtime.process_engines
    # 3 个定义中 2 个 process_id 相同 → 去重后应为 2 个引擎
    assert len(engines) == 2
    engine_ids = [e.process_id for e in engines]
    assert "proc_dup" in engine_ids
    assert "proc_uniq" in engine_ids
    assert engine_ids.count("proc_dup") == 1
