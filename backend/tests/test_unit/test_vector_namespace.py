"""企业级向量命名空间隔离回归测试。"""
import pytest

from app.services.vector_store import VectorStoreService, build_collection_name


ENTERPRISE_A = "11111111-1111-1111-1111-111111111111"
ENTERPRISE_B = "22222222-2222-2222-2222-222222222222"
AGENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_collection_name_contains_enterprise_and_agent_identity():
    assert build_collection_name(ENTERPRISE_A, AGENT_ID) == (
        "ent_11111111-1111-1111-1111-111111111111_agent_aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
    assert build_collection_name(ENTERPRISE_A, AGENT_ID) != build_collection_name(ENTERPRISE_B, AGENT_ID)


def test_vector_store_rejects_unscoped_arbitrary_collection_name():
    with pytest.raises(ValueError):
        VectorStoreService("shared_documents", client=object())


@pytest.mark.asyncio
async def test_agent_graph_vectorizer_writes_to_prefixed_collection(monkeypatch):
    """Agent Graph 新建知识库必须走企业前缀工厂，而不是旧 agent_{id} 集合。"""
    from app.services.agent_graph import make_vectorizer_node

    calls = []

    class FakeVectorStore:
        async def add_documents(self, documents, metadatas, ids):
            return None

    async def fake_create_prefixed(enterprise_id, agent_id):
        calls.append((enterprise_id, agent_id))
        return FakeVectorStore()

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, _statement):
            return FakeResult()

        async def commit(self):
            return None

    monkeypatch.setattr(
        VectorStoreService,
        "create_prefixed",
        fake_create_prefixed,
    )
    vectorizer = make_vectorizer_node(lambda: FakeSession())

    result = await vectorizer(
        {
            "enterprise_id": ENTERPRISE_A,
            "agent_id": AGENT_ID,
            "files": [],
            "chunks_by_file": [],
        }
    )

    assert calls == [(ENTERPRISE_A, AGENT_ID)]
    assert result["vector_count"] == 0


@pytest.mark.asyncio
async def test_agent_graph_vectorizer_raises_when_any_vector_batch_fails(monkeypatch):
    """向量写入失败不能被吞掉后把 Agent 错误标记为可用。"""
    from app.services.agent_graph import make_vectorizer_node

    class FailingVectorStore:
        async def add_documents(self, documents, metadatas, ids):
            raise RuntimeError("chroma unavailable")

    async def fake_create_prefixed(enterprise_id, agent_id):
        return FailingVectorStore()

    monkeypatch.setattr(
        VectorStoreService,
        "create_prefixed",
        fake_create_prefixed,
    )
    vectorizer = make_vectorizer_node(lambda: None)

    with pytest.raises(RuntimeError, match="向量化写入失败"):
        await vectorizer(
            {
                "enterprise_id": ENTERPRISE_A,
                "agent_id": AGENT_ID,
                "chunks_by_file": [
                    {
                        "file_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                        "file_name": "test.txt",
                        "file_path": "/safe/test.txt",
                        "file_type": "document",
                        "chunks": ["test document"],
                    }
                ],
            }
        )
