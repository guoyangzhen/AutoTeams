"""D2 RAG 检索与重排序增强测试。

覆盖：
1. reranker fallback：bge-reranker 不可用时降级到 LLM 重排序（mock 模型加载失败）
2. agentic_rag 返回 meta 包含 query_type/iterations/refined_queries/self_rag_score
3. reranker 正常路径（mock CrossEncoder 返回固定分数，验证重排序顺序）
4. rag_engine simple/complex 通道均返回 self_rag_score
5. sentence-transformers 包未安装时优雅降级
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.rag.reranker_service import (
    RerankerService,
    reranker_service,
    _reset_bge_reranker_state,
    _bge_reranker_load_attempted,
)
from app.services.rag.agentic_rag import AgenticRAG
from app.services.rag.rag_engine import RAGEngine


class TestRerankerFallback:
    """D2-S11: bge-reranker 不可用时的降级逻辑。"""

    def setup_method(self):
        """每个测试前重置 bge-reranker 加载状态，避免测试间互相污染。"""
        _reset_bge_reranker_state()

    @pytest.mark.asyncio
    async def test_bge_unavailable_falls_back_to_llm(self, monkeypatch):
        """bge-reranker 模型加载失败时，应降级到 LLM 重排序。"""
        # 模拟 bge-reranker 不可用（模型加载返回 None）
        from app.services.rag import reranker_service as rs_module
        monkeypatch.setattr(rs_module, "_get_bge_reranker", lambda: None)
        monkeypatch.setattr(rs_module.settings, "RERANK_ENABLED", True)

        reranker = RerankerService()

        docs = [
            {"content": "some text", "metadata": {}},
            {"content": "query answer here", "metadata": {}},
        ]
        # mock LLM 重排序返回原始顺序
        with patch.object(
            reranker, "_llm_rerank", new=AsyncMock(return_value=docs)
        ) as mock_llm:
            result = await reranker.rerank("query", docs, top_k=2)

        assert len(result) == 2
        mock_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bge_failure_falls_back_to_llm(self, monkeypatch):
        """bge-reranker 抛异常时，应降级到 LLM 重排序。"""
        from app.services.rag import reranker_service as rs_module

        # 模拟一个会抛异常的 bge 模型
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("模型推理失败")
        monkeypatch.setattr(rs_module, "_get_bge_reranker", lambda: mock_model)

        reranker = RerankerService()
        docs = [
            {"content": "doc1", "metadata": {}},
            {"content": "doc2", "metadata": {}},
        ]
        with patch.object(
            reranker, "_llm_rerank", new=AsyncMock(return_value=docs)
        ) as mock_llm:
            result = await reranker.rerank("query", docs)

        assert len(result) == 2
        mock_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_failures_fall_back_to_heuristic(self, monkeypatch):
        """bge 和 LLM 都失败时，应降级到启发式重排序。"""
        from app.services.rag import reranker_service as rs_module
        monkeypatch.setattr(rs_module, "_get_bge_reranker", lambda: None)

        reranker = RerankerService()
        docs = [
            {"content": "unrelated content", "metadata": {}},
            {"content": "query answer here", "metadata": {}},
        ]
        # LLM 也失败
        with patch.object(
            reranker, "_llm_rerank", new=AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        ):
            result = await reranker.rerank("query", docs, top_k=1)

        # 启发式应选中包含 "query" 的文档
        assert len(result) == 1
        assert result[0]["content"] == "query answer here"

    @pytest.mark.asyncio
    async def test_rerank_disabled_uses_heuristic(self, monkeypatch):
        """RERANK_ENABLED=false 时直接走启发式，不调用任何模型。"""
        from app.services.rag import reranker_service as rs_module
        monkeypatch.setattr(rs_module.settings, "RERANK_ENABLED", False)

        reranker = RerankerService()
        docs = [
            {"content": "unrelated", "metadata": {}},
            {"content": "answer to the query", "metadata": {}},
        ]
        result = await reranker.rerank("query", docs, top_k=1)
        assert len(result) == 1
        assert result[0]["content"] == "answer to the query"


class TestRerankerNormalPath:
    """D2-S11: bge-reranker 正常路径（mock CrossEncoder）。"""

    def setup_method(self):
        _reset_bge_reranker_state()

    @pytest.mark.asyncio
    async def test_bge_rerank_orders_by_score(self, monkeypatch):
        """bge-reranker 正常工作时，应按相关性分数降序排列。"""
        from app.services.rag import reranker_service as rs_module

        # mock CrossEncoder：doc2 分数高于 doc1
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9]  # doc1=0.1, doc2=0.9
        monkeypatch.setattr(rs_module, "_get_bge_reranker", lambda: mock_model)
        monkeypatch.setattr(rs_module.settings, "RERANK_ENABLED", True)

        reranker = RerankerService()
        docs = [
            {"content": "low relevance doc", "metadata": {}},
            {"content": "high relevance doc", "metadata": {}},
        ]

        # mock LLM 重排序（不应被调用）
        with patch.object(reranker, "_llm_rerank", new=AsyncMock()) as mock_llm:
            result = await reranker.rerank("query", docs)

        # doc2 分数更高，应排在前面
        assert result[0]["content"] == "high relevance doc"
        assert result[1]["content"] == "low relevance doc"
        mock_llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bge_rerank_respects_top_k(self, monkeypatch):
        """bge-reranker 正常工作时，top_k 应限制返回结果数。"""
        from app.services.rag import reranker_service as rs_module

        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5, 0.3]
        monkeypatch.setattr(rs_module, "_get_bge_reranker", lambda: mock_model)
        monkeypatch.setattr(rs_module.settings, "RERANK_ENABLED", True)

        reranker = RerankerService()
        docs = [
            {"content": f"doc {i}", "metadata": {}}
            for i in range(4)
        ]
        result = await reranker.rerank("query", docs, top_k=2)
        assert len(result) == 2
        # 分数最高的两个是 doc1(0.9) 和 doc2(0.5)
        assert result[0]["content"] == "doc 1"
        assert result[1]["content"] == "doc 2"

    @pytest.mark.asyncio
    async def test_bge_rerank_stable_sort(self, monkeypatch):
        """相同分数时应保留原始顺序（稳定排序）。"""
        from app.services.rag import reranker_service as rs_module

        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.5, 0.5]
        monkeypatch.setattr(rs_module, "_get_bge_reranker", lambda: mock_model)
        monkeypatch.setattr(rs_module.settings, "RERANK_ENABLED", True)

        reranker = RerankerService()
        docs = [
            {"content": "first", "metadata": {}},
            {"content": "second", "metadata": {}},
            {"content": "third", "metadata": {}},
        ]
        result = await reranker.rerank("query", docs)
        assert [d["content"] for d in result] == ["first", "second", "third"]


class TestRerankerPackageMissing:
    """D2-S11: sentence-transformers 包未安装时的优雅降级。"""

    def setup_method(self):
        _reset_bge_reranker_state()

    @pytest.mark.asyncio
    async def test_package_missing_uses_llm_fallback(self, monkeypatch):
        """sentence-transformers 未安装时，应走 LLM fallback。"""
        from app.services.rag import reranker_service as rs_module

        # 模拟包未安装
        monkeypatch.setattr(rs_module, "_SENTENCE_TRANSFORMERS_AVAILABLE", False)
        _reset_bge_reranker_state()
        monkeypatch.setattr(rs_module.settings, "RERANK_ENABLED", True)

        reranker = RerankerService()
        docs = [
            {"content": "doc1", "metadata": {}},
            {"content": "doc2", "metadata": {}},
        ]
        with patch.object(
            reranker, "_llm_rerank", new=AsyncMock(return_value=docs)
        ) as mock_llm:
            result = await reranker.rerank("query", docs)

        assert len(result) == 2
        mock_llm.assert_awaited_once()


class TestAgenticRAGMeta:
    """D2-S9: agentic_rag 返回 meta 含 query_type/iterations/refined_queries/self_rag_score。"""

    @pytest.mark.asyncio
    async def test_search_returns_all_meta_keys(self):
        """agentic_rag.search 返回值应包含所有必需的 meta 键。"""
        rag = AgenticRAG()

        async def mock_search_fn(q, n):
            return [
                {"id": f"chunk_{i}", "content": f"结果 {i}", "metadata": {"source": f"doc_{i}"}}
                for i in range(n)
            ]

        # mock reranker 避免依赖模型
        with patch(
            "app.services.rag.agentic_rag.reranker_service.rerank",
            new=AsyncMock(side_effect=lambda q, docs, top_k=None: docs),
        ):
            result = await rag.search("测试查询", mock_search_fn, n_results=3)

        # 验证所有必需的 meta 键都存在
        assert "documents" in result
        assert "query_type" in result
        assert "iterations" in result
        assert "refined_queries" in result
        assert "self_rag_score" in result

        # 验证类型
        assert result["query_type"] == "complex"
        assert isinstance(result["iterations"], int)
        assert isinstance(result["refined_queries"], list)
        assert isinstance(result["self_rag_score"], float)
        assert 0.0 <= result["self_rag_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_self_rag_score_zero_when_no_documents(self):
        """无检索结果时 self_rag_score 应为 0.0。"""
        rag = AgenticRAG()

        async def mock_search_fn(q, n):
            return []

        with patch(
            "app.services.rag.agentic_rag.reranker_service.rerank",
            new=AsyncMock(return_value=[]),
        ):
            result = await rag.search("测试查询", mock_search_fn, n_results=3)

        assert result["self_rag_score"] == 0.0
        assert result["documents"] == []

    @pytest.mark.asyncio
    async def test_self_rag_score_from_relevance(self):
        """Self-RAG 评估返回 relevance_score 时，应归一化为 0-1。"""
        rag = AgenticRAG()

        async def mock_search_fn(q, n):
            return [
                {"id": f"chunk_{i}", "content": f"结果 {i}", "metadata": {}}
                for i in range(n)
            ]

        # mock 评估返回 relevance_score=8（应归一化为 0.8）
        mock_eval_result = {
            "sufficient": True,
            "relevance_score": 8,
            "consistency_score": 7,
            "refined_query": "",
        }
        with patch.object(
            rag, "_evaluate", new=AsyncMock(return_value=mock_eval_result)
        ):
            with patch(
                "app.services.rag.agentic_rag.reranker_service.rerank",
                new=AsyncMock(side_effect=lambda q, docs, top_k=None: docs),
            ):
                result = await rag.search("测试查询", mock_search_fn, n_results=3)

        assert result["self_rag_score"] == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_self_rag_score_from_distance_when_eval_fails(self):
        """Self-RAG 评估失败时，应基于检索距离近似计算 self_rag_score。"""
        rag = AgenticRAG()

        async def mock_search_fn(q, n):
            return [
                {"id": f"chunk_{i}", "content": f"结果 {i}", "metadata": {}, "distance": 0.5}
                for i in range(n)
            ]

        # mock 评估失败（relevance_score=0）
        mock_eval_result = {
            "sufficient": False,
            "relevance_score": 0,
            "consistency_score": 0,
            "refined_query": "",
        }
        with patch.object(
            rag, "_evaluate", new=AsyncMock(return_value=mock_eval_result)
        ):
            with patch(
                "app.services.rag.agentic_rag.reranker_service.rerank",
                new=AsyncMock(side_effect=lambda q, docs, top_k=None: docs),
            ):
                result = await rag.search("测试查询", mock_search_fn, n_results=3)

        # distance=0.5 → 1/(1+0.5) ≈ 0.667
        assert result["self_rag_score"] == pytest.approx(1.0 / 1.5, abs=0.01)


class TestRAGEngineMeta:
    """D2-S9: rag_engine 返回 meta 含 self_rag_score。"""

    @pytest.mark.asyncio
    async def test_simple_mode_returns_self_rag_score(self):
        """simple 通道应返回 self_rag_score。"""
        engine = RAGEngine()
        docs = [
            {"content": "doc1", "metadata": {}, "distance": 0.3},
            {"content": "doc2", "metadata": {}, "distance": 0.5},
        ]
        mock_store = MagicMock()
        mock_store.search = AsyncMock(return_value=docs)

        with patch(
            "app.services.rag.rag_engine.reranker_service.rerank",
            new=AsyncMock(return_value=[docs[0]]),
        ):
            result = await engine.search("query", mock_store, n_results=1, force_mode="simple")

        assert "self_rag_score" in result
        assert isinstance(result["self_rag_score"], float)
        assert 0.0 <= result["self_rag_score"] <= 1.0
        assert result["query_type"] == "simple"
        assert result["iterations"] == 1

    @pytest.mark.asyncio
    async def test_complex_mode_passes_through_self_rag_score(self):
        """complex 通道应透传 agentic_rag 的 self_rag_score。"""
        engine = RAGEngine()
        mock_store = MagicMock()

        agentic_result = {
            "documents": [{"content": "c1", "metadata": {}}],
            "query_type": "complex",
            "iterations": 2,
            "refined_queries": ["q1"],
            "self_rag_score": 0.85,
        }
        with patch(
            "app.services.rag.rag_engine.agentic_rag.search",
            new=AsyncMock(return_value=agentic_result),
        ):
            result = await engine.search("query", mock_store, n_results=1, force_mode="complex")

        assert result["self_rag_score"] == 0.85
        assert result["query_type"] == "complex"
        assert result["iterations"] == 2
        assert result["refined_queries"] == ["q1"]

    @pytest.mark.asyncio
    async def test_simple_mode_empty_documents_zero_score(self):
        """simple 通道无文档时 self_rag_score 应为 0.0。"""
        engine = RAGEngine()
        mock_store = MagicMock()
        mock_store.search = AsyncMock(return_value=[])

        result = await engine.search("query", mock_store, n_results=3, force_mode="simple")
        assert result["self_rag_score"] == 0.0
        assert result["documents"] == []
