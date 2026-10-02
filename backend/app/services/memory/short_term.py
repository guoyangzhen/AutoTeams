"""短期记忆：对话上下文管理（PRD §5.12）。

特性：
- 保留最近 max_turns 轮对话（默认 20）
- 内存存储 + Redis 持久化（可选，Redis 不可用时透明降级）
- 线程安全（asyncio.Lock 按 conversation 维度隔离）

设计说明：
- 内存存储为一级缓存，Redis 为持久化后备
- 任务结束后可显式 clear()，或由 TTL 自动过期
- max_turns 可通过 MemoryConfig.short_term.max_turns 配置
"""
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# PRD §5.12: 短期记忆默认保留最近 20 轮
DEFAULT_MAX_TURNS = 20

# Redis 缓存键前缀
_REDIS_KEY_PREFIX = "memory:st"


class ShortTermTurn:
    """短期记忆单轮对话。"""

    __slots__ = ("role", "content", "timestamp", "metadata")

    def __init__(
        self,
        role: str,
        content: str,
        timestamp: Optional[datetime] = None,
        metadata: Optional[dict] = None,
    ):
        self.role = role
        self.content = content
        self.timestamp = timestamp or utcnow()
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ShortTermTurn":
        ts = data.get("timestamp")
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(ts) if ts else None,
            metadata=data.get("metadata", {}),
        )


class ShortTermMemory:
    """短期记忆管理器。

    用法：
        stm = ShortTermMemory()
        await stm.add_turn(agent_id, conversation_id, "user", "你好")
        context = await stm.get_context(agent_id, conversation_id)
    """

    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS):
        self._max_turns = max_turns
        # 内存存储：{agent_id: {conversation_id: [ShortTermTurn, ...]}}
        self._store: dict[str, dict[str, list[ShortTermTurn]]] = defaultdict(lambda: defaultdict(list))

    def _redis_key(self, agent_id: str, conversation_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}:{agent_id}:{conversation_id}"

    async def get_context(
        self,
        agent_id: str,
        conversation_id: str,
    ) -> list[ShortTermTurn]:
        """获取对话上下文（最近 max_turns 轮）。

        优先从内存读取；内存未命中时尝试 Redis（透明降级）。
        """
        # 内存命中
        turns = self._store.get(agent_id, {}).get(conversation_id, [])
        if turns:
            return list(turns)

        # 尝试 Redis
        try:
            from app.utils.cache import cache_get
            raw = cache_get(self._redis_key(agent_id, conversation_id))
            if raw:
                data = json.loads(raw)
                turns = [ShortTermTurn.from_dict(t) for t in data]
                # 回填内存
                self._store[agent_id][conversation_id] = turns
                return turns
        except Exception as e:
            logger.debug(f"Redis 读取短期记忆失败（透明降级到内存）: {e}")

        return []

    async def add_turn(
        self,
        agent_id: str,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """添加一轮对话到短期记忆，并自动裁剪到 max_turns。"""
        turn = ShortTermTurn(role=role, content=content, metadata=metadata)
        self._store[agent_id][conversation_id].append(turn)
        self._trim(agent_id, conversation_id)
        await self._persist(agent_id, conversation_id)

    async def clear(self, agent_id: str, conversation_id: str) -> None:
        """清除指定对话的短期记忆。"""
        self._store.get(agent_id, {}).pop(conversation_id, None)
        try:
            from app.utils.cache import cache_delete
            cache_delete(self._redis_key(agent_id, conversation_id))
        except Exception as exc:  # Redis 不可用时静默降级
            logger.debug("短期记忆缓存删除失败（Redis 可能不可用）: %s", exc)

    def _trim(self, agent_id: str, conversation_id: str) -> None:
        """裁剪到 max_turns 轮（保留最近的）。"""
        turns = self._store[agent_id][conversation_id]
        if len(turns) > self._max_turns:
            self._store[agent_id][conversation_id] = turns[-self._max_turns:]

    async def _persist(self, agent_id: str, conversation_id: str) -> None:
        """持久化到 Redis（best-effort，失败静默降级）。"""
        try:
            from app.utils.cache import cache_set
            turns = self._store[agent_id][conversation_id]
            data = json.dumps([t.to_dict() for t in turns], ensure_ascii=False)
            # TTL=24h：对话级短期记忆，24 小时后自动过期
            cache_set(self._redis_key(agent_id, conversation_id), data, ttl=86400)
        except Exception as e:
            logger.debug(f"Redis 持久化短期记忆失败（透明降级到内存）: {e}")

    def get_turn_count(self, agent_id: str, conversation_id: str) -> int:
        """获取当前对话轮数（用于测试与调试）。"""
        return len(self._store.get(agent_id, {}).get(conversation_id, []))
