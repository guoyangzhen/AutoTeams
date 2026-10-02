"""Agentic RAG 引擎（ReAct 多轮检索 + Self-RAG 评估）。

重构说明（P1-1）：
之前只有单次向量检索，复杂问题检索不足。
现实现 Agentic RAG 通道：

1. ReAct 循环：Thought → Action(检索) → Observation → ...
2. 查询改写：如果第一轮检索不足，改写查询再检索
3. Self-RAG 评估：LLM 评估检索结果是否足够回答问题
4. 最多 3 轮迭代，避免无限循环

与查询分类器配合：
- simple 查询 → 传统 RAG（单次检索+重排序）
- complex 查询 → Agentic RAG（本模块）

D2-S9 检索元数据：
- 返回值中增加 self_rag_score（Self-RAG 评分，0-1 浮点）
- query_type 固定为 "complex"（本模块只处理复杂查询，由 rag_engine 路由）
- iterations / refined_queries 保持原有语义
- 这些字段由 rag_engine 透传给 agents.py 的 stream_chat SSE 流
"""
import json
import logging

import httpx

from app.services.llm_service import llm_service, ModelTier
from app.services.rag.reranker_service import reranker_service
from app.services.prompt_security import wrap_untrusted, safe_json_extract
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3  # 最大检索轮数

REACT_PROMPT = """你是一个智能检索评估专家。请基于用户问题和已有检索结果，评估检索质量并决定下一步行动。

用户问题：{query}

已有检索结果：
{context}

请从以下维度评估：
1. 相关性（relevance）：检索结果与用户问题是否相关？
2. 完整性（completeness）：已有信息是否足够完整回答问题？
3. 一致性（consistency）：检索结果之间是否存在矛盾？
4. 如果信息足够，请总结关键证据并给出答案；如果不够，请给出改写后的检索查询。

以JSON格式返回：
{{
    "sufficient": true/false,
    "relevance_score": 0-10,
    "consistency_score": 0-10,
    "refined_query": "改写后的查询（如果不足够）",
    "answer": "答案（如果足够）",
    "sources": ["source1", "source2"]
}}
"""


