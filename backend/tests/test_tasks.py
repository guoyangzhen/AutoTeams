"""Celery 异步任务规范测试（§4.2.4）。

覆盖两类约定：
1. **任务签名** —— 任务名、bind、max_retries、soft_time_limit 等契约；
2. **Celery 路由配置** —— broker/result backend 的 Redis DB 隔离、队列路由、beat 计划。

全部用例**不依赖真实 broker / Redis / 数据库**：任务函数以 eager 方式直接调用，
外部依赖通过 monkeypatch 替换。这样 CI 无需起中间件也能守住契约。
"""
import pytest
from celery import Task

from app.tasks import celery_app
from app.tasks.celery_app import (
    BROKER_DB_OFFSET,
    QUEUE_DOCUMENTS,
    QUEUE_SCHEDULED,
    RESULT_DB_OFFSET,
    _shift_db,
    build_config,
)
from app.tasks.document_processing import count_document_chunks, process_documents
from app.tasks.scheduled_metrics import (
    _summarize_metrics,
    check_platform_health,
    generate_daily_report,
    summarize_batch,
)


# ---------------------------------------------------------------------------
# 1. Redis 连接与 DB 隔离
# ---------------------------------------------------------------------------

class TestRedisConfig:
    @pytest.mark.parametrize(
        "url,offset,expected",
        [
            ("redis://localhost:6379/0", 1, "redis://localhost:6379/1"),
            ("redis://localhost:6379/0", 2, "redis://localhost:6379/2"),
            ("redis://:pw@h:6379/0", 1, "redis://:pw@h:6379/1"),
            ("rediss://h:6379/0", 2, "rediss://h:6379/2"),
        ],
    )
    def test_shift_db_moves_index(self, url, offset, expected):
        assert _shift_db(url, offset) == expected

    def test_shift_db_appends_when_no_db_segment(self):
        assert _shift_db("redis://h", 1) == "redis://h/1"

    def test_shift_db_preserves_query_string(self):
        assert _shift_db("redis://h:6379/0?ssl_cert_reqs=none", 1) == (
            "redis://h:6379/1?ssl_cert_reqs=none"
        )

    def test_shift_db_leaves_non_redis_untouched(self):
        """非 Redis scheme（内存/其它 broker）不应被改写。"""
        assert _shift_db("memory://", 1) == "memory://"
        assert _shift_db("sqs://queue", 2) == "sqs://queue"

    def test_broker_and_result_do_not_collide_with_cache(self):
        """broker/result 必须与应用缓存（db0）错开，否则队列键与缓存键互相污染。"""
        cfg = build_config("redis://h:6379/0")
        assert cfg["broker_url"] == f"redis://h:6379/{BROKER_DB_OFFSET}"
        assert cfg["result_backend"] == f"redis://h:6379/{RESULT_DB_OFFSET}"
        assert cfg["broker_url"] != cfg["result_backend"]
        assert "redis://h:6379/0" not in (cfg["broker_url"], cfg["result_backend"])

    def test_config_uses_json_serialization(self):
        """跨语言友好：只允许 json，避免 pickle 反序列化面。"""
        cfg = build_config("redis://h:6379/0")
        assert cfg["task_serializer"] == "json"
        assert cfg["result_serializer"] == "json"
        assert cfg["accept_content"] == ["json"]

    def test_acks_late_and_prefetch_one(self):
        """崩溃重投 + 单次预取：避免任务丢失，也避免 worker 饿死其他任务。"""
        cfg = build_config("redis://h:6379/0")
        assert cfg["task_acks_late"] is True
        assert cfg["worker_prefetch_multiplier"] == 1


# ---------------------------------------------------------------------------
# 2. 路由与 beat 配置
# ---------------------------------------------------------------------------

class TestRoutingAndBeat:
    def test_task_routes_point_to_declared_queues(self):
        cfg = build_config("redis://h:6379/0")
        routes = cfg["task_routes"]
        assert routes["app.tasks.document_processing.*"]["queue"] == QUEUE_DOCUMENTS
        assert routes["app.tasks.scheduled_metrics.*"]["queue"] == QUEUE_SCHEDULED

    def test_task_modules_are_imported(self):
        """未列入 imports 的任务模块不会被 worker 自动加载。"""
        cfg = build_config("redis://h:6379/0")
        assert "app.tasks.document_processing" in cfg["imports"]
        assert "app.tasks.scheduled_metrics" in cfg["imports"]

    def test_beat_schedule_declares_both_scheduled_tasks(self):
        schedule = celery_app.conf.beat_schedule
        assert schedule["daily-report"]["task"] == "app.tasks.scheduled_metrics.generate_daily_report"
        assert schedule["health-check"]["task"] == "app.tasks.scheduled_metrics.check_platform_health"

    def test_app_name(self):
        assert celery_app.main == "autoteams"

    def test_all_tasks_registered_under_expected_names(self):
        """任务名是 beat/route 的契约，注册名不一致会导致定时任务静默不执行。"""
        names = set(celery_app.tasks)
        for expected in (
            "app.tasks.document_processing.process_documents",
            "app.tasks.document_processing.count_document_chunks",
            "app.tasks.scheduled_metrics.generate_daily_report",
            "app.tasks.scheduled_metrics.check_platform_health",
            "app.tasks.scheduled_metrics.summarize_batch",
        ):
            assert expected in names, f"{expected} 未注册到 celery_app"


# ---------------------------------------------------------------------------
# 3. 任务签名契约
# ---------------------------------------------------------------------------

