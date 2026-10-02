"""AutoTeams 5.0 战役 2：分层长程认知记忆中枢测试。

覆盖三条主线（对齐 battle 2 §3）：
1. 剧集追加：record_episodic_trace 的落库、校验、企业隔离与向量索引降级。
2. 混合检索召回：Dense Vector + Sparse BM25 + Multi-hop Graph 三路召回与 RRF 融合。
3. 规程沉淀：consolidural 把高绩效轨迹蒸馏为经验基因卡。
外加因果链多跳回放与 /api/v1/memory 端点契约。

向量库在测试中以假实现注入：真实 ChromaDB 会让单测依赖外部服务，
而这里要验证的是「三路如何融合」，不是 Chroma 本身。
"""
import math

import pytest

from app.models.cognitive_memory import EpisodicTrace, ProceduralGene
from app.services.memory.cognitive import (
    BM25Index,
    CognitiveMemoryCore,
    reciprocal_rank_fusion,
    tokenize,
)


class FakeVectorStore:
    """假向量库：按字符 bigram 重叠度返回相似度，行为确定且无外部依赖。"""

    def __init__(self) -> None:
        self.documents: dict[str, str] = {}
        self.metadata: dict[str, dict] = {}

    async def add_documents(self, documents, metadatas, ids) -> None:
        for doc, meta, doc_id in zip(documents, metadatas, ids, strict=False):
            self.documents[doc_id] = doc
            self.metadata[doc_id] = meta

    async def search(self, query, n_results=5, where=None):
        query_grams = set(_bigrams(query))
        scored = []
        for doc_id, doc in self.documents.items():
            doc_grams = set(_bigrams(doc))
            if not query_grams or not doc_grams:
                continue
            overlap = len(query_grams & doc_grams) / len(query_grams)
            if overlap > 0:
                scored.append({
                    "content": doc,
                    "metadata": self.metadata[doc_id],
                    "distance": 1.0 - overlap,
                    "id": doc_id,
                })
        scored.sort(key=lambda item: item["distance"])
        return scored[:n_results]


def _bigrams(text: str) -> list[str]:
    return tokenize(text)


@pytest.fixture
def vector_store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture
def core(vector_store) -> CognitiveMemoryCore:
    return CognitiveMemoryCore(vector_store_factory=lambda _eid: _async_return(vector_store))


async def _async_return(value):
    return value


async def _failing_vector_store(_eid):
    raise RuntimeError("ChromaDB 未部署")


