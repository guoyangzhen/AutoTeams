"""认知记忆中枢实现体（重构计划 §4.2.2：单文件 ≤ 400 行，按职责拆分）。

``core.py`` 的 ``CognitiveMemoryCore`` 是唯一门面，自身只做编排与企业隔离；四条
通道各自独立、可单独降级：

- ``retrieval.py``  Sparse BM25 + RRF 融合（纯函数，无 IO）
- ``dense.py``      ChromaDB 向量写入与语义召回（异常即降级）
- ``graph.py``      因果多跳回放与种子扩散（带深度/规模上限）
- ``procedural.py`` 高绩效轨迹 → 经验基因卡沉淀
"""
from app.services.memory.cognitive.core import CognitiveMemoryCore
from app.services.memory.cognitive.graph import (
    CausalChainNode,
    CausalChainReplay,
)
from app.services.memory.cognitive.retrieval import (
    BM25Index,
    MemoryHit,
    build_search_text,
    reciprocal_rank_fusion,
    tokenize,
)

__all__ = [
    "CognitiveMemoryCore",
    "BM25Index",
    "MemoryHit",
    "CausalChainNode",
    "CausalChainReplay",
    "tokenize",
    "build_search_text",
    "reciprocal_rank_fusion",
]
