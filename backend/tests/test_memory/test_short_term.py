"""短期记忆测试（PRD §5.12）。

覆盖：
- max_turns=20 裁剪逻辑
- add_turn / get_context / clear
- Redis 不可用时透明降级到内存
- 多 agent / 多 conversation 隔离
"""
import pytest

from app.services.memory.short_term import ShortTermMemory, ShortTermTurn, DEFAULT_MAX_TURNS


class TestShortTermMemory:
    """短期记忆核心功能测试。"""

    async def test_add_and_get_context(self):
        """添加单轮对话并读取。"""
        stm = ShortTermMemory(max_turns=20)
        await stm.add_turn("agent-1", "conv-1", "user", "你好")
        await stm.add_turn("agent-1", "conv-1", "assistant", "您好，有什么可以帮您？")

        context = await stm.get_context("agent-1", "conv-1")
        assert len(context) == 2
        assert context[0].role == "user"
        assert context[0].content == "你好"
        assert context[1].role == "assistant"

    async def test_max_turns_default_is_20(self):
        """PRD §5.12: 短期记忆默认保留最近 20 轮。"""
        assert DEFAULT_MAX_TURNS == 20

        stm = ShortTermMemory()
        assert stm._max_turns == 20

    async def test_trim_to_max_turns(self):
        """超过 max_turns 时自动裁剪，保留最近的。"""
        stm = ShortTermMemory(max_turns=3)
        for i in range(5):
            await stm.add_turn("agent-1", "conv-1", "user", f"消息{i}")

        context = await stm.get_context("agent-1", "conv-1")
        assert len(context) == 3
        # 保留最近 3 轮：消息2, 消息3, 消息4
        assert context[0].content == "消息2"
        assert context[2].content == "消息4"

    async def test_max_turns_20_boundary(self):
        """正好 20 轮时不裁剪，第 21 轮裁剪掉最早的。"""
        stm = ShortTermMemory(max_turns=20)
        for i in range(21):
            await stm.add_turn("agent-1", "conv-1", "user", f"消息{i}")

        context = await stm.get_context("agent-1", "conv-1")
        assert len(context) == 20
        # 最早的"消息0"被裁剪
        assert context[0].content == "消息1"
        assert context[-1].content == "消息20"

    async def test_clear(self):
        """清除指定对话的短期记忆。"""
        stm = ShortTermMemory()
        await stm.add_turn("agent-1", "conv-1", "user", "你好")
        await stm.clear("agent-1", "conv-1")

        context = await stm.get_context("agent-1", "conv-1")
        assert len(context) == 0

    async def test_agent_isolation(self):
        """不同 Agent 的记忆相互隔离。"""
        stm = ShortTermMemory()
        await stm.add_turn("agent-1", "conv-1", "user", "Agent1消息")
        await stm.add_turn("agent-2", "conv-1", "user", "Agent2消息")

        ctx1 = await stm.get_context("agent-1", "conv-1")
        ctx2 = await stm.get_context("agent-2", "conv-1")
        assert len(ctx1) == 1
        assert ctx1[0].content == "Agent1消息"
        assert len(ctx2) == 1
        assert ctx2[0].content == "Agent2消息"

    async def test_conversation_isolation(self):
        """同一 Agent 不同对话的记忆相互隔离。"""
        stm = ShortTermMemory()
        await stm.add_turn("agent-1", "conv-1", "user", "对话1")
        await stm.add_turn("agent-1", "conv-2", "user", "对话2")

        ctx1 = await stm.get_context("agent-1", "conv-1")
        ctx2 = await stm.get_context("agent-1", "conv-2")
        assert ctx1[0].content == "对话1"
        assert ctx2[0].content == "对话2"

    async def test_get_context_empty(self):
        """不存在的对话返回空列表。"""
        stm = ShortTermMemory()
        context = await stm.get_context("nonexistent", "nonexistent")
        assert context == []

    async def test_get_turn_count(self):
        """get_turn_count 返回当前对话轮数。"""
        stm = ShortTermMemory()
        await stm.add_turn("agent-1", "conv-1", "user", "你好")
        await stm.add_turn("agent-1", "conv-1", "assistant", "您好")
        assert stm.get_turn_count("agent-1", "conv-1") == 2
        assert stm.get_turn_count("agent-1", "conv-2") == 0

    async def test_turn_with_metadata(self):
        """带 metadata 的对话轮次。"""
        stm = ShortTermMemory()
        await stm.add_turn(
            "agent-1", "conv-1", "user", "你好",
            metadata={"source": "web", "lang": "zh"},
        )
        context = await stm.get_context("agent-1", "conv-1")
        assert context[0].metadata["source"] == "web"
        assert context[0].metadata["lang"] == "zh"


class TestShortTermTurn:
    """ShortTermTurn 序列化测试。"""

    def test_to_dict_and_from_dict_roundtrip(self):
        """to_dict / from_dict 往返序列化。"""
        turn = ShortTermTurn(role="user", content="测试", metadata={"k": "v"})
        d = turn.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "测试"
        assert d["metadata"] == {"k": "v"}

        restored = ShortTermTurn.from_dict(d)
        assert restored.role == "user"
        assert restored.content == "测试"
        assert restored.metadata == {"k": "v"}

    def test_from_dict_without_metadata(self):
        """from_dict 兼容无 metadata 的字典。"""
        turn = ShortTermTurn.from_dict({"role": "system", "content": "系统消息"})
        assert turn.metadata == {}
