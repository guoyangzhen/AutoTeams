"""因果图路：多跳回放与种子扩散（认知记忆三路检索之一）。

因果边 ``EpisodicTrace.caused_by_event_id`` 自引用形成有向图。本模块负责
「沿边走」这一路：dense / sparse 召回的种子在这里扩散出因果邻居——这些邻居与
查询字面毫无关系，纯 BM25 永远看不到，但往往正是答案所在的根因或后果。

两个规模上限防止病态数据导致无界递归。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cognitive_memory import EpisodicTrace

MAX_GRAPH_NODES = 500
DEFAULT_MAX_DEPTH = 6


def sort_key(trace: EpisodicTrace) -> float:
    """created_at 的可排序投影。

    created_at 带时区，与 naive 的 datetime.min 比较会抛 TypeError，
    故统一投影成 epoch 秒再排序。
    """
    return trace.created_at.timestamp() if trace.created_at else 0.0


@dataclass
class CausalChainNode:
    """因果回放输出的一个节点。"""

    trace: EpisodicTrace
    depth: int
    direction: str  # self / ancestor / descendant


@dataclass
class CausalChainReplay:
    """因果链回放结果。"""

    root_trace_id: str
    nodes: List[CausalChainNode]
    max_depth_reached: int
    truncated: bool


def _parent_ids(trace: EpisodicTrace) -> List[str]:
    return [trace.caused_by_event_id] if trace.caused_by_event_id else []


async def load_child_ids(
    db: AsyncSession, enterprise_id: str
) -> Dict[str, List[str]]:
    """一次查询载入「父 id → 子 id 列表」，避免逐节点查子造成 N+1。"""
    stmt = select(EpisodicTrace.id, EpisodicTrace.caused_by_event_id).where(
        EpisodicTrace.enterprise_id == enterprise_id,
        EpisodicTrace.caused_by_event_id.isnot(None),
    )
    mapping: Dict[str, List[str]] = defaultdict(list)
    for child_id, parent_id in (await db.execute(stmt)).all():
        mapping[parent_id].append(child_id)
    return dict(mapping)


async def replay_causal_chain(
    db: AsyncSession,
    enterprise_id: str,
    trace_id: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Optional[CausalChainReplay]:
    """沿 caused_by 边双向多跳回放因果链。

    向上游找根因（ancestor），向下游找被它引发的执行（descendant），根节点自身
    标记 self。环状数据由 visited 集合截断。

    truncated 仅在「确有未访问邻居但深度已用尽」时置位，否则自然遍历到边界会
    被误报为截断。
    """
    root = await db.get(EpisodicTrace, trace_id)
    if root is None or root.enterprise_id != enterprise_id:
        return None

    depth_limit = max(1, min(int(max_depth), DEFAULT_MAX_DEPTH))
    children = await load_child_ids(db, enterprise_id)
    nodes: List[CausalChainNode] = [
        CausalChainNode(trace=root, depth=0, direction="self")
    ]
    visited = {root.id}
    max_reached = 0
    truncated = False

    for direction, step_ids in (
        ("ancestor", _parent_ids),
        ("descendant", lambda t: children.get(t.id, [])),
    ):
        frontier: List[Tuple[EpisodicTrace, int]] = [(root, 0)]
        while frontier and not truncated:
            next_frontier: List[Tuple[EpisodicTrace, int]] = []
            for current, depth in frontier:
                unvisited = [rid for rid in step_ids(current) if rid not in visited]
                if not unvisited:
                    continue
                if depth >= depth_limit:
                    truncated = True
                    break
                for related_id in unvisited:
                    related = await db.get(EpisodicTrace, related_id)
                    if related is None or related.enterprise_id != enterprise_id:
                        continue
                    visited.add(related.id)
                    next_frontier.append((related, depth + 1))
                    max_reached = max(max_reached, depth + 1)
                    nodes.append(
                        CausalChainNode(trace=related, depth=depth + 1, direction=direction)
                    )
                    if len(nodes) >= MAX_GRAPH_NODES:
                        truncated = True
                        break
            frontier = next_frontier

    nodes.sort(key=lambda n: (n.depth, n.direction, sort_key(n.trace)))
    return CausalChainReplay(
        root_trace_id=root.id,
        nodes=nodes,
        max_depth_reached=max_reached,
        truncated=truncated,
    )


async def expand_graph(
    db: AsyncSession,
    enterprise_id: str,
    seeds: Sequence[str],
    limit: int,
) -> Tuple[List[str], Dict[str, int]]:
    """多跳因果扩散：返回 (按跳数排序的 trace_id, trace_id -> 跳数)。

    只有「由遍历真正走到」的节点才计入 graph 路。种子自身是 dense / sparse 召回
    的结果，把它记成 graph 命中会让召回归因失真。
    """
    if not seeds:
        return [], {}

    corpus = await _load_causal_subgraph(db, enterprise_id)
    by_id = set(corpus)
    parents: Dict[str, str] = {
        tid: t.caused_by_event_id for tid, t in corpus.items() if t.caused_by_event_id
    }
    children: Dict[str, List[str]] = defaultdict(list)
    for child_id, parent_id in parents.items():
        children[parent_id].append(child_id)

    seed_set = {s for s in seeds if s in by_id}
    distances: Dict[str, int] = {}
    expanded: set[str] = set()
    frontier = list(seed_set)
    depth = 0
    while frontier and len(distances) < limit and depth < DEFAULT_MAX_DEPTH:
        depth += 1
        next_frontier: List[str] = []
        for node_id in frontier:
            if node_id in expanded:
                continue
            expanded.add(node_id)
            neighbours = list(children.get(node_id, []))
            if node_id in parents:
                neighbours.append(parents[node_id])
            for neighbour in neighbours:
                if neighbour in by_id and neighbour not in distances:
                    distances[neighbour] = depth
                    next_frontier.append(neighbour)
        frontier = next_frontier

    # 种子已由 dense / sparse 召回。扩散再绕回种子不构成新的证据，否则每条
    # 因果边都会给两端无端加上 graph 归因。
    reached = {tid: hops for tid, hops in distances.items() if tid not in seed_set}
    ranked = sorted(reached, key=lambda tid: (reached[tid], tid))[:limit]
    return ranked, {tid: reached[tid] for tid in ranked}


async def _load_causal_subgraph(
    db: AsyncSession, enterprise_id: str
) -> Dict[str, EpisodicTrace]:
    """载入企业内最近 MAX_GRAPH_NODES 条情景轨迹作为扩散边界。"""
    stmt = (
        select(EpisodicTrace)
        .where(EpisodicTrace.enterprise_id == enterprise_id)
        .order_by(EpisodicTrace.created_at.desc())
        .limit(MAX_GRAPH_NODES)
    )
    return {t.id: t for t in (await db.execute(stmt)).scalars().all()}
