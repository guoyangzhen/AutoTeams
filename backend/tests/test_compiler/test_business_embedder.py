"""嵌入业务测试（PRD §4.7）。"""
import pytest
from datetime import datetime

from app.services.embedding.business_embedder import BusinessEmbedder
from app.schemas.compiler import (
    RuntimeCompileResult,
    AgentConfigTemplate,
    ProcessEngineInstance,
    ProcessStep,
    ProcessTrigger,
)
from app.utils.time import utcnow


def _make_runtime(agents=None, process_engines=None):
    """构造测试用 Runtime。"""
    return RuntimeCompileResult(
        compiled_at=utcnow(),
        agents=agents or [],
        process_engines=process_engines or [],
    )


def test_embed_processes():
    """测试业务流程嵌入。"""
    agent = AgentConfigTemplate(
        agent_id="agent_role_1",
        agent_name="销售经理",
        role_id="role_1",
        system_prompt="你是销售经理。",
    )
    engine = ProcessEngineInstance(
        engine_id="engine_proc_1",
        process_id="proc_1",
        process_type="sop",
        steps=[ProcessStep(step_id="s1", name="步骤1", order=1)],
        triggers=[ProcessTrigger()],
        participants=["role_1"],
    )
    runtime = _make_runtime(agents=[agent], process_engines=[engine])

    embedder = BusinessEmbedder("ent_embed_1")
    result = embedder.embed(runtime)

    # Agent 系统 Prompt 应包含流程信息
    assert "engine_proc_1" in result.agents[0].system_prompt


def test_embed_data_schemas():
    """测试业务数据嵌入。"""
    agent = AgentConfigTemplate(
        agent_id="agent_1",
        agent_name="测试",
        role_id="r1",
        system_prompt="测试",
        knowledge_bases=["existing_kb"],
    )
    runtime = _make_runtime(agents=[agent])

    embedder = BusinessEmbedder("ent_embed_2")
    result = embedder.embed(
        runtime,
        data_schemas=[{"name": "customer_db"}, {"name": "order_db"}],
    )

    # Agent 知识库应包含数据 schema
    assert "customer_db" in result.agents[0].knowledge_bases
    assert "order_db" in result.agents[0].knowledge_bases
    assert "existing_kb" in result.agents[0].knowledge_bases


def test_embed_business_rules():
    """测试业务规则嵌入。"""
    agent = AgentConfigTemplate(
        agent_id="agent_1",
        agent_name="测试",
        role_id="r1",
        system_prompt="你是员工。",
    )
    runtime = _make_runtime(agents=[agent])

    embedder = BusinessEmbedder("ent_embed_3")
    result = embedder.embed(
        runtime,
        business_rules=[
            {"name": "金额限制", "description": "单笔交易不超过10万"},
            {"name": "审批要求", "description": "所有采购需经理审批"},
        ],
    )

    # Agent 系统 Prompt 应包含业务规则
    prompt = result.agents[0].system_prompt
    assert "金额限制" in prompt
    assert "审批要求" in prompt
    assert "业务规则" in prompt


def test_embed_empty():
    """测试无业务上下文的嵌入。"""
    agent = AgentConfigTemplate(
        agent_id="agent_1",
        agent_name="测试",
        role_id="r1",
        system_prompt="你是员工。",
    )
    runtime = _make_runtime(agents=[agent])

    embedder = BusinessEmbedder("ent_embed_4")
    result = embedder.embed(runtime)

    # 无业务上下文时，Agent 不变
    assert result.agents[0].system_prompt == "你是员工。"


def test_embed_no_agents():
    """测试无 Agent 的嵌入。"""
    runtime = _make_runtime(agents=[])
    embedder = BusinessEmbedder("ent_embed_5")
    result = embedder(runtime) if callable(embedder) else embedder.embed(runtime)
    assert len(result.agents) == 0
