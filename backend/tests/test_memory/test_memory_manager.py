"""记忆管理器测试（PRD §5.12 记忆检索流程）。

覆盖：
- retrieve：统一检索三层记忆（短期 + 实体 + 长期）
- update_short_term：更新短期记忆
- update_entity_memory：更新实体记忆
- consolidate_to_long_term：短期 → 长期整合（LLM mock）
- clear_short_term：清除短期记忆

LLM 调用全部 mock；VectorStoreService mock 避免依赖外部 ChromaDB。
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.memory.memory_manager import MemoryManager
from app.services.memory.short_term import ShortTermMemory
from app.services.memory.long_term import LongTermMemoryService
from app.services.memory.entity_memory import EntityMemoryService


class _FakeVectorStore:
    """模拟 VectorStoreService。"""

    async def add_documents(self, documents, metadatas, ids):
        pass

    async def search(self, query, n_results=5, where=None):
        return []


class TestMemoryManagerRetrieve:
    """统一记忆检索测试。"""

    async def test_retrieve_returns_three_layers(self, db_session):
        """retrieve 返回 short_term / entity / long_term 三层。"""
        mgr = MemoryManager()
        # 预填充短期记忆
        await mgr.update_short_term("agent-mgr-1", "conv-1", "user", "你好")
        await mgr.update_short_term("agent-mgr-1", "conv-1", "assistant", "您好")

        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ), patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ):
            result = await mgr.retrieve(
                db_session,
                agent_id="agent-mgr-1",
                enterprise_id="ent-mgr-1",
                conversation_id="conv-1",
            )

        assert "short_term" in result
        assert "entity" in result
        assert "long_term" in result
        assert len(result["short_term"]) == 2
        assert result["short_term"][0]["role"] == "user"
        assert isinstance(result["entity"], list)
        assert isinstance(result["long_term"], list)

    async def test_retrieve_empty_when_no_memory(self, db_session):
        """无任何记忆时返回空列表。"""
        mgr = MemoryManager()
        with patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ):
            result = await mgr.retrieve(
                db_session,
                agent_id="agent-empty",
                enterprise_id="ent-empty",
                conversation_id="conv-empty",
            )

        assert result["short_term"] == []
        assert result["entity"] == []
        assert result["long_term"] == []

    async def test_retrieve_with_current_task(self, db_session):
        """current_task 用于长期记忆语义检索。"""
        mgr = MemoryManager()
        await mgr.update_short_term("agent-mgr-2", "conv-2", "user", "短期消息")

        with patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ) as mock_create:
            result = await mgr.retrieve(
                db_session,
                agent_id="agent-mgr-2",
                enterprise_id="ent-mgr-2",
                conversation_id="conv-2",
                current_task="查询测温精度",
            )

        # VectorStoreService.create 应被调用（用于长期记忆检索）
        mock_create.assert_called()

    async def test_retrieve_includes_entity_memory(self, db_session):
        """retrieve 包含实体记忆。"""
        mgr = MemoryManager()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ), patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ):
            # 先写入实体记忆
            await mgr.update_entity_memory(
                db_session, "agent-mgr-3", "ent-mgr-3",
                entity_type="customer", entity_id="C-001",
                attributes={"name": "华智制造"},
            )
            # 检索
            result = await mgr.retrieve(
                db_session,
                agent_id="agent-mgr-3",
                enterprise_id="ent-mgr-3",
                conversation_id="conv-3",
            )

        assert len(result["entity"]) == 1
        assert result["entity"][0]["entity_type"] == "customer"
        assert result["entity"][0]["entity_id"] == "C-001"


class TestMemoryManagerUpdate:
    """记忆更新测试。"""

    async def test_update_short_term(self):
        """update_short_term 添加对话轮次。"""
        mgr = MemoryManager()
        await mgr.update_short_term("agent-up-1", "conv-1", "user", "测试")
        context = await mgr.short_term.get_context("agent-up-1", "conv-1")
        assert len(context) == 1
        assert context[0].content == "测试"

    async def test_update_entity_memory(self, db_session):
        """update_entity_memory 写入实体记忆。"""
        mgr = MemoryManager()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            await mgr.update_entity_memory(
                db_session, "agent-up-2", "ent-up-2",
                entity_type="product", entity_id="P-001",
                attributes={"name": "温度传感器"},
            )

        entity = await mgr.entity_memory.get_entity(
            db_session, "agent-up-2", "ent-up-2",
            "product", "P-001",
        )
        assert entity is not None
        assert entity.attributes["name"] == "温度传感器"

    async def test_clear_short_term(self):
        """clear_short_term 清除对话上下文。"""
        mgr = MemoryManager()
        await mgr.update_short_term("agent-up-3", "conv-1", "user", "消息1")
        await mgr.clear_short_term("agent-up-3", "conv-1")

        context = await mgr.short_term.get_context("agent-up-3", "conv-1")
        assert context == []


class TestMemoryManagerConsolidate:
    """短期 → 长期记忆整合测试。"""

    async def test_consolidate_to_long_term(self, db_session):
        """consolidate_to_long_term：短期记忆 → LLM 摘要 → 向量库 + DB。"""
        mgr = MemoryManager()
        # 预填充短期记忆
        await mgr.update_short_term("agent-con-1", "conv-1", "user", "SL-T100 测温范围？")
        await mgr.update_short_term("agent-con-1", "conv-1", "assistant", "-40℃~600℃")

        with patch(
            "app.services.memory.long_term.llm_service.chat",
            new=AsyncMock(return_value="客户咨询 SL-T100 测温范围，已告知 -40℃~600℃。"),
        ), patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ):
            record = await mgr.consolidate_to_long_term(
                db_session,
                agent_id="agent-con-1",
                enterprise_id="ent-con-1",
                conversation_id="conv-1",
            )

        assert record is not None
        assert "测温范围" in record.summary
        # 整合后短期记忆应被清除
        context = await mgr.short_term.get_context("agent-con-1", "conv-1")
        assert context == []

    async def test_consolidate_empty_short_term_returns_none(self, db_session):
        """无对话内容时返回 None。"""
        mgr = MemoryManager()
        with patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ):
            record = await mgr.consolidate_to_long_term(
                db_session,
                agent_id="agent-con-2",
                enterprise_id="ent-con-2",
                conversation_id="conv-empty",
            )

        assert record is None

    async def test_consolidate_with_external_turns(self, db_session):
        """consolidate 支持外部传入 turns。"""
        mgr = MemoryManager()
        turns = [
            {"role": "user", "content": "外部传入的对话"},
            {"role": "assistant", "content": "回复"},
        ]

        with patch(
            "app.services.memory.long_term.llm_service.chat",
            new=AsyncMock(return_value="外部传入对话的摘要"),
        ), patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ):
            record = await mgr.consolidate_to_long_term(
                db_session,
                agent_id="agent-con-3",
                enterprise_id="ent-con-3",
                conversation_id="conv-3",
                turns=turns,
            )

        assert record is not None
        assert record.summary == "外部传入对话的摘要"

    async def test_consolidate_llm_failure_fallback(self, db_session):
        """LLM 摘要生成失败时降级处理。"""
        mgr = MemoryManager()
        await mgr.update_short_term("agent-con-4", "conv-4", "user", "降级测试内容")

        with patch(
            "app.services.memory.long_term.llm_service.chat",
            new=AsyncMock(side_effect=RuntimeError("LLM 不可用")),
        ), patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(return_value=_FakeVectorStore()),
        ):
            record = await mgr.consolidate_to_long_term(
                db_session,
                agent_id="agent-con-4",
                enterprise_id="ent-con-4",
                conversation_id="conv-4",
            )

        # 降级：仍应生成记录（摘要为降级内容）
        assert record is not None
        assert "降级" in record.summary or "测试" in record.summary


class TestMemoryManagerInit:
    """MemoryManager 初始化测试。"""

    def test_default_max_turns(self):
        """默认 max_turns=20。"""
        mgr = MemoryManager()
        assert mgr.short_term._max_turns == 20

    def test_custom_max_turns(self):
        """自定义 max_turns。"""
        mgr = MemoryManager(max_turns=10)
        assert mgr.short_term._max_turns == 10

    def test_custom_services_injection(self):
        """支持注入自定义的子服务（用于测试）。"""
        stm = ShortTermMemory(max_turns=5)
        mgr = MemoryManager(short_term=stm)
        assert mgr.short_term is stm
        assert mgr.short_term._max_turns == 5
