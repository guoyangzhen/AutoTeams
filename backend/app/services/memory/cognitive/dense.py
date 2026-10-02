"""Dense 路：向量库写入与语义召回（认知记忆三路检索之一）。

向量库是**增强**而非依赖：Chroma 未部署、collection 建不出来、查询超时——任何
一种都必须降级为「BM25 + 因果图」两路，而不是让整条检索失败。故本模块所有
入口对异常一律吞掉并返回空结果，只留 warning。
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Tuple

from app.models.cognitive_memory import EpisodicTrace
from app.services.memory.cognitive.retrieval import build_search_text

logger = logging.getLogger(__name__)

# 向量 collection 的确定性命名空间。
# 种子字符串是**持久化契约**：它决定 collection_name，改动会让所有企业已索引的
# 向量全部失联（dense 路静默召回为空，不报错）。常量名虽为 trace，种子不可动。
_TRACE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_OID, "autoteams-procedural-gene")


async def default_vector_store(enterprise_id: str):
    """默认向量库：企业隔离的 collection（collection_name 正则要求 agent_<slug> 形态）。"""
    from app.services.vector_store import VectorStoreService, build_collection_name

    namespace_id = str(uuid.uuid5(_TRACE_NAMESPACE, f"memory-lt-{enterprise_id}"))
    return await VectorStoreService.create(
        build_collection_name(enterprise_id, namespace_id)
    )


async def index_trace_vector(
    vector_store_factory, enterprise_id: str, trace: EpisodicTrace
) -> None:
    """把轨迹摘要写入向量库（best-effort，不阻断记忆写入）。"""
    try:
        store = await vector_store_factory(enterprise_id)
        await store.add_documents(
            documents=[build_search_text(trace)],
            metadatas=[{"trace_id": trace.id, "badge": trace.badge or ""}],
            ids=[trace.id],
        )
    except Exception as exc:  # noqa: BLE001 — 向量库不可用不得阻断记忆写入
        logger.warning("情景轨迹向量化失败（降级为 BM25 + 因果图检索）: %s", exc)


async def dense_search(
    vector_store_factory, enterprise_id: str, query: str, top_k: int
) -> List[Tuple[str, float]]:
    """Dense Vector 召回：返回 (trace_id, 相似度)，相似度 = 1 - 距离。"""
    try:
        store = await vector_store_factory(enterprise_id)
        raw = await store.search(query, n_results=top_k)
    except Exception as exc:  # noqa: BLE001 — 降级而非失败
        logger.warning("向量召回失败（降级为 BM25 + 因果图检索）: %s", exc)
        return []
    results: List[Tuple[str, float]] = []
    for item in raw or []:
        trace_id = (item.get("metadata") or {}).get("trace_id") or item.get("id")
        distance = item.get("distance")
        if not trace_id or not isinstance(distance, (int, float)):
            continue
        results.append((str(trace_id), 1.0 - float(distance)))
    return results
