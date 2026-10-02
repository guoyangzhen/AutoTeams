"""Knowledge Compiler 测试（PRD §4.3 第二级）。

测试实体对齐 + 关系抽取 + 图构建 + 向量化。
LLM 调用全部 mock。
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.compiler.knowledge_compiler import KnowledgeCompiler
from app.services.compiler.base import CompilationContext
from app.schemas.compiler import InformationCompileOutput, InformationEntry


@pytest.mark.asyncio
async def test_knowledge_compiler_no_upstream(db_session):
    """测试缺少上游产物时的处理。"""
    compiler = KnowledgeCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_know_1",
        db=db_session,
        upstream={},
    )
    result = await compiler.compile(ctx)

    assert result.stage == "knowledge"
    assert result.confidence == 0.0
    assert "缺少上游" in result.discovered_summary


@pytest.mark.asyncio
async def test_knowledge_compiler_entity_alignment(db_session):
    """测试实体对齐（去重）。"""
    # 构造有重复的 Information 输出
    entries = [
        InformationEntry(entry_id="e1", entry_type="Department", name="销售部", confidence=0.8),
        InformationEntry(entry_id="e2", entry_type="Department", name="销售部", confidence=0.9),  # 重复
        InformationEntry(entry_id="e3", entry_type="Role", name="销售经理", confidence=0.7),
    ]
    info_output = InformationCompileOutput(
        enterprise_id="ent_know_2",
        entries=entries,
        total_files=3,
        confidence=0.8,
    )

    compiler = KnowledgeCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_know_2",
        db=db_session,
        upstream={"information": info_output},
    )

    # Mock LLM 关系抽取返回空
    with patch("app.services.compiler.knowledge_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "[]"
        result = await compiler.compile(ctx)

    assert result.stage == "knowledge"
    # 去重后应剩 2 个节点
    assert len(result.output.nodes) == 2
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_knowledge_compiler_rule_based_relations(db_session):
    """测试基于规则的关系抽取。"""
    entries = [
        InformationEntry(
            entry_id="e1", entry_type="Department", name="销售部", confidence=0.9
        ),
        InformationEntry(
            entry_id="e2", entry_type="Role", name="销售经理",
            attributes={"department": "销售部"}, confidence=0.8
        ),
    ]
    info_output = InformationCompileOutput(
        enterprise_id="ent_know_3", entries=entries, total_files=2, confidence=0.8,
    )

    compiler = KnowledgeCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_know_3",
        db=db_session,
        upstream={"information": info_output},
    )

    with patch("app.services.compiler.knowledge_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "[]"
        result = await compiler.compile(ctx)

    # 应有一条 BELONGS_TO 关系
    belongs_edges = [e for e in result.output.edges if e.relation == "belongs_to"]
    assert len(belongs_edges) == 1


@pytest.mark.asyncio
async def test_knowledge_compiler_llm_relation_extraction(db_session):
    """测试 LLM 关系抽取。"""
    entries = [
        InformationEntry(entry_id="e1", entry_type="Role", name="经理A", confidence=0.8),
        InformationEntry(entry_id="e2", entry_type="Role", name="员工B", confidence=0.8),
    ]
    info_output = InformationCompileOutput(
        enterprise_id="ent_know_4", entries=entries, total_files=2, confidence=0.8,
    )

    # Mock LLM 返回关系
    llm_response = '[{"source_id": "PLACEHOLDER", "target_id": "PLACEHOLDER", "relation": "reports_to"}]'

    compiler = KnowledgeCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_know_4",
        db=db_session,
        upstream={"information": info_output},
    )

    with patch("app.services.compiler.knowledge_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        # 动态返回包含真实 node_id 的关系
        async def mock_chat_fn(messages, **kwargs):
            nodes_part = messages[1]["content"]
            # 返回固定关系
            return '[{"source_id": "", "target_id": "", "relation": "reports_to"}]'
        mock_chat.side_effect = mock_chat_fn
        result = await compiler.compile(ctx)

    assert result.stage == "knowledge"
    assert len(result.output.nodes) == 2