class AgenticRAG:
    """Agentic RAG 引擎：多轮检索 + Self-RAG 评估。"""

    async def search(
        self,
        query: str,
        vector_store_search_fn,  # async callable: (query, n) -> list[dict]
        n_results: int = 5,
    ) -> dict:
        """执行 Agentic RAG 检索。

        Args:
            query: 用户查询
            vector_store_search_fn: 异步向量检索函数 (query, n_results) -> list[dict]
            n_results: 每轮检索的结果数

        Returns:
            {
                "documents": [...],          # 最终检索结果
                "query_type": "complex",     # 查询类型（本模块固定为 complex）
                "iterations": int,           # 检索轮数
                "refined_queries": [...],    # 改写过的查询列表
                "self_rag_score": float,     # Self-RAG 评分（0-1）
            }
        """
        all_documents: list[dict] = []
        seen_contents: set[str] = set()
        refined_queries: list[str] = []
        current_query = query
        # D2-S9: 跟踪最后一轮 Self-RAG 评估的相关性分数（0-10），用于计算 self_rag_score
        last_relevance_score: float = 0.0
        iteration = 0

        for iteration in range(MAX_ITERATIONS):
            logger.info(f"Agentic RAG 第 {iteration + 1} 轮检索: {current_query[:50]}")

            # 1. 检索
            results = await vector_store_search_fn(current_query, n_results)
            new_docs = self._deduplicate(results, seen_contents)
            all_documents.extend(new_docs)

            # 2. Self-RAG 评估：检查信息是否足够
            evaluation = await self._evaluate(query, all_documents)
            last_relevance_score = float(evaluation.get("relevance_score", 0) or 0)

            if evaluation.get("sufficient", False):
                logger.info(f"Agentic RAG 在第 {iteration + 1} 轮评估为足够")
                break

            # 3. 改写查询
            refined = evaluation.get("refined_query", "").strip()
            if not refined or refined == current_query:
                logger.info("无法改写查询，结束检索")
                break

            refined_queries.append(refined)
            current_query = refined
        else:
            logger.info(f"Agentic RAG 达到最大轮数 {MAX_ITERATIONS}")

        # 最终重排序
        if all_documents:
            all_documents = await reranker_service.rerank(query, all_documents, top_k=n_results)

        # D2-S9: 计算 self_rag_score
        # Self-RAG 评估的 relevance_score 范围是 0-10，归一化到 0-1
        # 若未检索到任何文档，评分为 0
        self_rag_score = self._compute_self_rag_score(
            last_relevance_score, all_documents
        )

        return {
            "documents": all_documents,
            "query_type": "complex",
            "iterations": min(iteration + 1, MAX_ITERATIONS),
            "refined_queries": refined_queries,
            "self_rag_score": self_rag_score,
        }

    def _compute_self_rag_score(
        self, relevance_score: float, documents: list[dict]
    ) -> float:
        """计算 Self-RAG 评分（0-1 浮点）。

        D2 未实现完整 Self-RAG 时，用检索置信度近似：
        - 优先使用 Self-RAG 评估的 relevance_score（0-10 归一化到 0-1）
        - 若 relevance_score 为 0（评估失败/未评估），回退到基于检索距离的置信度
        - 若无文档，返回 0.0

        Args:
            relevance_score: Self-RAG 评估的相关性分数（0-10）
            documents: 检索结果列表

        Returns:
            0-1 之间的浮点数
        """
        if not documents:
            return 0.0

        # 优先使用 Self-RAG 评估分数
        if relevance_score > 0:
            return max(0.0, min(1.0, relevance_score / 10.0))

        # 回退：基于检索距离的置信度近似
        # ChromaDB 的 distance 越小越相关，用 1/(1+d) 转换为 0-1 分数
        distances = [
            d.get("distance")
            for d in documents
            if d.get("distance") is not None
        ]
        if distances:
            avg_distance = sum(distances) / len(distances)
            return max(0.0, min(1.0, 1.0 / (1.0 + avg_distance)))

        # 无距离信息时给中等置信度
        return 0.5

    def _deduplicate(self, documents: list[dict], seen: set[str]) -> list[dict]:
        """P1-RAG: 使用 chunk id 去重，避免内容前 100 字符相同导致误判。

        同一文档的不同 chunk 或相似模板内容可能前 100 字符相同，
        使用向量库返回的唯一 id 可准确识别已召回的 chunk。
        """
        unique = []
        for doc in documents:
            doc_id = doc.get("id")
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                unique.append(doc)
        return unique

    async def _evaluate(self, query: str, documents: list[dict]) -> dict:
        """P1-RAG Self-RAG 评估：检查相关性、一致性、完整性，并给出引用来源。"""
        if not documents:
            return {"sufficient": False, "relevance_score": 0, "consistency_score": 0, "refined_query": query}

        # 构建 context 文本
        context_parts = []
        for i, doc in enumerate(documents[:5]):  # 只取前5条避免过长
            content = doc.get("content", "")[:300]
            source = doc.get("metadata", {}).get("source", f"文档{i}")
            context_parts.append(f"[{source}] {content}")
        context = "\n\n".join(context_parts)

        # P0-08: 用 wrap_untrusted 包裹 query 和 context，防止 prompt injection
        wrapped_query = wrap_untrusted(query, "用户问题")
        wrapped_context = wrap_untrusted(context, "检索结果")

        prompt = REACT_PROMPT.format(query=wrapped_query, context=wrapped_context)

        try:
            # P1-3: Self-RAG 评估是轻量判断任务，使用廉价模型
            response = await llm_service.chat(
                [{"role": "user", "content": prompt}],
                tier=ModelTier.CHEAP,
            )
            # P0-08: 使用 safe_json_extract 替代脆弱的 find/rfind
            result = safe_json_extract(response)
            if result is not None:
                return {
                    "sufficient": result.get("sufficient", False),
                    "relevance_score": result.get("relevance_score", 0),
                    "consistency_score": result.get("consistency_score", 0),
                    "refined_query": result.get("refined_query", ""),
                    "answer": result.get("answer", ""),
                    "sources": result.get("sources", []),
                }
        except (httpx.HTTPError, ValueError, RuntimeError, json.JSONDecodeError) as e:
            logger.warning(f"Self-RAG 评估失败: {e}", exc_info=True)
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"Self-RAG 评估失败（未预期错误）: {e}", exc_info=True)

        # 默认继续检索
        return {"sufficient": False, "relevance_score": 0, "consistency_score": 0, "refined_query": ""}


# 全局单例
agentic_rag = AgenticRAG()
