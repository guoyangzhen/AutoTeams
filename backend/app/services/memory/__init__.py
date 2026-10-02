"""WT3 记忆架构服务包（PRD §5.12）。

三层记忆架构：
- 短期记忆（short_term）：对话上下文，max_turns=20，内存 + Redis 持久化
- 长期记忆（long_term）：交互摘要，向量库存储 + 语义检索
- 实体记忆（entity_memory）：客户/商机/产品关键属性，关联知识图谱

统一检索接口：memory_manager.retrieve()
"""
from app.services.memory.short_term import ShortTermMemory
from app.services.memory.long_term import LongTermMemoryService
from app.services.memory.entity_memory import EntityMemoryService
from app.services.memory.memory_manager import MemoryManager
from app.services.memory.cognitive import (
    CognitiveMemoryCore,
    BM25Index,
    MemoryHit,
    CausalChainNode,
    CausalChainReplay,
    tokenize,
    build_search_text,
    reciprocal_rank_fusion,
)


__all__ = [
    "ShortTermMemory",
    "LongTermMemoryService",
    "EntityMemoryService",
    "MemoryManager",
    "CognitiveMemoryCore",
    "BM25Index",
    "MemoryHit",
    "CausalChainNode",
    "CausalChainReplay",
    "tokenize",
    "build_search_text",
    "reciprocal_rank_fusion",
]
