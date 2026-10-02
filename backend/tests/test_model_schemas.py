"""ORM JSON 列 Pydantic schema 测试（§4.2.3）。

重点验证三件事：
1. **约束真的生效** —— 越界值被钳制到合法域，而不是被静默接受或直接拒绝；
2. **历史脏数据不会炸** —— 库里已有的非 dict / 非 list 值必须能安全降级，
   否则「加 schema」本身就会变成线上事故源；
3. **不虚构不存在的字段** —— schema 覆盖的是代码里真实读写的键。
"""
import pytest
from pydantic import ValidationError

from app.models.schemas import (
    DEFAULT_TOP_K,
    TOP_K_MAX,
    TOP_K_MIN,
    AgentConfig,
    AgentKnowledgeConfig,
    CompilationArtifactOutput,
    ProcessingTaskErrorLog,
    resolve_top_k,
)


# ---------------------------------------------------------------------------
# 1. Agent.config
# ---------------------------------------------------------------------------

class TestAgentConfig:
    def test_empty_config_is_valid(self):
        cfg = AgentConfig.model_validate({})
        assert cfg.knowledge.topK is None
        assert cfg.top_k is None
        assert cfg.memory_config is None

    def test_nested_knowledge_topk(self):
        cfg = AgentConfig.model_validate({"knowledge": {"topK": 12}})
        assert cfg.knowledge.topK == 12
        assert cfg.resolved_top_k() == 12

    def test_legacy_flat_topk_still_accepted(self):
        """_resolve_top_k 同时兼容扁平 top_k，schema 不得把它判为非法。"""
        cfg = AgentConfig.model_validate({"top_k": 8})
        assert cfg.resolved_top_k() == 8

    def test_nested_wins_over_legacy(self):
        cfg = AgentConfig.model_validate({"knowledge": {"topK": 7}, "top_k": 3})
        assert cfg.resolved_top_k() == 7

    def test_out_of_range_is_clamped_not_rejected(self):
        """越界值必须在读取时被钳制，**不能**在 schema 校验期就拒绝。

        库中可能已存在 top_k=999 的历史行；若 schema 用 ge/le 硬拒，
        读取路径会抛 ValidationError，把「规范化 schema」变成线上事故源。
        既有契约（_resolve_top_k）是「越界则钳制」，本 schema 与之一致。
        """
        assert AgentConfig.model_validate({"top_k": 999}).resolved_top_k() == TOP_K_MAX
        assert AgentConfig.model_validate({"top_k": -5}).resolved_top_k() == TOP_K_MIN
        nested = AgentConfig.model_validate({"knowledge": {"topK": 999}})
        assert nested.resolved_top_k() == TOP_K_MAX

    def test_non_integer_topk_falls_back(self):
        cfg = AgentConfig.model_validate({"top_k": "abc"})
        assert cfg.resolved_top_k() == DEFAULT_TOP_K

    def test_unknown_keys_allowed(self):
        """历史遗留键不得导致整列读取失败。"""
        cfg = AgentConfig.model_validate({"some_legacy_flag": True, "knowledge": {"topK": 3}})
        assert cfg.resolved_top_k() == 3

    def test_null_knowledge_degrades(self):
        """knowledge=null 是历史常见脏值，不应让整列校验失败。"""
        cfg = AgentConfig.model_validate({"knowledge": None})
        assert cfg.knowledge.topK is None

    def test_memory_config_reuses_existing_schema(self):
        """memory_config 必须复用 app.schemas.compiler.MemoryConfig，不另造一套。"""
        from app.schemas.compiler import MemoryConfig

        cfg = AgentConfig.model_validate({"memory_config": {"long_term": {"enabled": True}}})
        assert isinstance(cfg.memory_config, MemoryConfig)

    def test_knowledge_submodel_is_optional_and_clamped(self):
        kc = AgentKnowledgeConfig.model_validate({})
        assert kc.topK is None
        assert AgentKnowledgeConfig.model_validate({"topK": 30}).topK == 30


