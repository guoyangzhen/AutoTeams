"""稀疏检索原语：分词、BM25、RRF 融合与命中契约（认知记忆三路检索之一）。

Dense 与 Graph 两路在 ``core.py`` / ``graph.py``；本模块只负责「字面」这一路，
以及三路排名融合所需的纯函数——全部无 IO，可独立单测。
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from app.models.cognitive_memory import EpisodicTrace

# ASCII 按词切分；中文另走 bigram。错误码（SLA-503）与中文摘要都能命中。
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]*")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")


@dataclass
class MemoryHit:
    """一条混合检索命中。channels 记录它被哪几路召回，用于归因展示。"""

    trace: EpisodicTrace
    score: float
    channels: List[str] = field(default_factory=list)
    causal_distance: Optional[int] = None


def tokenize(text: str) -> List[str]:
    """混合分词：ASCII 词 + 中文 bigram。

    中文不分词会让「退款审批」无法被「退款」命中；bigram 在召回率与索引体积
    之间取平衡，且不需要引入分词依赖。
    """
    if not text:
        return []
    lowered = text.lower()
    tokens: List[str] = list(_ASCII_TOKEN_RE.findall(lowered))
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def build_search_text(trace: EpisodicTrace) -> str:
    """情景轨迹的可检索文本：摘要 + 因果步骤 + 反思 + 标签。

    steps_detail 故意不参与索引——它体量大且噪声高，索引它会稀释 BM25 判别力。
    """
    chain = " ".join(
        str(step.get("action", ""))
        for step in (trace.causal_chain_json or [])
        if isinstance(step, dict)
    )
    tags = " ".join(str(t) for t in (trace.context_tags or []))
    parts = [trace.task_summary or "", chain, trace.reflection_notes or "", tags]
    return " ".join(p for p in parts if p)


class BM25Index:
    """Okapi BM25 稀疏索引（k1=1.5, b=0.75）。

    自行实现而非引入 rank_bm25：召回语料是运行时从 DB 加载的少量情景轨迹，
    几十行即可，不值得为几十条文档付出一个常驻依赖的代价。
    """

    K1 = 1.5
    B = 0.75

    def __init__(self, documents: Dict[str, str]) -> None:
        self._doc_tokens: Dict[str, List[str]] = {
            doc_id: tokenize(text) for doc_id, text in documents.items()
        }
        self._doc_freq: Dict[str, int] = defaultdict(int)
        for tokens in self._doc_tokens.values():
            for term in set(tokens):
                self._doc_freq[term] += 1
        self._avg_len = (
            sum(len(t) for t in self._doc_tokens.values()) / len(self._doc_tokens)
            if self._doc_tokens
            else 0.0
        )

    def search(self, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
        """返回按 BM25 分数降序的 (doc_id, score)；非正分文档被丢弃。"""
        if not self._doc_tokens:
            return []
        query_terms = tokenize(query)
        if not query_terms:
            return []
        total_docs = len(self._doc_tokens)
        scored: List[Tuple[str, float]] = []
        for doc_id, tokens in self._doc_tokens.items():
            if not tokens:
                continue
            counts: Dict[str, int] = defaultdict(int)
            for term in tokens:
                counts[term] += 1
            doc_len = len(tokens)
            score = 0.0
            for term in query_terms:
                tf = counts.get(term, 0)
                if tf == 0:
                    continue
                df = self._doc_freq.get(term, 0)
                # +0.5 平滑避免除零；df 越大（词越常见）贡献越低
                idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
                denom = tf + self.K1 * (1 - self.B + self.B * doc_len / (self._avg_len or 1.0))
                score += idf * (tf * (self.K1 + 1)) / denom
            if score > 0:
                scored.append((doc_id, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]], k: int = 60
) -> Dict[str, float]:
    """Reciprocal Rank Fusion：score(d) = Σ 1 / (k + rank_i(d))。

    三路的分数量纲互不相容（余弦距离 vs BM25 vs 跳数衰减），加权求和需要每次
    重新调参；RRF 只吃名次、不需归一化，且天然让「多路都召回」的文档上浮。
    """
    fused: Dict[str, float] = defaultdict(float)
    for ranking in ranked_lists:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] += 1.0 / (k + rank)
    return dict(fused)
