"""认知记忆精简版的存取契约与三路检索测试。

对应重构计划 §4.2.2：``services/memory/cognitive/`` 按职责拆分后，本文件钉住
**跨模块对外可见的行为**——记忆存取契约（情景轨迹落库/回列、实体记忆抽取持久化）
与 Dense + Sparse + Graph 三路检索的融合语义。

分工：``test_cognitive_memory.py`` 覆盖各条单线功能的细节，本文件只覆盖
「拆分后仍然成立」的那部分：三路各自能独立召回、向量库挂掉时降级不失败、
企业隔离不被跨越。

向量库以假实现注入：真实 ChromaDB 会让单测依赖外部服务，而这里要验证的是
「三路如何融合」，不是 Chroma 本身。
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.memory.cognitive import CognitiveMemoryCore, dense, tokenize
from app.services.memory.entity_memory import EntityMemoryService


ENT_A = "ent-simplified-a"
ENT_B = "ent-simplified-b"


class FakeVectorStore:
    """假向量库。

    默认按 token 重叠度给距离；``forced`` 置位时直接返回指定结果，用来构造
    「语义近似但字面零重叠」的召回，隔离出 Dense 路自身的行为。
    """

    def __init__(self) -> None:
        self.documents: dict[str, str] = {}
        self.metadata: dict[str, dict] = {}
        self.forced: list[dict] | None = None

    async def add_documents(self, documents, metadatas, ids) -> None:
        for doc, meta, doc_id in zip(documents, metadatas, ids, strict=False):
            self.documents[doc_id] = doc
            self.metadata[doc_id] = meta

    async def search(self, query, n_results=5, where=None):
        if self.forced is not None:
            return self.forced[:n_results]
        query_tokens = set(tokenize(query))
        scored = []
        for doc_id, doc in self.documents.items():
            overlap = len(query_tokens & set(tokenize(doc))) / len(query_tokens or {1})
            if overlap > 0:
                scored.append({
                    "content": doc,
                    "metadata": self.metadata[doc_id],
                    "distance": 1.0 - overlap,
                    "id": doc_id,
                })
        scored.sort(key=lambda item: item["distance"])
        return scored[:n_results]


async def _resolve(value):
    return value


async def _failing_vector_store(_enterprise_id):
    raise RuntimeError("ChromaDB 未部署")


def _core(vector_store: FakeVectorStore) -> CognitiveMemoryCore:
    return CognitiveMemoryCore(vector_store_factory=lambda _eid: _resolve(vector_store))


@pytest.fixture
def vector_store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture
def core(vector_store) -> CognitiveMemoryCore:
    return _core(vector_store)


def _event(summary: str, **overrides) -> dict:
    payload = {
        "task_summary": summary,
        "causal_chain": [{"step": 1, "action": f"受理{summary}"}],
        "reflection_notes": "流程可复用",
        "outcome_score": 0.9,
        "context_tags": ["对账"],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# 1. 记忆存取契约
# --------------------------------------------------------------------------- #
class TestMemoryStoreContract:
    async def test_recorded_trace_round_trips(self, db_session, core):
        """存取契约：写入的因果链、反思、标签原样落库并可回列。"""
        trace = await core.record_episodic_trace(
            db_session, ENT_A, "ATE-F-0001", _event("客户退款审批")
        )

        listed = await core.list_traces(db_session, ENT_A)
        stored = next(t for t in listed if t.id == trace.id)
        assert stored.task_summary == "客户退款审批"
        assert stored.badge == "ATE-F-0001"
        assert [step["action"] for step in stored.causal_chain_json] == ["受理客户退款审批"]
        assert stored.context_tags == ["对账"]

    async def test_list_traces_excludes_other_enterprise(self, db_session, core):
        """企业隔离是硬约束：A 企业不得看到 B 企业的轨迹。"""
        await core.record_episodic_trace(db_session, ENT_A, "B1", _event("A 企业任务"))
        await core.record_episodic_trace(db_session, ENT_B, "B2", _event("B 企业任务"))

        summaries = {t.task_summary for t in await core.list_traces(db_session, ENT_A)}
        assert summaries == {"A 企业任务"}

    async def test_entity_extraction_persists_and_merges(self, db_session):
        """实体记忆抽取：同实体二次写入合并属性而非新增行。"""
        svc = EntityMemoryService()
        with patch.object(EntityMemoryService, "_sync_to_graph", new=AsyncMock()):
            await svc.upsert_entity(
                db_session, "agent-1", ENT_A, "customer", "C-001",
                attributes={"name": "华智制造", "budget": 200000},
            )
            merged = await svc.upsert_entity(
                db_session, "agent-1", ENT_A, "customer", "C-001",
                attributes={"focus": "测温精度"},
            )

        assert merged.attributes == {"name": "华智制造", "budget": 200000, "focus": "测温精度"}
        reloaded = await svc.get_entity(db_session, "agent-1", ENT_A, "customer", "C-001")
        assert reloaded.attributes["focus"] == "测温精度"

        others = await svc.get_entities_by_type(db_session, "agent-1", "customer")
        assert [e.entity_id for e in others] == ["C-001"]


# --------------------------------------------------------------------------- #
# 2. 三路检索
# --------------------------------------------------------------------------- #
class TestThreeWayRetrieval:
    async def test_dense_channel_recalls_semantic_neighbour(
        self, db_session, core, vector_store
    ):
        """Dense 路：字面零重叠的近义轨迹仍被召回，且归因为 dense。"""
        await core.record_episodic_trace(
            db_session, ENT_A, "B1", _event("客诉工单闭环处理")
        )
        target = await core.record_episodic_trace(
            db_session, ENT_A, "B2", _event("客户投诉处理")
        )
        # 查询词与两条轨迹均无 token 重叠，召回只能来自向量库
        vector_store.forced = [{"id": target.id, "metadata": {"trace_id": target.id}, "distance": 0.1}]

        hits = await core.query_hybrid_memory(db_session, ENT_A, "zebra-unrelated")

        hit = next(h for h in hits if h.trace.id == target.id)
        assert "dense" in hit.channels

    async def test_sparse_channel_matches_error_code_literally(
        self, db_session, core
    ):
        """Sparse 路：错误码必须字面命中，不能被语义近似抹平。"""
        await core.record_episodic_trace(
            db_session, ENT_A, "B1", _event("退款工单", reflection_notes="触发 SLA-503 升级")
        )
        await core.record_episodic_trace(
            db_session, ENT_A, "B2", _event("无关任务", reflection_notes="正常归档")
        )

        hits = await core.query_hybrid_memory(db_session, ENT_A, "SLA-503")

        assert [h.trace.task_summary for h in hits] == ["退款工单"]
        assert "sparse" in hits[0].channels

    async def test_graph_channel_pulls_causal_neighbour(self, db_session):
        """Graph 路：与种子同因的邻居即使字面无关也被召回，并带跳数。"""
        # 注入失败的向量库以关闭 dense 路：此处要观察的只有 sparse + graph
        degraded = CognitiveMemoryCore(vector_store_factory=_failing_vector_store)
        seed = await degraded.record_episodic_trace(
            db_session, ENT_A, "B1", _event("触发源单")
        )
        caused = await degraded.record_episodic_trace(
            db_session, ENT_A, "B2", _event("下游处置", caused_by_event_id=seed.id)
        )

        hits = await degraded.query_hybrid_memory(db_session, ENT_A, "触发源单")
        by_id = {h.trace.id: h for h in hits}

        assert by_id[seed.id].channels == ["sparse"]
        assert by_id[caused.id].channels == ["graph"]
        assert by_id[caused.id].causal_distance == 1

    async def test_fusion_prefers_multi_channel_consensus(self, db_session, core, vector_store):
        """RRF 融合：dense 与 sparse 同时召回的文档排在单路 graph 命中之上。"""
        seed = await core.record_episodic_trace(
            db_session, ENT_A, "B1", _event("触发源单")
        )
        # 摘要含查询词 → sparse 命中；再由向量库强制召回 → dense 也命中
        consensus = await core.record_episodic_trace(
            db_session, ENT_A, "B2", _event("触发源单下游处置", caused_by_event_id=seed.id)
        )
        # 字面与语义都无关，仅靠 graph 从 consensus 扩散得到
        neighbor = await core.record_episodic_trace(
            db_session, ENT_A, "B3", _event("旁支处置", caused_by_event_id=consensus.id)
        )
        vector_store.forced = [
            {"id": consensus.id, "metadata": {"trace_id": consensus.id}, "distance": 0.05},
        ]

        hits = await core.query_hybrid_memory(db_session, ENT_A, "触发源单")
        by_id = {h.trace.id: h for h in hits}
        order = [h.trace.id for h in hits]

        assert {"dense", "sparse"} <= set(by_id[consensus.id].channels)
        assert by_id[neighbor.id].channels == ["graph"]
        assert order.index(consensus.id) < order.index(neighbor.id)

    async def test_degrades_to_two_channels_when_vector_store_down(self, db_session):
        """向量库不可用时降级为 sparse + graph，而非整条检索失败。"""
        degraded = CognitiveMemoryCore(vector_store_factory=_failing_vector_store)
        await degraded.record_episodic_trace(
            db_session, ENT_A, "B1", _event("触发源单")
        )
        await degraded.record_episodic_trace(
            db_session, ENT_A, "B2", _event("下游处置")
        )

        hits = await degraded.query_hybrid_memory(db_session, ENT_A, "触发源单")

        assert [h.trace.task_summary for h in hits] == ["触发源单"]
        assert "dense" not in hits[0].channels

    async def test_query_never_crosses_enterprise(self, db_session, core, vector_store):
        """检索语料按 enterprise_id 硬隔离，跨企业命中被丢弃。"""
        await core.record_episodic_trace(db_session, ENT_B, "B9", _event("他企业退款审批"))
        vector_store.forced = []

        hits = await core.query_hybrid_memory(db_session, ENT_A, "退款审批")

        assert hits == []
        leaked = await core.query_hybrid_memory(db_session, ENT_B, "退款审批")
        assert [h.trace.enterprise_id for h in leaked] == [ENT_B]

    async def test_context_tags_narrow_corpus(self, db_session, core):
        """上下文标签是检索收窄条件：未打标签的轨迹不进语料。"""
        tagged = await core.record_episodic_trace(
            db_session, ENT_A, "B1", _event("对账差异核查", context_tags=["对账"])
        )
        await core.record_episodic_trace(
            db_session, ENT_A, "B2", _event("对账差异核查", context_tags=["招聘"])
        )

        hits = await core.query_hybrid_memory(
            db_session, ENT_A, "对账差异核查", context_tags=["对账"]
        )

        assert [h.trace.id for h in hits] == [tagged.id]


class TestVectorCollectionStability:
    """向量 collection 名的持久化契约。

    collection_name 由 uuid5(种子, enterprise_id) 派生，种子字符串一旦改动，
    所有企业已索引的向量就全部失联：写入与查询都「成功」，但 dense 路召回恒为空，
    不报任何错。功能测试无法察觉这种漂移，故把已知值钉死。
    """

    def test_collection_id_matches_pre_refactor_value(self):
        namespace = uuid.uuid5(uuid.NAMESPACE_OID, "autoteams-procedural-gene")
        collection_id = str(uuid.uuid5(namespace, "memory-lt-ent-1"))

        assert collection_id == "eeac065d-977e-5f02-a880-7b3e5e89459c"

    async def test_default_store_targets_the_stable_collection(self):
        """默认向量库工厂必须落在上面那个 collection 上。"""
        expected_id = "eeac065d-977e-5f02-a880-7b3e5e89459c"
        captured = {}

        class _Spy:
            @staticmethod
            async def create(name):
                captured["name"] = name
                return object()

        monkey = pytest.MonkeyPatch()
        try:
            import app.services.vector_store as vs
            monkey.setattr(vs.VectorStoreService, "create", _Spy.create)
            await dense.default_vector_store("ent-1")
        finally:
            monkey.undo()

        assert expected_id in captured["name"]
        assert captured["name"].startswith("ent_ent-1_agent_")