class TestResolveTopK:
    def test_defaults_when_absent(self):
        assert resolve_top_k({}) == DEFAULT_TOP_K
        assert resolve_top_k(None) == DEFAULT_TOP_K

    def test_reads_nested_then_legacy(self):
        assert resolve_top_k({"knowledge": {"topK": 9}}) == 9
        assert resolve_top_k({"top_k": 6}) == 6

    def test_clamps_out_of_range(self):
        assert resolve_top_k({"top_k": 999}) == TOP_K_MAX
        assert resolve_top_k({"top_k": -5}) == TOP_K_MIN

    def test_clamps_non_integer(self):
        assert resolve_top_k({"top_k": "abc"}) == DEFAULT_TOP_K

    def test_survives_garbage_column(self):
        """列里存成 list / str 是历史事实，检索不能因此崩。"""
        assert resolve_top_k(["not", "a", "dict"]) == DEFAULT_TOP_K
        assert resolve_top_k("garbage") == DEFAULT_TOP_K
        assert resolve_top_k(42) == DEFAULT_TOP_K

    def test_matches_legacy_resolver_semantics(self):
        """与 app/api/agents/chat.py::_resolve_top_k 的钳制结果必须一致。"""
        from app.api.agents.chat import _resolve_top_k
        from app.models.agent import Agent

        cases = (
            {"top_k": 9},
            {"knowledge": {"topK": 4}},
            {"top_k": 999},
            {"top_k": -3},
            {"top_k": "x"},
            {},
        )
        for cfg in cases:
            agent = Agent(id="a", enterprise_id="e", name="n")
            agent.config = cfg
            assert resolve_top_k(cfg) == _resolve_top_k(agent), cfg


# ---------------------------------------------------------------------------
# 2. ProcessingTask.error_log
# ---------------------------------------------------------------------------

class TestProcessingTaskErrorLog:
    def test_well_formed_entries(self):
        log = ProcessingTaskErrorLog.coerce(
            [{"file": "a.pdf", "error": "解析失败", "timestamp": "2026-09-27T10:00:00"}]
        )
        assert len(log.entries) == 1
        assert log.entries[0].file == "a.pdf"

    def test_null_column_degrades_to_empty(self):
        assert ProcessingTaskErrorLog.coerce(None).entries == []

    def test_wrong_type_degrades_to_empty(self):
        """列被写成 dict / str 时必须降级，而不是抛错阻断任务详情页。"""
        assert ProcessingTaskErrorLog.coerce({"file": "a"}).entries == []
        assert ProcessingTaskErrorLog.coerce("boom").entries == []

    def test_bad_entries_skipped_others_survive(self):
        """单条脏记录不能连累同批次的其余记录。"""
        log = ProcessingTaskErrorLog.coerce(
            [{"file": "ok.pdf", "error": "x"}, "garbage", 123, {"file": "ok2.pdf"}]
        )
        assert [e.file for e in log.entries] == ["ok.pdf", "ok2.pdf"]

    def test_entry_with_bad_timestamp_is_skipped(self):
        log = ProcessingTaskErrorLog.coerce(
            [{"file": "a", "error": "e", "timestamp": "not-a-date"}, {"file": "b"}]
        )
        assert [e.file for e in log.entries] == ["b"]

    def test_empty_list(self):
        assert ProcessingTaskErrorLog.coerce([]).entries == []


# ---------------------------------------------------------------------------
# 3. CompilationArtifact.output
# ---------------------------------------------------------------------------

class TestCompilationArtifactOutput:
    def test_public_shell_constrained(self):
        out = CompilationArtifactOutput.model_validate(
            {"stage": "knowledge", "confidence": 0.87, "nodes": [{"id": "n1"}]}
        )
        assert out.stage == "knowledge"
        assert out.confidence == 0.87

    def test_confidence_out_of_range_rejected(self):
        """confidence 是本次新引入的强约束：越界应直接拒绝。"""
        with pytest.raises(ValidationError):
            CompilationArtifactOutput.model_validate({"confidence": 1.5})

    def test_stage_specific_payload_passed_through(self):
        """五级编译各级结构不同，私有载荷必须原样透传而非被拒。"""
        payload = {"raw_interview": [{"question": "q", "answer": "a"}], "arbitrary": {"deep": [1, 2]}}
        out = CompilationArtifactOutput.model_validate(payload)
        assert out.model_dump()["raw_interview"] == payload["raw_interview"]


# ---------------------------------------------------------------------------
# 4. 与 ORM 列的实测对齐
# ---------------------------------------------------------------------------

class TestAgainstOrmColumns:
    def test_empty_agent_config_resolves_to_default(self):
        from app.models.agent import Agent

        agent = Agent(id="a", enterprise_id="e", name="n")
        assert AgentConfig.model_validate(agent.config or {}).resolved_top_k() == DEFAULT_TOP_K

    def test_processing_task_table_name(self):
        from app.models.processing_task import ProcessingTask

        assert ProcessingTask.__tablename__ == "processing_tasks"

    def test_background_job_uses_same_error_log_shape(self):
        """BackgroundJob 是 ProcessingTask 的合并目标，error_log 形状必须一致。

        （本用例原先反向断言「BackgroundJob 尚不存在」；该模型已于 §4.2.3 落地，
        反向断言随之失效，改为守护合并后的形状一致性。）
        """
        from app.models.job import BackgroundJob

        entries = [{"file": "a.pdf", "error": "解析失败"}]
        assert ProcessingTaskErrorLog.coerce(entries).entries[0].file == "a.pdf"
        assert BackgroundJob.__tablename__ == "background_jobs"
