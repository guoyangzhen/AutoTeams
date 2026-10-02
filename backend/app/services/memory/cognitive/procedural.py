"""规程层：把高绩效情景轨迹蒸馏为经验基因卡（ProceduralGene）。

这一层是**写入侧**的沉淀：N 次高绩效执行按「工号 + 触发模式」合并为一张卡，
置信度按 ``mean_score * (1 - e^(-support/2))`` 收敛——样本越多越可信，增速递减，
避免单次偶然高分就把规程固化成铁律。

刻意不做 LLM 改写：沉淀结果必须可复现、可审计，且不能因模型波动把已验证有效的
规程改坏。语义改写由上层 SOPSynthesizer 承担。
"""
from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cognitive_memory import EpisodicTrace, ProceduralGene
from app.services.memory.cognitive.graph import MAX_GRAPH_NODES, sort_key

logger = logging.getLogger(__name__)

# 经验基因的确定性业务键命名空间
GENE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_OID, "autoteams-procedural-gene")


async def consolidate(
    db: AsyncSession,
    enterprise_id: str,
    min_outcome_score: float = 0.8,
    min_samples: int = 2,
) -> Tuple[List[ProceduralGene], int]:
    """记忆沉淀：把高绩效情景轨迹蒸馏为规程经验基因卡。

    Returns:
        (本轮写入或更新的基因卡列表, 参与扫描的高绩效轨迹数)
    """
    threshold = max(0.0, min(1.0, float(min_outcome_score)))
    required = max(1, int(min_samples))
    stmt = (
        select(EpisodicTrace)
        .where(
            EpisodicTrace.enterprise_id == enterprise_id,
            EpisodicTrace.outcome_score >= threshold,
        )
        .order_by(EpisodicTrace.created_at.desc())
        .limit(MAX_GRAPH_NODES)
    )
    traces = list((await db.execute(stmt)).scalars().all())
    if not traces:
        return [], 0

    groups: Dict[str, List[EpisodicTrace]] = defaultdict(list)
    for trace in traces:
        pattern = (trace.task_summary or "").strip()
        if pattern:
            groups[gene_key(trace.badge, pattern)].append(trace)

    genes: List[ProceduralGene] = []
    for key, group in groups.items():
        if len(group) < required:
            continue
        mean_score = sum(t.outcome_score for t in group) / len(group)
        confidence = round(mean_score * (1 - math.exp(-len(group) / 2)), 4)
        gene = ProceduralGene(
            gene_id=key,
            enterprise_id=enterprise_id,
            badge=group[0].badge or None,
            trigger_pattern=group[0].task_summary,
            successful_sop_patch=derive_sop_patch(group),
            confidence_rating=confidence,
            support_count=len(group),
            source_trace_ids=[t.id for t in group],
        )
        # merge 返回合并后的实例；已有行时其身份与传入对象不同，须以返回值入列
        gene = await db.merge(gene)
        genes.append(gene)

    if genes:
        await db.commit()
    logger.info(
        "经验沉淀完成 enterprise=%s 扫描=%d 蒸馏=%d", enterprise_id, len(traces), len(genes)
    )
    return genes, len(traces)


def gene_key(badge: Optional[str], pattern: str) -> str:
    """经验基因卡的确定性业务键 → uuid5，使重复沉淀幂等而非堆出重复卡。"""
    return str(uuid.uuid5(GENE_NAMESPACE, f"{badge or '*'}|{pattern}"))


def derive_sop_patch(group: Sequence[EpisodicTrace]) -> str:
    """从高绩效轨迹中蒸馏规程补丁：取最近一次成功链路并附绩效证据。"""
    latest = max(group, key=sort_key)
    steps = " → ".join(
        str(step.get("action", "")).strip()
        for step in (latest.causal_chain_json or [])
        if isinstance(step, dict) and str(step.get("action", "")).strip()
    )
    mean_score = sum(t.outcome_score for t in group) / len(group)
    return f"{steps or latest.task_summary}｜基于 {len(group)} 次高绩效执行（均值 {mean_score:.2f}）"


async def list_genes(
    db: AsyncSession, enterprise_id: str, limit: int = 50
) -> List[ProceduralGene]:
    """按置信度倒序列出经验基因卡。"""
    stmt = (
        select(ProceduralGene)
        .where(ProceduralGene.enterprise_id == enterprise_id)
        .order_by(ProceduralGene.confidence_rating.desc(), ProceduralGene.updated_at.desc())
        .limit(max(1, min(limit, MAX_GRAPH_NODES)))
    )
    return list((await db.execute(stmt)).scalars().all())