def _event(summary: str, **overrides) -> dict:
    payload = {
        "task_summary": summary,
        "causal_chain": [
            {"step": 1, "action": f"受理{summary}", "actor": "ATE-F-0001"},
            {"step": 2, "action": "复核并归档", "actor": "ATE-F-0002", "note": "留痕"},
        ],
        "reflection_notes": "流程可复用",
        "outcome_score": 0.9,
        "context_tags": ["对账"],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# 1. 剧集追加
# --------------------------------------------------------------------------- #
class TestEpisodicTraceRecording:
    async def test_records_trace_with_causal_chain(self, db_session, core):
        """剧集追加：时间序列快照与因果字段完整落库。"""
        trace = await core.record_episodic_trace(
            db_session, "ent-a", "ATE-F-0001", _event("客户退款审批")
        )

        assert trace.id
        assert trace.enterprise_id == "ent-a"
        assert trace.badge == "ATE-F-0001"
        assert trace.task_summary == "客户退款审批"
        assert [step["action"] for step in trace.causal_chain_json] == [
            "受理客户退款审批", "复核并归档",
        ]
        assert trace.reflection_notes == "流程可复用"
        assert trace.outcome_score == pytest.approx(0.9)
        assert trace.context_tags == ["对账"]

    async def test_rejects_blank_task_summary(self, db_session, core):
        """空摘要必须被拒绝，否则记忆层会积累无法检索的空轨迹。"""
        with pytest.raises(ValueError, match="task_summary"):
            await core.record_episodic_trace(
                db_session, "ent-a", "ATE-F-0001", _event("   ")
            )

    async def test_clamps_outcome_score_into_unit_range(self, db_session, core):
        """越界评分被收敛到 0..1，否则会污染经验蒸馏的置信度。"""
        trace = await core.record_episodic_trace(
            db_session, "ent-a", "ATE-F-0001", _event("异常评分", outcome_score=7.5)
        )
        assert trace.outcome_score == 1.0

    async def test_ignores_cross_enterprise_caused_by(self, db_session, core):
        """跨企业的因果父指针必须被丢弃，否则记忆可被串成他企业链路。"""
        other = await core.record_episodic_trace(
            db_session, "ent-b", "ATE-F-0009", _event("他企业任务")
        )
        trace = await core.record_episodic_trace(
            db_session,
            "ent-a",
            "ATE-F-0001",
            _event("本企业任务", caused_by_event_id=other.id),
        )
        assert trace.caused_by_event_id is None

    async def test_links_valid_caused_by(self, db_session, core):
        """同企业的因果父指针被保留，因果链可被回放。"""
        root = await core.record_episodic_trace(
            db_session, "ent-a", "ATE-F-0001", _event("根因任务")
        )
        child = await core.record_episodic_trace(
            db_session, "ent-a", "ATE-F-0002", _event("派生任务", caused_by_event_id=root.id)
        )
        assert child.caused_by_event_id == root.id

    async def test_indexes_trace_into_vector_store(self, db_session, core, vector_store):
        """写入后向量化：向量库按 trace_id 建索引，供 Dense 路召回。"""
        trace = await core.record_episodic_trace(
            db_session, "ent-a", "ATE-F-0001", _event("向量化验证")
        )
        assert trace.id in vector_store.documents
        assert vector_store.metadata[trace.id]["trace_id"] == trace.id

    async def test_vector_store_failure_does_not_block_write(self, db_session):
        """向量库不可用时记忆写入仍须成功——长程记忆是增强能力，不是单点故障。"""
        degraded = CognitiveMemoryCore(vector_store_factory=_failing_vector_store)
        trace = await degraded.record_episodic_trace(
            db_session, "ent-a", "ATE-F-0001", _event("降级写入")
        )
        assert trace.id


# --------------------------------------------------------------------------- #
# 2. 因果链回放
# --------------------------------------------------------------------------- #
class TestCausalChainReplay:
    async def test_replays_ancestors_and_descendants(self, db_session, core):
        """回放：向上追根因、向下追被引发的执行。"""
        a = await core.record_episodic_trace(db_session, "ent-a", "B1", _event("A 事件"))
        b = await core.record_episodic_trace(
            db_session, "ent-a", "B1", _event("B 事件", caused_by_event_id=a.id))
        c = await core.record_episodic_trace(
            db_session, "ent-a", "B1", _event("C 事件", caused_by_event_id=b.id))

        replay = await core.replay_causal_chain(db_session, "ent-a", b.id)
        by_id = {n.trace.id: n for n in replay.nodes}

        assert replay.root_trace_id == b.id
        assert by_id[b.id].direction == "self" and by_id[b.id].depth == 0
        assert by_id[a.id].direction == "ancestor" and by_id[a.id].depth == 1
        assert by_id[c.id].direction == "descendant" and by_id[c.id].depth == 1
        assert replay.max_depth_reached == 1
        assert replay.truncated is False

    async def test_replay_respects_max_depth(self, db_session, core):
        """深度上限必须真正截断，并如实标记 truncated。"""
        root = await core.record_episodic_trace(db_session, "ent-a", "B1", _event("第 0 层"))
        parent = root
        for level in range(1, 5):
            parent = await core.record_episodic_trace(
                db_session, "ent-a", "B1",
                _event(f"第 {level} 层", caused_by_event_id=parent.id),
            )

        replay = await core.replay_causal_chain(db_session, "ent-a", root.id, max_depth=2)
        assert replay.max_depth_reached == 2
        assert replay.truncated is True

    async def test_replay_rejects_other_enterprise(self, db_session, core):
        """他企业轨迹不可回放。"""
        trace = await core.record_episodic_trace(db_session, "ent-b", "B1", _event("他企业"))
        assert await core.replay_causal_chain(db_session, "ent-a", trace.id) is None


# --------------------------------------------------------------------------- #
# 3. 混合检索召回
# --------------------------------------------------------------------------- #


class TestHybridRetrieval:
    async def test_recall_attributes_all_three_channels(self, db_session, core):
        """三路各自命中，命中项的 channels 必须如实标注来源，供归因展示。"""
        await core.record_episodic_trace(db_session, "ent-a", "B1", _event("退款审批"))
        await core.record_episodic_trace(db_session, "ent-a", "B1", _event("对账复核"))
        await core.record_episodic_trace(
            db_session, "ent-a", "B1", _event("售后回访", context_tags=["售后"]))

        hits = await core.query_hybrid_memory(
            db_session, "ent-a", "退款审批", top_k=10)
        channels = {h.trace.task_summary: set(h.channels) for h in hits}

        assert "退款审批" in channels
        assert "dense" in channels["退款审批"]
        assert "sparse" in channels["退款审批"]
        # 因果邻居靠图路进来：种子无因果关系时不应凭空出现 graph 命中
        assert all("graph" not in c for c in channels.values())

    async def test_graph_channel_reaches_causal_neighbour(self, db_session, core):
        """因果邻居的字面与种子毫无重叠，只能由图路召回——这是三路互补的价值。"""
        root = await core.record_episodic_trace(
            db_session, "ent-a", "B1", _event("SLA-503 告警", context_tags=["对账"]))
        await core.record_episodic_trace(
            db_session, "ent-a", "B1",
            _event("zzz 完全不同措辞的收尾动作", caused_by_event_id=root.id, context_tags=["对账"]),
        )

        hits = await core.query_hybrid_memory(
            db_session, "ent-a", "SLA-503", context_tags=["对账"], top_k=10)
        by_summary = {h.trace.task_summary: h for h in hits}

        neighbour = by_summary["zzz 完全不同措辞的收尾动作"]
        assert neighbour.channels == ["graph"]
        assert neighbour.causal_distance == 1

    async def test_sparse_channel_matches_error_code_literally(self, db_session, core):
        """错误码必须字面命中：语义近似的轨迹不能挤掉字面精确匹配。"""
        await core.record_episodic_trace(db_session, "ent-a", "B1", _event("无关的报销流程"))
        await core.record_episodic_trace(db_session, "ent-a", "B1", _event("SLA-503 触发赔付"))

        hits = await core.query_hybrid_memory(db_session, "ent-a", "SLA-503", top_k=5)
        assert hits[0].trace.task_summary == "SLA-503 触发赔付"
        assert "sparse" in hits[0].channels

    async def test_context_tags_narrow_the_corpus(self, db_session, core):
        """上下文标签收窄语料：不在该域的轨迹不应被召回。"""
        await core.record_episodic_trace(db_session, "ent-a", "B1", _event("对账差异处理"))
        await core.record_episodic_trace(
            db_session, "ent-a", "B1", _event("对账差异处理", context_tags=["售后"]))

        hits = await core.query_hybrid_memory(
            db_session, "ent-a", "对账差异处理", context_tags=["售后"], top_k=10)
        assert len(hits) == 1
        assert hits[0].trace.context_tags == ["售后"]

    async def test_never_leaks_across_enterprises(self, db_session, core):
        """企业隔离是硬约束：检索不得返回他企业的任何记忆。"""
        await core.record_episodic_trace(db_session, "ent-b", "B1", _event("他企业机密流程"))
        hits = await core.query_hybrid_memory(db_session, "ent-a", "机密流程", top_k=10)
        assert hits == []

    async def test_blank_query_returns_nothing(self, db_session, core):
        """空查询直接短路，不做无谓的三路检索。"""
        await core.record_episodic_trace(db_session, "ent-a", "B1", _event("任意"))
        assert await core.query_hybrid_memory(db_session, "ent-a", "   ", top_k=5) == []

    async def test_dense_failure_degrades_to_sparse(self, db_session):
        """Chroma 挂掉时仍能靠 BM25 召回，检索不整体失败。"""
        degraded = CognitiveMemoryCore(vector_store_factory=_failing_vector_store)
        await degraded.record_episodic_trace(db_session, "ent-a", "B1", _event("降级召回验证"))

        hits = await degraded.query_hybrid_memory(db_session, "ent-a", "降级召回", top_k=5)
        assert hits and hits[0].channels == ["sparse"]

    async def test_fusion_prefers_multi_channel_documents(self, db_session, core):
        """RRF 的核心价值：dense+sparse 双路命中的文档排在只有 graph 命中的前面。"""
        anchor = await core.record_episodic_trace(
            db_session, "ent-a", "B1", _event("设备校准异常"))
        await core.record_episodic_trace(
            db_session, "ent-a", "B1",
            _event("措辞毫不相干的收尾动作", caused_by_event_id=anchor.id))
        await core.record_episodic_trace(db_session, "ent-a", "B1", _event("完全无关事务"))

        hits = await core.query_hybrid_memory(db_session, "ent-a", "设备校准异常", top_k=10)
        assert [h.trace.task_summary for h in hits] == [
            "设备校准异常", "措辞毫不相干的收尾动作",
        ]
        assert set(hits[0].channels) == {"dense", "sparse"}
        assert hits[1].channels == ["graph"]
        assert hits[0].score > hits[1].score

    async def test_top_k_is_respected(self, db_session, core):
        for i in range(6):
            await core.record_episodic_trace(db_session, "ent-a", "B1", _event(f"批量任务{i}"))
        hits = await core.query_hybrid_memory(db_session, "ent-a", "批量任务", top_k=2)
        assert len(hits) == 2


# --------------------------------------------------------------------------- #
# 4. 稀疏检索原语
# --------------------------------------------------------------------------- #
class TestSparsePrimitives:
    def test_tokenizer_splits_ascii_words_and_cjk_bigrams(self):
        """ASCII 走词、中文走 bigram：否则「退款」无法命中「退款审批」。"""
        tokens = tokenize("SLA-503 退款审批")
        assert "sla-503" in tokens
        assert "退款" in tokens and "审批" in tokens

    def test_bm25_ranks_matching_document_first(self):
        index = BM25Index({
            "hit": "SLA-503 触发赔付流程",
            "miss": "月度团建活动安排",
        })
        assert index.search("SLA-503", top_k=5)[0][0] == "hit"

    def test_bm25_returns_nothing_for_unseen_terms(self):
        index = BM25Index({"a": "退款审批"})
        assert index.search("完全无关的词") == []

    def test_rrf_rewards_agreement_across_rankings(self):
        """两路都排第 1 的文档，融合分必须高于只被一路排第 1 的文档。"""
        fused = reciprocal_rank_fusion([["x", "y"], ["x", "z"]])
        assert fused["x"] > fused["y"]
        assert fused["x"] > fused["z"]


# --------------------------------------------------------------------------- #
# 5. 规程沉淀
# --------------------------------------------------------------------------- #
class TestProceduralConsolidation:
    async def test_distills_gene_from_repeated_high_performance_traces(
        self, db_session, core
    ):
        """沉淀：同工号同触发模式的高绩效轨迹合并为一张经验基因卡。"""
        for _ in range(3):
            await core.record_episodic_trace(
                db_session, "ent-a", "ATE-F-0001", _event("对账差异处理", outcome_score=0.95))

        genes, scanned = await core.consolidate_procedural_memory(
            db_session, "ent-a", min_outcome_score=0.8, min_samples=2)

        assert scanned == 3
        assert len(genes) == 1
        gene = genes[0]
        assert gene.trigger_pattern == "对账差异处理"
        assert gene.badge == "ATE-F-0001"
        assert gene.support_count == 3
        assert len(gene.source_trace_ids) == 3
        assert "受理对账差异处理 → 复核并归档" in gene.successful_sop_patch
        assert "3 次高绩效执行" in gene.successful_sop_patch

    async def test_excludes_low_performance_traces(self, db_session, core):
        """低绩效执行不进经验库：规程卡只沉淀被验证有效的做法。"""
        for _ in range(3):
            await core.record_episodic_trace(
                db_session, "ent-a", "ATE-F-0001", _event("失败流程", outcome_score=0.2))

        genes, scanned = await core.consolidate_procedural_memory(
            db_session, "ent-a", min_outcome_score=0.8, min_samples=2)
        assert scanned == 0
        assert genes == []

    async def test_requires_minimum_sample_count(self, db_session, core):
        """单次成功不足以固化成规程，避免偶然高分写进铁律。"""
        await core.record_episodic_trace(
            db_session, "ent-a", "ATE-F-0001", _event("一次性成功", outcome_score=0.99))
        genes, _ = await core.consolidate_procedural_memory(
            db_session, "ent-a", min_outcome_score=0.8, min_samples=2)
        assert genes == []

    async def test_separates_genes_per_badge(self, db_session, core):
        """不同工号的经验互不混淆：能力归属到人，不是全局铁律。"""
        for _ in range(2):
            await core.record_episodic_trace(
                db_session, "ent-a", "ATE-F-0001", _event("同流程", outcome_score=0.9))
        for _ in range(2):
            await core.record_episodic_trace(
                db_session, "ent-a", "ATE-F-0002", _event("同流程", outcome_score=0.9))

        genes, _ = await core.consolidate_procedural_memory(
            db_session, "ent-a", min_outcome_score=0.8, min_samples=2)
        assert {g.badge for g in genes} == {"ATE-F-0001", "ATE-F-0002"}

    async def test_confidence_grows_with_support(self, db_session, core):
        """置信度随样本数收敛但增速递减：支持越多越可信，不会无限增长。"""
        # 每次沉淀取标量快照：merge 复用同一 ORM 实例，直接持有对象会被后续沉淀改写
        async def snapshot() -> tuple[int, float]:
            await core.consolidate_procedural_memory(
                db_session, "ent-a", min_outcome_score=0.8, min_samples=2)
            gene = (await core.list_genes(db_session, "ent-a"))[0]
            return gene.support_count, gene.confidence_rating

        for _ in range(2):
            await core.record_episodic_trace(
                db_session, "ent-a", "B1", _event("收敛验证", outcome_score=0.8))
        two_support, two_conf = await snapshot()

        for _ in range(6):
            await core.record_episodic_trace(
                db_session, "ent-a", "B1", _event("收敛验证", outcome_score=0.8))
        eight_support, eight_conf = await snapshot()

        assert (two_support, eight_support) == (2, 8)
        assert two_conf < eight_conf < 0.8
        assert eight_conf == pytest.approx(0.8 * (1 - math.exp(-8 / 2)), abs=1e-4)

    async def test_consolidation_is_idempotent(self, db_session, core):
        """重复沉淀不堆出重复卡：业务键确定性 + upsert 语义。"""
        for _ in range(2):
            await core.record_episodic_trace(
                db_session, "ent-a", "B1", _event("幂等验证", outcome_score=0.9))

        await core.consolidate_procedural_memory(db_session, "ent-a", min_samples=2)
        await core.consolidate_procedural_memory(db_session, "ent-a", min_samples=2)

        genes = await core.list_genes(db_session, "ent-a")
        assert len(genes) == 1

    async def test_never_distills_other_enterprises_traces(self, db_session, core):
        """沉淀同样受企业隔离约束。"""
        for _ in range(3):
            await core.record_episodic_trace(
                db_session, "ent-b", "B1", _event("他企业流程", outcome_score=0.99))
        genes, scanned = await core.consolidate_procedural_memory(
            db_session, "ent-a", min_samples=2)
        assert (genes, scanned) == ([], 0)


# --------------------------------------------------------------------------- #
# 6. 图谱
# --------------------------------------------------------------------------- #
class TestMemoryGraph:
    async def test_graph_links_causes_and_distillation(self, db_session, core):
        """图谱同时表达 caused_by 与 distilled_into 两类边。"""
        root = await core.record_episodic_trace(
            db_session, "ent-a", "B1", _event("图谱根因", outcome_score=0.95))
        await core.record_episodic_trace(
            db_session, "ent-a", "B1",
            _event("图谱根因", caused_by_event_id=root.id, outcome_score=0.95))
        genes, _ = await core.consolidate_procedural_memory(
            db_session, "ent-a", min_outcome_score=0.8, min_samples=2)

        graph = await core.build_memory_graph(db_session, "ent-a")
        kinds = {edge["kind"] for edge in graph["edges"]}
        assert kinds == {"caused_by", "distilled_into"}
        assert graph["stats"] == {
            "trace_count": 2, "gene_count": len(genes), "edge_count": len(graph["edges"]),
        }
        assert {node["kind"] for node in graph["nodes"]} == {"trace", "gene"}


# --------------------------------------------------------------------------- #
# 7. API 契约
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_chroma(monkeypatch):
    """API 层走降级路径：不让单测依赖外部 Chroma 服务。"""
    from app.services.memory.cognitive import dense

    async def _boom(_eid):
        raise RuntimeError("ChromaDB 未部署")

    monkeypatch.setattr(dense, "default_vector_store", _boom)


def _payload(summary: str, **overrides) -> dict:
    body = {
        "badge": "ATE-F-0001",
        "event_trace": {
            "task_summary": summary,
            "causal_chain": [{"step": 1, "action": f"受理{summary}"}],
            "reflection_notes": "复盘完成",
            "outcome_score": 0.92,
            "context_tags": ["对账"],
        },
    }
    body.update(overrides)
    return body


class TestCognitiveMemoryApi:
    async def test_requires_authentication(self, client):
        assert (await client.get("/api/v1/memory/traces")).status_code == 401

    async def test_records_and_lists_traces(
        self, enterprise_authenticated_client, no_chroma
    ):
        created = await enterprise_authenticated_client.post(
            "/api/v1/memory/traces", json=_payload("合同审批受阻"))
        assert created.status_code == 201, created.text
        body = created.json()["data"]
        assert body["task_summary"] == "合同审批受阻"
        assert body["badge"] == "ATE-F-0001"
        assert body["causal_chain"][0]["step"] == 1

        listed = await enterprise_authenticated_client.get("/api/v1/memory/traces")
        assert listed.status_code == 200
        assert [t["id"] for t in listed.json()["data"]] == [body["id"]]

    async def test_rejects_invalid_payload(self, enterprise_authenticated_client, no_chroma):
        resp = await enterprise_authenticated_client.post(
            "/api/v1/memory/traces", json=_payload("x", event_trace={"task_summary": ""}))
        assert resp.status_code == 422

    async def test_replay_endpoint_returns_causal_nodes(
        self, enterprise_authenticated_client, no_chroma
    ):
        root = (await enterprise_authenticated_client.post(
            "/api/v1/memory/traces", json=_payload("根因事件"))).json()["data"]
        child = (await enterprise_authenticated_client.post(
            "/api/v1/memory/traces",
            json=_payload("派生事件", event_trace={
                "task_summary": "派生事件",
                "caused_by_event_id": root["id"],
                "causal_chain": [],
                "outcome_score": 0.5,
                "context_tags": [],
            }),
        )).json()["data"]

        replay = (await enterprise_authenticated_client.get(
            f"/api/v1/memory/traces/{child['id']}/replay")).json()["data"]
        assert replay["root_trace_id"] == child["id"]
        assert {n["direction"] for n in replay["nodes"]} == {"self", "ancestor"}
        assert replay["max_depth_reached"] == 1

    async def test_replay_unknown_trace_returns_404(
        self, enterprise_authenticated_client, no_chroma
    ):
        resp = await enterprise_authenticated_client.get(
            "/api/v1/memory/traces/does-not-exist/replay")
        assert resp.status_code == 404

    async def test_query_endpoint_reports_channel_attribution(
        self, enterprise_authenticated_client, no_chroma
    ):
        await enterprise_authenticated_client.post(
            "/api/v1/memory/traces", json=_payload("SLA-503 赔付"))

        resp = await enterprise_authenticated_client.post(
            "/api/v1/memory/query", json={"query": "SLA-503", "top_k": 5})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["fusion"] == "rrf"
        assert data["channel_counts"]["sparse"] == 1
        assert data["hits"][0]["task_summary"] == "SLA-503 赔付"

    async def test_consolidate_then_list_genes(
        self, enterprise_authenticated_client, no_chroma
    ):
        for _ in range(2):
            await enterprise_authenticated_client.post(
                "/api/v1/memory/traces", json=_payload("对账差异处理"))

        resp = await enterprise_authenticated_client.post(
            "/api/v1/memory/genes/consolidate", json={"min_samples": 2})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["distilled"] == 1
        assert data["scanned_traces"] == 2

        genes = (await enterprise_authenticated_client.get("/api/v1/memory/genes")).json()["data"]
        assert len(genes) == 1
        assert genes[0]["confidence_rating"] > 0

    async def test_graph_endpoint_shape(self, enterprise_authenticated_client, no_chroma):
        await enterprise_authenticated_client.post(
            "/api/v1/memory/traces", json=_payload("图谱探查"))
        data = (await enterprise_authenticated_client.get(
            "/api/v1/memory/graph")).json()["data"]
        assert data["stats"]["trace_count"] == 1
        assert all({"id", "label", "kind", "weight"} <= set(n) for n in data["nodes"])

    async def test_user_without_enterprise_cannot_write(
        self, authenticated_client, no_chroma
    ):
        """未归属企业的用户不得写入长程记忆。"""
        resp = await authenticated_client.post(
            "/api/v1/memory/traces", json=_payload("无企业归属"))
        assert resp.status_code == 403
