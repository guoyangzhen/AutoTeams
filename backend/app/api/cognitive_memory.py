"""AutoTeams 5.0 战役 2：分层长程认知记忆中枢 API 路由（挂载于 /api/v1/memory）。

端点契约：
- POST /memory/traces                          追加情景因果链轨迹（Layer 1 剧集记忆）
- GET  /memory/traces                          时间序列回放列表
- GET  /memory/traces/{trace_id}/replay       因果链多跳回放
- POST /memory/query                           三层混合检索（Dense + BM25 + 图扩散）
- GET  /memory/genes                           规程经验基因卡清单（Layer 3）
- POST /memory/genes/consolidate               经验沉淀：由高绩效轨迹蒸馏规程卡
- GET  /memory/graph                           情景 + 规程因果图谱

企业隔离：全部读写走 current_user.enterprise_id，调用方无法指定他人企业。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cognitive_memory import EpisodicTrace, ProceduralGene
from app.models.user import User
from app.services.memory.cognitive import CognitiveMemoryCore
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["AutoTeams Cognitive Memory"])


class CausalStep(BaseModel):
    """因果链上的一步。"""
    step: int = Field(ge=1, description="步骤序号，从 1 开始")
    action: str = Field(min_length=1, description="该步执行的动作")
    actor: Optional[str] = Field(None, description="执行者（工号或角色）")
    note: Optional[str] = Field(None, description="补充说明")


class EpisodicEventTrace(BaseModel):
    """一次协作执行的情景快照。"""
    task_summary: str = Field(min_length=1, max_length=2000)
    caused_by_event_id: Optional[str] = Field(None, description="触发本次执行的上游事件 id")
    causal_chain: List[CausalStep] = Field(default_factory=list, max_length=200)
    reflection_notes: Optional[str] = Field(None, max_length=8000)
    outcome_score: float = Field(default=0.0, ge=0.0, le=1.0, description="绩效评分 0..1")
    context_tags: List[str] = Field(default_factory=list, max_length=32)
    steps_detail: Optional[Dict[str, Any]] = None


class RecordTraceRequest(BaseModel):
    badge: str = Field(min_length=1, max_length=64, description="执行该协作的数字员工工号")
    event_trace: EpisodicEventTrace


class HybridQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    context_tags: List[str] = Field(default_factory=list, max_length=32)
    top_k: int = Field(default=10, ge=1, le=50)


class ConsolidateRequest(BaseModel):
    min_outcome_score: float = Field(default=0.8, ge=0.0, le=1.0)
    min_samples: int = Field(default=2, ge=1, le=50)


def _serialize_trace(trace: EpisodicTrace) -> Dict[str, Any]:
    return {
        "id": trace.id,
        "enterprise_id": trace.enterprise_id,
        "badge": trace.badge,
        "caused_by_event_id": trace.caused_by_event_id,
        "task_summary": trace.task_summary,
        "causal_chain": trace.causal_chain_json or [],
        "reflection_notes": trace.reflection_notes,
        "outcome_score": trace.outcome_score,
        "context_tags": trace.context_tags or [],
        "steps_detail": trace.steps_detail,
        "created_at": trace.created_at.isoformat() if trace.created_at else None,
    }


def _serialize_gene(gene: ProceduralGene) -> Dict[str, Any]:
    return {
        "gene_id": gene.gene_id,
        "enterprise_id": gene.enterprise_id,
        "badge": gene.badge,
        "trigger_pattern": gene.trigger_pattern,
        "successful_sop_patch": gene.successful_sop_patch,
        "confidence_rating": gene.confidence_rating,
        "support_count": gene.support_count,
        "source_trace_ids": gene.source_trace_ids or [],
        "created_at": gene.created_at.isoformat() if gene.created_at else None,
        "updated_at": gene.updated_at.isoformat() if gene.updated_at else None,
    }


@router.post("/traces", response_model=None, status_code=status.HTTP_201_CREATED)
async def record_episodic_trace(
    payload: RecordTraceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """追加一条情景因果链轨迹（剧集记忆）。"""
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail="当前用户未归属企业，无法写入长程记忆")
    core = CognitiveMemoryCore()
    try:
        trace = await core.record_episodic_trace(
            db,
            enterprise_id=current_user.enterprise_id,
            badge=payload.badge,
            event_trace=payload.event_trace.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success_response(data=_serialize_trace(trace), message="情景轨迹已记录")


@router.get("/traces", response_model=None)
async def list_episodic_traces(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按时间倒序列出情景轨迹，供时间序列回放使用。"""
    core = CognitiveMemoryCore()
    traces = await core.list_traces(db, enterprise_id=current_user.enterprise_id or "", limit=limit)
    return success_response(data=[_serialize_trace(t) for t in traces])


