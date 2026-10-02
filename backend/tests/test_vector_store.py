"""P3-5: app/services/vector_store.py 覆盖率补充测试。"""
import uuid
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from app.services.vector_store import (
    VectorStoreService,
    get_chroma_client,
    _validate_collection_name,
    ChromaDBConnectionError,
    _encode_texts,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _make_mock_client():
    client = MagicMock()
    collection = MagicMock()
    client.get_or_create_collection.return_value = collection
    client.heartbeat.return_value = True
    return client, collection


class TestValidateCollectionName:
    """集合名校验测试。"""

    @pytest.mark.asyncio
    async def test_valid_name(self):
        await _validate_collection_name(f"agent_{_uuid()}")

    @pytest.mark.asyncio
    async def test_invalid_name_raises(self):
        with pytest.raises(ValueError, match="非法 collection_name"):
            await _validate_collection_name("bad_name")

    @pytest.mark.asyncio
    async def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="非法 collection_name"):
            await _validate_collection_name("")


class TestVectorStoreServiceInit:
    """初始化测试。"""

    def test_init_with_injected_client(self):
        client, collection = _make_mock_client()
        vs = VectorStoreService(f"agent_{_uuid()}", client=client)
        assert vs.client is client
        # P0-PERF: __init__ 不再同步调用 ChromaDB，collection 需通过 initialize() 异步加载
        assert vs.collection is None

    @pytest.mark.asyncio
    async def test_initialize_sets_collection(self):
        client, collection = _make_mock_client()
        vs = VectorStoreService(f"agent_{_uuid()}", client=client)
        result = await vs.initialize()
        assert result is vs
        assert vs.collection is collection
        client.get_or_create_collection.assert_called_once()

    def test_init_rejects_invalid_collection(self):
        client, _ = _make_mock_client()
        with pytest.raises(ValueError, match="非法 collection_name"):
            VectorStoreService("invalid", client=client)


class TestAddDocuments:
    """add_documents 测试。"""

    @pytest.mark.asyncio
    async def test_add_documents_batched(self):
        client, collection = _make_mock_client()
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()

        docs = ["doc"] * 250
        metadatas = [{"s": i} for i in range(250)]
        ids = [f"id{i}" for i in range(250)]

        # P1-RAG: 禁用自定义 embedding，让 ChromaDB 使用默认模型
        with patch("app.services.vector_store._encode_texts", return_value=None):
            await vs.add_documents(docs, metadatas, ids)

        assert collection.add.call_count == 3

    @pytest.mark.asyncio
    async def test_add_documents_with_embeddings(self):
        client, collection = _make_mock_client()
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()

        docs = ["doc1", "doc2"]
        metadatas = [{"s": 0}, {"s": 1}]
        ids = ["id1", "id2"]
        embeddings = [[0.1, 0.2], [0.3, 0.4]]

        with patch("app.services.vector_store._encode_texts", return_value=embeddings):
            await vs.add_documents(docs, metadatas, ids)

        collection.add.assert_called_once_with(
            documents=docs,
            metadatas=metadatas,
            ids=ids,
            embeddings=embeddings,
        )

    @pytest.mark.asyncio
    async def test_add_empty_documents(self):
        client, collection = _make_mock_client()
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        await vs.add_documents([], [], [])
        collection.add.assert_not_called()


