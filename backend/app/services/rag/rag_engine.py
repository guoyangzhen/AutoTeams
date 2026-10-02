"""RAG 引擎统一入口（双通道路由）。

重构说明（P1-1）：
之前 agents.py._search_knowledge 只做简单的 ChromaDB 查询（n_results=3）。
现升级为双通道 RAG 引擎：

                    ┌── simple → 传统 RAG（单次检索 + 重排序）
用户查询 → 查询分类器 ┤
                    └── complex → Agentic RAG（多轮检索 + Self-RAG + 重排序）

D2-S9 检索元数据透传：
- query_type: 查询分类结果（"simple"/"complex"）
- iterations: 检索轮数（simple 始终为 1，complex 为实际迭代数）
- refined_queries: 改写过的查询列表（仅 complex 有）
- self_rag_score: Self-RAG 评分（0-1），由 agentic_rag 或启发式计算
这些字段由 agents.py 的 _search_knowledge 读取并透传到 stream_chat SSE 流。

使用方式：
    from app.services.rag import rag_engine
    result = await rag_engine.search(query, vector_store, n_results=5)
    # result = {"documents": [...], "query_type": "simple"/"complex",
    #           "iterations": 1, "refined_queries": [], "self_rag_score": 0.85}
"""
import logging
from typing import Optional

from app.services.rag.query_classifier import query_classifier, QueryType
from app.services.rag.reranker_service import reranker_service
from app.services.rag.agentic_rag import agentic_rag

logger = logging.getLogger(__name__)


class RAGEngine:
    """RAG 引擎：双通道路由 + 重排序。"""

    async def search(
        self,
        query: str,
        vector_store,  # VectorStoreService 实例
        n_results: int = 5,
        force_mode: Optional[QueryType] = None,
    ) -> dict:
        """执行 RAG 检索，自动路由到传统或 Agentic 通道。

        Args:
            query: 用户查询
            vector_store: VectorStoreService 实例（需要有 search 方法）
            n_results: 期望返回的结果数
            force_mode: 强制使用指定模式（"simple"/"complex"），None 表示自动分类

        Returns:
            {
                "documents": [...],      # 检索结果列表
                "query_type": "simple"/"complex",  # 实际使用的通道
                "iterations": int,       # 检索轮数（simple 始终为 1）
                "refined_queries": [],   # 改写过的查询（仅 complex 有）
                "self_rag_score": float, # Self-RAG 评分（0-1）
            }
        """
        # 1. 查询分类
        if force_mode:
            query_type = force_mode
        else:
            query_type = await query_classifier.classify(query)

        logger.info(f"RAG 引擎: query_type={query_type}, query={query[:50]}")

        # 2. 定义异步检索函数（供 Agentic RAG 回调）
        async def search_fn(q: str, n: int) -> list[dict]:
            return await vector_store.search(q, n_results=n)

        # 3. 根据查询类型路由
        if query_type == "simple":
            # 传统 RAG：单次检索 + 重排序
            documents = await search_fn(query, n_results * 2)  # 多检索一些供重排序筛选
            if documents:
                documents = await reranker_service.rerank(query, documents, top_k=n_results)
            return {
                "documents": documents,
                "query_type": "simple",
                "iterations": 1,
                "refined_queries": [],
                "self_rag_score": self._compute_simple_self_rag_score(documents),
            }
        else:
            # Agentic RAG：多轮检索 + Self-RAG + 重排序
            result = await agentic_rag.search(query, search_fn, n_results=n_results)
            return {
                "documents": result["documents"],
                "query_type": "complex",
                "iterations": result["iterations"],
                "refined_queries": result["refined_queries"],
                "self_rag_score": result.get("self_rag_score", 0.0),
            }

    def _compute_simple_self_rag_score(self, documents: list[dict]) -> float:
        """计算 simple 通道的 Self-RAG 评分（0-1）。

        simple 通道不做 LLM Self-RAG 评估，用检索置信度近似：
        - 基于检索距离的平均值转换为 0-1 分数
        - 无距离信息时给中等置信度
        - 无文档时返回 0.0
        """
        if not documents:
            return 0.0

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


# 全局单例
rag_engine = RAGEngine()