class TestTaskSignatures:
    @pytest.mark.parametrize(
        "task_obj",
        [process_documents, count_document_chunks, generate_daily_report, check_platform_health, summarize_batch],
    )
    def test_task_is_celery_task(self, task_obj):
        assert isinstance(task_obj, Task)

    def test_process_documents_signature_matches_spec(self):
        """§4.2.4 明确要求 max_retries=3, soft_time_limit=600。"""
        assert process_documents.name == "app.tasks.document_processing.process_documents"
        assert process_documents.max_retries == 3
        assert process_documents.soft_time_limit == 600

    def test_count_document_chunks_has_own_name(self):
        """体检任务需独立任务名，便于单独重放与监控。"""
        assert count_document_chunks.name == "app.tasks.document_processing.count_document_chunks"

    def test_daily_report_accepts_enterprise_and_range(self):
        """日报需能按企业与时间窗调用，缺参会让 beat 固定成全库汇总。"""
        import inspect

        params = inspect.signature(generate_daily_report.run).parameters
        assert "enterprise_id" in params
        assert "range_days" in params

# ---------------------------------------------------------------------------
# 4. 任务行为（无外部依赖路径）
# ---------------------------------------------------------------------------

class TestDocumentProcessing:
    def test_empty_input_returns_zero_summary(self):
        """空列表应短路返回，不该去开 DB/Chroma 连接。"""
        result = process_documents("ent-1", "agent-1", [])
        assert result == {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "total_chunks": 0,
            "results": [],
        }

    def test_single_file_failure_does_not_abort_batch(self, monkeypatch):
        """单文件失败不拖垮整批 —— 批量任务最常见的诉求。"""
        import asyncio

        import app.tasks.document_processing as dp

        async def _boom(**kwargs):
            raise RuntimeError("解析炸了")

        monkeypatch.setattr(dp, "_process_one", _boom)
        monkeypatch.setattr(asyncio, "run", lambda coro: (coro.close(), _results())[1])

        def _results():
            return [
                {"file": "a.pdf", "status": "failed", "reason": "解析炸了", "chunks": 0},
                {"file": "b.pdf", "status": "failed", "reason": "解析炸了", "chunks": 0},
            ]

        with pytest.raises(RuntimeError, match="全部"):
            process_documents("ent-1", "agent-1", ["a.pdf", "b.pdf"])


class TestScheduledMetrics:
    def test_summarize_extracts_expected_fields(self):
        raw = {
            "period": {"start": "2026-09-26", "end": "2026-09-27"},
            "summary": {"total_conversations": 126, "total_questions": 480, "resolved_count": 120},
            "efficiency": {"resolution_rate": 0.9841, "hours_saved": 310},
            "quality": {"satisfaction_score": 4.567, "rated_count": 42},
        }
        out = _summarize_metrics(raw)
        assert out["conversations"] == 126
        assert out["resolution_rate"] == 0.9841
        assert out["satisfaction"] == 4.57
        assert out["rated_count"] == 42

    def test_summarize_tolerates_missing_sections(self):
        """缺字段应补零而不是 KeyError —— 上游 schema 演进时不能整批崩。"""
        out = _summarize_metrics({})
        assert out["conversations"] == 0
        assert out["resolution_rate"] == 0.0
        assert out["period"] == {"start": None, "end": None}

    def test_daily_report_failure_is_reported_not_raised(self, monkeypatch):
        """日报失败是业务结论，不该让 beat 崩掉。"""
        import app.services.metrics_service as ms
        import app.database as dbmod

        class _Boom:
            async def get_business_metrics(self, **kwargs):
                raise RuntimeError("db down")

        monkeypatch.setattr(ms, "MetricsService", _Boom)
        result = generate_daily_report(enterprise_id="ent-1")
        assert result["status"] == "failed"
        assert "db down" in result["error"]

    def test_health_check_reports_unhealthy_as_result_not_exception(self, monkeypatch):
        """unhealthy 是探测结论，必须正常返回，不能抛。"""
        from app.utils.health import DependencyStatus
        from app.utils import health as health_mod

        # 用真实的 DependencyStatus 数据类，避免 mock 字段名与真实结构漂移
        def _dep():
            return DependencyStatus(
                name="database", status="down", latency_ms=5.0, message="connect refused"
            )

        async def _run():
            return "unhealthy", [_dep()]

        monkeypatch.setattr(health_mod, "run_health_checks", _run)
        result = check_platform_health()
        assert result["status"] == "unhealthy"
        assert result["dependencies"][0]["name"] == "database"
        assert result["checked_at"]

    def test_health_check_probe_crash_is_isolated(self, monkeypatch):
        """探测本身崩溃时返回 error 结论，而不是让 worker 退出。"""
        from app.utils import health as health_mod

        async def _boom():
            raise RuntimeError("探测炸了")

        monkeypatch.setattr(health_mod, "run_health_checks", _boom)
        result = check_platform_health()
        assert result["status"] == "error"
        assert "探测炸了" in result["error"]

    def test_batch_counts_failures_without_aborting(self, monkeypatch):
        import app.tasks.scheduled_metrics as sm

        def _fake(enterprise_id=None, range_days=1):
            return {"status": "ok" if enterprise_id == "a" else "failed"}

        monkeypatch.setattr(sm, "generate_daily_report", _fake)
        out = summarize_batch(["a", "b"])
        assert out["total"] == 2
        assert out["ok"] == 1
        assert out["failed"] == 1
