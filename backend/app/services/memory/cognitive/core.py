"""认知记忆中枢：情景轨迹读写 + 三路混合检索编排。

分层记忆的读写入口，自身不含检索算法，只做编排与企业隔离：

- Layer 1 情景剧集（EpisodicTrace）：``record_episodic_trace`` 写入时间序列快照
  与因果父指针；``replay_causal_chain`` 回放因果链。
- Layer 3 规程经验（ProceduralGene）：``consolidate_procedural_memory`` 沉淀。
- 混合检索 ``query_hybrid_memory``：Dense Vector（``dense.py``）+ Sparse BM25
  （``retrieval.py``）+ Multi-hop Graph（``graph.py``），RRF 融合。

硬约束：记忆是企业私有数据，所有读写以 enterprise_id 硬隔离，检索不得跨企业。
向量库不可用时降级为「BM25 + 因果图」两路可用——长程记忆是增强能力，不能成为
单点故障。

拆分依据重构计划 §4.2.2「单个 .py ≤ 400 行」。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cognitive_memory import EpisodicTrace, ProceduralGene
from app.services.memory.cognitive import dense, graph, procedural
from app.services.memory.cognitive.graph import (
    CausalChainReplay,
    DEFAULT_MAX_DEPTH,
    MAX_GRAPH_NODES,
)
from app.services.memory.cognitive.retrieval import (
    BM25Index,
    MemoryHit,
    build_search_text,
    reciprocal_rank_fusion,
)

logger = logging.getLogger(__name__)


class CognitiveMemoryCore:
    """分层长程认知记忆中枢。"""

    def __init__(self, vector_store_factory=None) -> None:
        """
        Args:
            vector_store_factory: async 工厂 ``(enterprise_id) -> VectorStoreService``。
                默认走 ChromaDB 企业隔离 collection；测试可注入假实现，
                也可传 None 强制走「BM25 + 因果图」两路降级。
        """
        self._vector_store_factory = vector_store_factory or dense.default_vector_store

    # ----------------------------------------------------------------- Layer 1 写入
    async def record_episodic_trace(
        self,
        db: AsyncSession,
        enterprise_id: str,
        badge: str,
        event_trace: Dict[str, Any],
    ) -> EpisodicTrace:
        """记录情景因果链轨迹（Layer 1）。"""
        summary = str(event_trace.get("task_summary") or "").strip()
        if not summary:
            raise ValueError("task_summary 不能为空")
        try:
            outcome_score = float(event_trace.get("outcome_score", 0.0))
        except (TypeError, ValueError):
            raise ValueError("outcome_score 必须是 0..1 的数值") from None
        outcome_score = max(0.0, min(1.0, outcome_score))

        caused_by = event_trace.get("caused_by_event_id")
        if caused_by:
            # 因果父事件必须属于同一企业，否则会出现跨企业串链
            parent = await db.get(EpisodicTrace, str(caused_by))
            if parent is None or parent.enterprise_id != enterprise_id:
                logger.info("忽略无效 caused_by_event_id=%s（不存在或跨企业）", caused_by)
                caused_by = None

        trace = EpisodicTrace(
            enterprise_id=enterprise_id,
            badge=badge or None,
            caused_by_event_id=str(caused_by) if caused_by else None,
            task_summary=summary,
            causal_chain_json=list(event_trace.get("causal_chain") or []),
            reflection_notes=event_trace.get("reflection_notes") or None,
            outcome_score=outcome_score,
            context_tags=[str(t) for t in (event_trace.get("context_tags") or [])],
            steps_detail=event_trace.get("steps_detail"),
        )
        db.add(trace)
        await db.commit()
        await db.refresh(trace)
        await dense.index_trace_vector(self._vector_store_factory, enterprise_id, trace)
        return trace

    async def list_traces(
        self, db: AsyncSession, enterprise_id: str, limit: int = 50
    ) -> List[EpisodicTrace]:
        """按时间倒序列出情景轨迹（时间序列回放用）。"""
        stmt = (
            select(EpisodicTrace)
            .where(EpisodicTrace.enterprise_id == enterprise_id)
            .order_by(EpisodicTrace.created_at.desc(), EpisodicTrace.id.desc())
            .limit(max(1, min(limit, MAX_GRAPH_NODES)))
        )
        return list((await db.execute(stmt)).scalars().all())

    # ----------------------------------------------------------------- 混合检索
    async def query_hybrid_memory(
        self,
        db: AsyncSession,
        enterprise_id: str,
        query: str,
        context_tags: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> List[MemoryHit]:
        """三路混合检索：Dense Vector + Sparse BM25 + Multi-hop Graph，RRF 融合。

        三路职责互补：
        - dense 认语义（「客户投诉处理」召回写着「客诉工单闭环」的轨迹），
        - sparse 认字面与错误码（「SLA-503」必须字面命中，不能被语义近似抹平），
        - graph 认因果（与种子轨迹同因同果的邻居，纯 BM25 完全看不到）。
        """
        query = (query or "").strip()
        if not query:
            return []
        k = max(1, min(int(top_k), 50))
        candidate_limit = k * 5

        corpus = await self._load_search_corpus(db, enterprise_id, context_tags)
        if not corpus:
            return []

        dense_hits = await dense.dense_search(
            self._vector_store_factory, enterprise_id, query, candidate_limit
        )
        dense_ranked = [tid for tid, _ in dense_hits if tid in corpus]

        bm25 = BM25Index({tid: build_search_text(t) for tid, t in corpus.items()})
        sparse_ranked = [
            tid for tid, _ in bm25.search(query, top_k=candidate_limit) if tid in corpus
        ]

        seeds = list(dict.fromkeys(dense_ranked + sparse_ranked))
        graph_ranked, graph_distances = await graph.expand_graph(
            db, enterprise_id, seeds, candidate_limit
        )

        fused = reciprocal_rank_fusion([dense_ranked, sparse_ranked, graph_ranked])
        dense_set, sparse_set, graph_set = (
            set(dense_ranked),
            set(sparse_ranked),
            set(graph_ranked),
        )

        hits: List[MemoryHit] = []
        for trace_id, score in fused.items():
            trace = corpus.get(trace_id)
            if trace is None:
                continue
            channels = [
                name
                for name, bucket in (
                    ("dense", dense_set),
                    ("sparse", sparse_set),
                    ("graph", graph_set),
                )
                if trace_id in bucket
            ]
            hits.append(
                MemoryHit(
                    trace=trace,
                    score=score,
                    channels=channels,
                    causal_distance=graph_distances.get(trace_id),
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        logger.debug(
            "混合检索完成 query=%r dense=%d sparse=%d graph=%d",
            query,
            len(dense_ranked),
            len(sparse_ranked),
            len(graph_ranked),
        )
        return hits[:k]

    async def _load_search_corpus(
        self,
        db: AsyncSession,
        enterprise_id: str,
        context_tags: Optional[List[str]],
    ) -> Dict[str, EpisodicTrace]:
        """加载检索语料：企业内最近 MAX_GRAPH_NODES 条情景轨迹，可按上下文标签收窄。"""
        stmt = (
            select(EpisodicTrace)
            .where(EpisodicTrace.enterprise_id == enterprise_id)
            .order_by(EpisodicTrace.created_at.desc())
            .limit(MAX_GRAPH_NODES)
        )
        traces = list((await db.execute(stmt)).scalars().all())
        if context_tags:
            wanted = {t.strip().lower() for t in context_tags if t and t.strip()}
            if wanted:
                traces = [
                    t for t in traces
                    if wanted & {str(tag).lower() for tag in (t.context_tags or [])}
                ]
        return {t.id: t for t in traces}

    # ----------------------------------------------------------------- Layer 3 沉淀
    async def consolidate_procedural_memory(
        self,
        db: AsyncSession,
        enterprise_id: str,
        min_outcome_score: float = 0.8,
        min_samples: int = 2,
    ) -> Tuple[List[ProceduralGene], int]:
        """记忆沉淀：把高绩效情景轨迹蒸馏为规程经验基因卡。"""
        return await procedural.consolidate(
            db, enterprise_id, min_outcome_score=min_outcome_score, min_samples=min_samples
        )

    async def list_genes(
        self, db: AsyncSession, enterprise_id: str, limit: int = 50
    ) -> List[ProceduralGene]:
        """按置信度倒序列出经验基因卡。"""
        return await procedural.list_genes(db, enterprise_id, limit=limit)

    # ----------------------------------------------------------------- 图谱
    async def build_memory_graph(
        self, db: AsyncSession, enterprise_id: str, limit: int = 120
    ) -> Dict[str, Any]:
        """构建情景 + 规程的因果图谱（前端实体图谱探查用）。

        边有两类：caused_by（情景之间的因果）与 distilled_into（轨迹 → 经验卡）。
        """
        cap = max(1, min(int(limit), MAX_GRAPH_NODES))
        traces = await self.list_traces(db, enterprise_id, limit=cap)
        genes = await self.list_genes(db, enterprise_id, limit=cap)

        nodes: List[Dict[str, Any]] = [
            {
                "id": t.id,
                "label": t.task_summary,
                "kind": "trace",
                "badge": t.badge,
                "weight": t.outcome_score,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in traces
        ]
        nodes.extend(
            {
                "id": g.gene_id,
                "label": g.trigger_pattern,
                "kind": "gene",
                "badge": g.badge,
                "weight": g.confidence_rating,
                "created_at": (g.updated_at or g.created_at).isoformat()
                if (g.updated_at or g.created_at) else None,
            }
            for g in genes
        )

        known_ids = {n["id"] for n in nodes}
        edges: List[Dict[str, Any]] = [
            {
                "source": t.caused_by_event_id,
                "target": t.id,
                "kind": "caused_by",
                "weight": t.outcome_score,
            }
            for t in traces
            if t.caused_by_event_id and t.caused_by_event_id in known_ids
        ]
        edges.extend(
            {
                "source": source_id,
                "target": g.gene_id,
                "kind": "distilled_into",
                "weight": g.confidence_rating,
            }
            for g in genes
            for source_id in (g.source_trace_ids or [])
            if source_id in known_ids
        )
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "trace_count": len(traces),
                "gene_count": len(genes),
                "edge_count": len(edges),
            },
        }

    # ----------------------------------------------------------------- 因果回放
    async def replay_causal_chain(
        self,
        db: AsyncSession,
        enterprise_id: str,
        trace_id: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> Optional[CausalChainReplay]:
        """沿 caused_by 边双向多跳回放因果链。"""
        return await graph.replay_causal_chain(
            db, enterprise_id, trace_id, max_depth=max_depth
        )