@router.get("/traces/{trace_id}/replay", response_model=None)
async def replay_causal_chain(
    trace_id: str,
    max_depth: int = Query(default=6, ge=1, le=6),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """沿 caused_by 边多跳回放因果链。"""
    core = CognitiveMemoryCore()
    replay = await core.replay_causal_chain(
        db,
        enterprise_id=current_user.enterprise_id or "",
        trace_id=trace_id,
        max_depth=max_depth,
    )
    if replay is None:
        raise HTTPException(status_code=404, detail="情景轨迹不存在")
    return success_response(data={
        "root_trace_id": replay.root_trace_id,
        "nodes": [
            {
                "trace_id": node.trace.id,
                "badge": node.trace.badge,
                "task_summary": node.trace.task_summary,
                "outcome_score": node.trace.outcome_score,
                "depth": node.depth,
                "direction": node.direction,
                "created_at": node.trace.created_at.isoformat() if node.trace.created_at else None,
            }
            for node in replay.nodes
        ],
        "max_depth_reached": replay.max_depth_reached,
        "truncated": replay.truncated,
    })


@router.post("/query", response_model=None)
async def query_hybrid_memory(
    payload: HybridQueryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """三层混合检索：Dense Vector + Sparse BM25 + Multi-hop Graph，RRF 融合。"""
    core = CognitiveMemoryCore()
    hits = await core.query_hybrid_memory(
        db,
        enterprise_id=current_user.enterprise_id or "",
        query=payload.query,
        context_tags=payload.context_tags,
        top_k=payload.top_k,
    )
    return success_response(data={
        "query": payload.query,
        "hits": [
            {
                "trace_id": hit.trace.id,
                "badge": hit.trace.badge,
                "task_summary": hit.trace.task_summary,
                "outcome_score": hit.trace.outcome_score,
                "created_at": hit.trace.created_at.isoformat() if hit.trace.created_at else None,
                "score": round(hit.score, 6),
                "channels": hit.channels,
                "causal_distance": hit.causal_distance,
            }
            for hit in hits
        ],
        "channel_counts": {
            "dense": sum(1 for h in hits if "dense" in h.channels),
            "sparse": sum(1 for h in hits if "sparse" in h.channels),
            "graph": sum(1 for h in hits if "graph" in h.channels),
        },
        "fusion": "rrf",
    })


@router.get("/genes", response_model=None)
async def list_procedural_genes(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出规程经验基因卡。"""
    core = CognitiveMemoryCore()
    genes = await core.list_genes(db, enterprise_id=current_user.enterprise_id or "", limit=limit)
    return success_response(data=[_serialize_gene(g) for g in genes])


@router.post("/genes/consolidate", response_model=None)
async def consolidate_procedural_memory(
    payload: ConsolidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """经验沉淀：把高绩效情景轨迹蒸馏为规程经验基因卡。"""
    core = CognitiveMemoryCore()
    genes, scanned = await core.consolidate_procedural_memory(
        db,
        enterprise_id=current_user.enterprise_id or "",
        min_outcome_score=payload.min_outcome_score,
        min_samples=payload.min_samples,
    )
    return success_response(data={
        "genes": [_serialize_gene(g) for g in genes],
        "distilled": len(genes),
        "scanned_traces": scanned,
    })


@router.get("/graph", response_model=None)
async def get_memory_graph(
    limit: int = Query(default=120, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """情景 + 规程的因果图谱（caused_by / distilled_into 两类边）。"""
    core = CognitiveMemoryCore()
    graph = await core.build_memory_graph(
        db, enterprise_id=current_user.enterprise_id or "", limit=limit
    )
    return success_response(data=graph)
