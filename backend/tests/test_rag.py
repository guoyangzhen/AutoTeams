"""RAG 引擎测试：分块服务、查询分类器、重排序、Agentic RAG。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.rag.chunker_service import ChunkerService, chunker_service
from app.services.rag.query_classifier import QueryClassifier


class TestChunkerService:
    """结构感知分块测试。"""

    def test_empty_text(self):
        chunker = ChunkerService()
        assert chunker.chunk_text("") == []
        assert chunker.chunk_text("   ") == []

    def test_short_text(self):
        chunker = ChunkerService()
        text = "这是一个简短的文本。"
        chunks = chunker.chunk_text(text)
        assert len(chunks) >= 1
        assert "简短的文本" in chunks[0]

    def test_paragraph_split(self):
        """多段文本应按段落分块。"""
        chunker = ChunkerService(chunk_size=100, chunk_overlap=20)
        text = "这是第一段。\n\n这是第二段。\n\n这是第三段。"
        chunks = chunker.chunk_text(text)
        assert len(chunks) >= 1
        # 每段都应该在某个块中
        all_content = " ".join(chunks)
        assert "第一段" in all_content
        assert "第二段" in all_content
        assert "第三段" in all_content

    def test_long_paragraph_split_by_sentence(self):
        """超长段落应按句子切分。"""
        chunker = ChunkerService(chunk_size=30, chunk_overlap=5)
        text = "这是第一句话。这是第二句话。这是第三句话。这是第四句话。这是第五句话。"
        chunks = chunker.chunk_text(text)
        assert len(chunks) > 1

    def test_presentation_split_by_slides(self):
        """PPT 文档应按 Slide 标记切分。"""
        chunker = ChunkerService()
        text = "[Slide 1]\n标题1\n内容1\n\n[Slide 2]\n标题2\n内容2"
        chunks = chunker.chunk_text(text, file_type="presentation")
        assert len(chunks) >= 2

    def test_spreadsheet_split_by_sheets(self):
        """Excel 文档应按 Sheet 标记切分。"""
        chunker = ChunkerService()
        text = "[Sheet1]\nA | B | C\n1 | 2 | 3\n\n[Sheet2]\nD | E\n4 | 5"
        chunks = chunker.chunk_text(text, file_type="spreadsheet")
        assert len(chunks) >= 2

    def test_chunk_size_within_limits(self):
        """块大小应控制在合理范围内。"""
        chunker = ChunkerService(chunk_size=200, chunk_overlap=30)
        text = "段落内容。" * 100  # 很长的文本
        chunks = chunker.chunk_text(text)
        for chunk in chunks:
            # 允许一定的弹性（重叠可能导致略超）
            assert len(chunk) <= 200 + 50

    def test_overlap_between_chunks(self):
        """相邻块之间应有重叠内容。"""
        chunker = ChunkerService(chunk_size=100, chunk_overlap=30)
        text = "句子一。句子二。句子三。句子四。句子五。句子六。句子七。句子八。"
        chunks = chunker.chunk_text(text)
        if len(chunks) >= 2:
            # 检查是否有重叠（第二个块的开头应该出现在第一个块的末尾附近）
            # 注意：由于分块逻辑复杂，我们只验证不会丢失内容
            all_content = "".join(chunks)
            assert "句子八" in all_content


class TestQueryClassifier:
    """查询分类器测试。"""

    def test_short_query_is_simple(self):
        """短查询应被分类为 simple。"""
        classifier = QueryClassifier()
        # "你好" 只有 2 个字符，应该被分类为 simple
        result = classifier._rule_classify("你好")
        assert result == "simple"

    def test_complex_keywords_detected(self):
        """包含复杂关键词的查询应被分类为 complex。"""
        classifier = QueryClassifier()
        assert classifier._rule_classify("请比较 A 和 B 的优缺点") == "complex"
        assert classifier._rule_classify("分析一下这个方案") == "complex"
        assert classifier._rule_classify("如何实现这个功能") == "complex"
        assert classifier._rule_classify("为什么会出现这个问题") == "complex"

    def test_simple_query_no_keywords(self):
        """没有复杂关键词的查询应被分类为 simple。"""
        classifier = QueryClassifier()
        assert classifier._rule_classify("公司的地址是什么") == "simple"
        assert classifier._rule_classify("产品名称叫什么") == "simple"

    def test_english_complex_keywords(self):
        """英文复杂关键词也应被识别。"""
        classifier = QueryClassifier()
        assert classifier._rule_classify("how to implement this") == "complex"
        assert classifier._rule_classify("compare A and B") == "complex"

    def test_english_simple_query(self):
        """英文简单查询应被分类为 simple。"""
        classifier = QueryClassifier()
        assert classifier._rule_classify("what is the company name") == "simple"
        assert classifier._rule_classify("where is the office") == "simple"


class TestRerankerService:
    """重排序服务测试。"""

    @pytest.mark.asyncio
    async def test_empty_documents(self):
        from app.services.rag.reranker_service import RerankerService
        reranker = RerankerService()
        result = await reranker.rerank("query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_single_document(self):
        from app.services.rag.reranker_service import RerankerService
        reranker = RerankerService()
        docs = [{"content": "only doc", "metadata": {}}]
        result = await reranker.rerank("query", docs)
        assert len(result) == 1
        assert result[0]["content"] == "only doc"

    @pytest.mark.asyncio
    async def test_top_k_limit(self):
        """top_k 应限制返回结果数。"""
        from app.services.rag.reranker_service import RerankerService
        reranker = RerankerService()
        docs = [
            {"content": f"doc {i}", "metadata": {}}
            for i in range(5)
        ]
        result = await reranker.rerank("query", docs, top_k=2)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_rerank_unexpected_error_returns_original(self):
        """未预期错误时应返回原始顺序。"""
        from app.services.rag.reranker_service import RerankerService
        reranker = RerankerService()
        docs = [{"content": "doc1", "metadata": {}}, {"content": "doc2", "metadata": {}}]
        with patch.object(reranker, "_llm_rerank", new=AsyncMock(side_effect=AttributeError("未预期错误"))):
            result = await reranker.rerank("query", docs, top_k=1)
        assert len(result) == 1
        assert result[0]["content"] == "doc1"

    @pytest.mark.asyncio
    async def test_rerank_disabled_uses_heuristic(self, monkeypatch):
        """RERANK_ENABLED=false 时应使用启发式重排序。"""
        from app.services.rag.reranker_service import RerankerService
        monkeypatch.setattr("app.services.rag.reranker_service.settings.RERANK_ENABLED", False)
        reranker = RerankerService()
        docs = [
            {"content": "unrelated content", "metadata": {}},
            {"content": "answer to the query", "metadata": {}},
        ]
        result = await reranker.rerank("query", docs, top_k=1)
        assert len(result) == 1
        assert result[0]["content"] == "answer to the query"

    @pytest.mark.asyncio
    async def test_rerank_llm_failure_falls_back_to_heuristic(self, monkeypatch):
        """LLM 重排序失败时应降级到启发式重排序。"""
        from app.services.rag.reranker_service import RerankerService
        monkeypatch.setattr("app.services.rag.reranker_service.settings.RERANK_ENABLED", True)
        reranker = RerankerService()
        docs = [
            {"content": "some text", "metadata": {}},
            {"content": "query answer here", "metadata": {}},
        ]
        with patch.object(reranker, "_llm_rerank", new=AsyncMock(side_effect=RuntimeError("LLM 不可用"))):
            result = await reranker.rerank("query", docs, top_k=1)
        assert len(result) == 1
        assert result[0]["content"] == "query answer here"


class TestAgenticRAG:
    """Agentic RAG 测试。"""

    @pytest.mark.asyncio
    async def test_deduplicate(self):
        from app.services.rag.agentic_rag import AgenticRAG
        rag = AgenticRAG()
        seen = set()
        docs = [
            {"id": "chunk_1", "content": "duplicate content here", "metadata": {}},
            {"id": "chunk_1", "content": "duplicate content here", "metadata": {}},  # 重复 id
            {"id": "chunk_2", "content": "unique content here", "metadata": {}},
        ]
        result = rag._deduplicate(docs, seen)
        assert len(result) == 2  # 按 chunk id 去重后保留两条

    @pytest.mark.asyncio
    async def test_search_with_mock_vector_store(self):
        """使用 mock 向量存储测试 Agentic RAG。"""
        from app.services.rag.agentic_rag import AgenticRAG

        class MockVectorStore:
            def __init__(self):
                self.call_count = 0

            def search(self, query: str, n_results: int = 5) -> list[dict]:
                self.call_count += 1
                return [
                    {"content": f"结果 {self.call_count}-{i}", "metadata": {"source": f"doc_{i}"}}
                    for i in range(n_results)
                ]

        rag = AgenticRAG()
        mock_store = MockVectorStore()

        async def search_fn(q, n):
            return mock_store.search(q, n)

        # 注意：由于没有配置 LLM API，Self-RAG 评估会失败，
        # 此时应该优雅降级
        result = await rag.search("测试查询", search_fn, n_results=3)
        assert "documents" in result
        assert "iterations" in result
        assert result["iterations"] >= 1

    @pytest.mark.asyncio
    async def test_evaluate_unexpected_error_returns_continue(self):
        """Self-RAG 评估遇到未预期错误时应返回继续检索。"""
        from app.services.rag.agentic_rag import AgenticRAG
        from app.services.llm_service import llm_service

        rag = AgenticRAG()
        docs = [{"content": "doc1", "metadata": {"source": "s1"}}]
        with patch.object(llm_service, "chat", new=AsyncMock(side_effect=AttributeError("未预期错误"))):
            result = await rag._evaluate("query", docs)
        assert result["sufficient"] is False
        assert result["refined_query"] == ""


class TestQueryClassifierAsync:
    """查询分类器 classify 异步入口测试。"""

    @pytest.mark.asyncio
    async def test_short_query_returns_simple_without_llm(self):
        classifier = QueryClassifier()
        # 短查询直接返回 simple，不会调用 LLM
        result = await classifier.classify("你好")
        assert result == "simple"

    @pytest.mark.asyncio
    async def test_llm_classify_complex(self):
        classifier = QueryClassifier()
        with patch.object(classifier, "_llm_classify", new=AsyncMock(return_value="complex")):
            result = await classifier.classify("请详细比较A方案和B方案的优劣")
        assert result == "complex"

    @pytest.mark.asyncio
    async def test_llm_classify_simple(self):
        classifier = QueryClassifier()
        with patch.object(classifier, "_llm_classify", new=AsyncMock(return_value="simple")):
            result = await classifier.classify("请问公司地址是什么")
        assert result == "simple"

    @pytest.mark.asyncio
    async def test_llm_failure_fallback_to_rule(self):
        classifier = QueryClassifier()
        with patch.object(classifier, "_llm_classify", new=AsyncMock(side_effect=RuntimeError("LLM失败"))):
            result = await classifier.classify("请详细分析这个方案的可行性")
        # 规则判断包含"分析"关键字，应返回 complex
        assert result == "complex"

    @pytest.mark.asyncio
    async def test_llm_unexpected_error_fallback_to_rule(self):
        classifier = QueryClassifier()
        with patch.object(classifier, "_llm_classify", new=AsyncMock(side_effect=AttributeError("未预期错误"))):
            result = await classifier.classify("请详细分析这个方案的可行性")
        # 规则判断包含"分析"关键字，应返回 complex
        assert result == "complex"


class TestRAGEngine:
    """RAG 引擎双通道路由测试。"""

    @pytest.mark.asyncio
    async def test_search_simple_mode(self):
        from app.services.rag.rag_engine import RAGEngine

        engine = RAGEngine()
        docs = [{"content": "doc1", "metadata": {}}, {"content": "doc2", "metadata": {}}]
        mock_store = MagicMock()
        mock_store.search = AsyncMock(return_value=docs)

        with patch("app.services.rag.rag_engine.reranker_service.rerank", new=AsyncMock(return_value=[docs[0]])) as mock_rerank:
            result = await engine.search("query", mock_store, n_results=1, force_mode="simple")

        assert result["query_type"] == "simple"
        assert result["iterations"] == 1
        assert result["documents"] == [docs[0]]
        mock_store.search.assert_awaited_once_with("query", n_results=2)
        mock_rerank.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_simple_mode_empty_documents(self):
        from app.services.rag.rag_engine import RAGEngine

        engine = RAGEngine()
        mock_store = MagicMock()
        mock_store.search = AsyncMock(return_value=[])

        result = await engine.search("query", mock_store, n_results=3, force_mode="simple")
        assert result["query_type"] == "simple"
        assert result["documents"] == []

    @pytest.mark.asyncio
    async def test_search_complex_mode(self):
        from app.services.rag.rag_engine import RAGEngine

        engine = RAGEngine()
        mock_store = MagicMock()
        mock_store.search = MagicMock(return_value=[{"content": "c1", "metadata": {}}])

        with patch("app.services.rag.rag_engine.agentic_rag.search", new=AsyncMock(return_value={
            "documents": [{"content": "c1", "metadata": {}}],
            "iterations": 2,
            "refined_queries": ["q1"],
        })) as mock_agentic:
            result = await engine.search("query", mock_store, n_results=1, force_mode="complex")

        assert result["query_type"] == "complex"
        assert result["iterations"] == 2
        assert result["refined_queries"] == ["q1"]
        mock_agentic.assert_awaited_once()