class TestSearch:
    """search 测试。"""

    @pytest.mark.asyncio
    async def test_search_returns_documents(self):
        client, collection = _make_mock_client()
        collection.query.return_value = {
            "documents": [["hello", "world"]],
            "metadatas": [[{"source": "a"}, {"source": "b"}]],
            "distances": [[0.1, 0.2]],
            "ids": [["id1", "id2"]],
        }
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()

        with patch("app.services.vector_store._encode_texts", return_value=None):
            results = await vs.search("query", n_results=2)

        assert len(results) == 2
        assert results[0]["content"] == "hello"
        assert results[0]["metadata"]["source"] == "a"

    @pytest.mark.asyncio
    async def test_search_with_query_embeddings(self):
        client, collection = _make_mock_client()
        collection.query.return_value = {
            "documents": [["hello"]],
            "metadatas": [[{"source": "a"}]],
            "distances": [[0.1]],
            "ids": [["id1"]],
        }
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        query_embeddings = [[0.9, 0.1]]

        with patch("app.services.vector_store._encode_texts", return_value=query_embeddings):
            results = await vs.search("query", n_results=1)

        assert len(results) == 1
        collection.query.assert_called_once_with(
            n_results=1,
            query_embeddings=query_embeddings,
        )

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        client, collection = _make_mock_client()
        collection.query.return_value = {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        with patch("app.services.vector_store._encode_texts", return_value=None):
            assert await vs.search("q") == []

    @pytest.mark.asyncio
    async def test_search_chroma_error_returns_empty(self):
        import chromadb.errors
        client, collection = _make_mock_client()
        collection.query.side_effect = chromadb.errors.ChromaError("boom")
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        with patch("app.services.vector_store._encode_texts", return_value=None):
            assert await vs.search("q") == []

    @pytest.mark.asyncio
    async def test_search_value_error_returns_empty(self):
        client, collection = _make_mock_client()
        collection.query.side_effect = ValueError("bad params")
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        with patch("app.services.vector_store._encode_texts", return_value=None):
            assert await vs.search("q") == []


class TestDelete:
    """delete / delete_by_metadata / delete_collection / count 测试。"""

    @pytest.mark.asyncio
    async def test_delete_by_ids(self):
        client, collection = _make_mock_client()
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        await vs.delete(["id1", "id2"])
        collection.delete.assert_called_once_with(ids=["id1", "id2"])

    @pytest.mark.asyncio
    async def test_delete_empty_ids(self):
        client, collection = _make_mock_client()
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        await vs.delete([])
        collection.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_by_metadata(self):
        client, collection = _make_mock_client()
        collection.get.return_value = {"ids": ["id1", "id2"]}
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()

        count = await vs.delete_by_metadata({"source": "a"})
        assert count == 2
        collection.delete.assert_called_once_with(ids=["id1", "id2"])

    @pytest.mark.asyncio
    async def test_delete_collection(self):
        collection_name = f"agent_{_uuid()}"
        client, _ = _make_mock_client()
        vs = await VectorStoreService(collection_name, client=client).initialize()
        await vs.delete_collection()
        client.delete_collection.assert_called_once_with(collection_name)

    @pytest.mark.asyncio
    async def test_count(self):
        client, collection = _make_mock_client()
        collection.count.return_value = 42
        vs = await VectorStoreService(f"agent_{_uuid()}", client=client).initialize()
        assert await vs.count() == 42


class TestGetChromaClient:
    """get_chroma_client 测试。"""

    @pytest.mark.asyncio
    async def test_returns_cached_client(self):
        mock_client = MagicMock()
        with patch("app.services.vector_store._client", mock_client):
            result = await get_chroma_client()
            assert result is mock_client
            mock_client.heartbeat.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_client(self):
        mock_client = MagicMock()
        with patch("app.services.vector_store._client", None):
            with patch("app.services.vector_store._build_chroma_client", return_value=mock_client):
                result = await get_chroma_client()
                assert result is mock_client
                mock_client.heartbeat.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_error(self):
        import chromadb.errors
        with patch("app.services.vector_store._client", None):
            with patch(
                "app.services.vector_store._build_chroma_client",
                side_effect=chromadb.errors.ChromaError("fail"),
            ):
                with pytest.raises(ChromaDBConnectionError):
                    await get_chroma_client()


class TestEncodeTexts:
    """P1-RAG: embedding 编码辅助函数测试。"""

    @pytest.mark.asyncio
    async def test_encode_texts_empty(self):
        with patch("app.services.vector_store._embedding_api_keys", return_value=[]):
            assert await _encode_texts([]) == []

    @pytest.mark.asyncio
    async def test_encode_texts_model_unavailable(self):
        with patch("app.services.vector_store._embedding_api_keys", return_value=[]):
            assert await _encode_texts(["hello"]) is None

    @pytest.mark.asyncio
    async def test_encode_texts_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("app.services.vector_store._embedding_api_keys", return_value=["test_key"]), \
             patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
            result = await _encode_texts(["a", "b"])
            assert result == [[0.1, 0.2], [0.3, 0.4]]

    @pytest.mark.asyncio
    async def test_encode_texts_failure_returns_none(self):
        with patch("app.services.vector_store._embedding_api_keys", return_value=["test_key"]), \
             patch("httpx.AsyncClient.post", AsyncMock(side_effect=RuntimeError("boom"))):
            assert await _encode_texts(["a"]) is None
