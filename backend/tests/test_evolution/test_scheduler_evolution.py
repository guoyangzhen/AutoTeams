"""WT4 进化层定时任务测试（scheduler_service 加性追加部分）。

测试覆盖：
- evolution_continuous_optimize：遍历活跃企业执行持续优化分析
- evolution_org_metrics_snapshot：遍历活跃企业生成指标快照
- evolution_advisor_generate：遍历活跃企业生成 Advisor 建议
- _register_evolution_jobs：任务注册幂等性
- 空企业列表时安全跳过
- 单企业失败不影响其他企业

关键约束：
- 后台任务使用 async_session_factory() 独立 session（spec §2.3）
- LLM 调用全 mock
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.scheduler_service import (
    evolution_continuous_optimize,
    evolution_org_metrics_snapshot,
    evolution_advisor_generate,
    _register_evolution_jobs,
    _EVOLUTION_OPTIMIZE_JOB_ID,
    _EVOLUTION_METRICS_JOB_ID,
    _EVOLUTION_ADVISOR_JOB_ID,
)

# 共享 fixtures（conftest_extensions 未被 pytest 自动发现，需显式导入）


# ============================================================
# evolution_continuous_optimize 测试
# ============================================================


class TestEvolutionContinuousOptimize:
    async def test_skips_when_no_enterprises(self, v3_tables):
        """无活跃企业时安全跳过。"""
        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=[]),
        ):
            await evolution_continuous_optimize()
        # 无异常即通过

    async def test_processes_each_enterprise(self, v3_tables):
        """遍历所有活跃企业执行持续优化分析。"""
        enterprises = [("ent-001", "企业A"), ("ent-002", "企业B")]
        mock_report = {"optimization_items": [], "analyzed_agents": 0}

        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=enterprises),
        ), patch(
            "app.services.evolution.continuous_optimizer.continuous_optimizer.run_enterprise_optimization_background",
            new=AsyncMock(return_value=mock_report),
        ) as mock_run:
            await evolution_continuous_optimize()

        assert mock_run.call_count == 2

    async def test_single_failure_does_not_block_others(self, v3_tables):
        """单个企业失败不影响其他企业。"""
        enterprises = [("ent-001", "企业A"), ("ent-002", "企业B")]
        mock_report = {"optimization_items": [], "analyzed_agents": 0}

        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=enterprises),
        ), patch(
            "app.services.evolution.continuous_optimizer.continuous_optimizer.run_enterprise_optimization_background",
            new=AsyncMock(
                side_effect=[RuntimeError("企业A失败"), mock_report]
            ),
        ) as mock_run:
            await evolution_continuous_optimize()

        # 两个企业都被调用（第一个失败不阻断第二个）
        assert mock_run.call_count == 2

    async def test_handles_list_failure_gracefully(self, v3_tables):
        """企业列表获取失败时安全返回。"""
        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(side_effect=RuntimeError("DB 不可用")),
        ):
            await evolution_continuous_optimize()
        # 无异常即通过


# ============================================================
# evolution_org_metrics_snapshot 测试
# ============================================================


class TestEvolutionOrgMetricsSnapshot:
    async def test_skips_when_no_enterprises(self, v3_tables):
        """无活跃企业时安全跳过。"""
        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=[]),
        ):
            await evolution_org_metrics_snapshot()

    async def test_generates_metrics_for_each_enterprise(self, v3_tables):
        """为每个企业生成指标快照。"""
        enterprises = [("ent-001", "企业A")]
        mock_metrics = {
            "agent_workload": {"total_agents": 1},
            "maturity_level": "L2",
        }

        # mock async_session_factory 返回上下文管理器
        mock_db = AsyncMock()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_db
        mock_session_cm.__aexit__.return_value = None

        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=enterprises),
        ), patch(
            "app.services.scheduler_service.async_session_factory",
            return_value=mock_session_cm,
        ), patch(
            "app.services.evolution.org_analytics.org_analytics.get_metrics",
            new=AsyncMock(return_value=mock_metrics),
        ) as mock_get:
            await evolution_org_metrics_snapshot()

        assert mock_get.call_count == 1

    async def test_single_failure_does_not_block_others(self, v3_tables):
        """单个企业失败不影响其他企业。"""
        enterprises = [("ent-001", "企业A"), ("ent-002", "企业B")]

        # 第一个企业的 session 抛异常，第二个正常
        mock_good_db = AsyncMock()
        mock_good_cm = AsyncMock()
        mock_good_cm.__aenter__.return_value = mock_good_db
        mock_good_cm.__aexit__.return_value = None

        mock_bad_cm = AsyncMock()
        mock_bad_cm.__aenter__.side_effect = RuntimeError("连接失败")
        mock_bad_cm.__aexit__.return_value = None

        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=enterprises),
        ), patch(
            "app.services.scheduler_service.async_session_factory",
            side_effect=[mock_bad_cm, mock_good_cm],
        ), patch(
            "app.services.evolution.org_analytics.org_analytics.get_metrics",
            new=AsyncMock(return_value={"maturity_level": "L2"}),
        ) as mock_get:
            await evolution_org_metrics_snapshot()

        # 第二个企业仍被处理
        assert mock_get.call_count == 1


# ============================================================
# evolution_advisor_generate 测试
# ============================================================


class TestEvolutionAdvisorGenerate:
    async def test_skips_when_no_enterprises(self, v3_tables):
        """无活跃企业时安全跳过。"""
        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=[]),
        ):
            await evolution_advisor_generate()

    async def test_generates_suggestions_for_each_enterprise(self, v3_tables):
        """为每个企业生成 Advisor 建议。"""
        enterprises = [("ent-001", "企业A"), ("ent-002", "企业B")]
        mock_suggestions = [{"type": "knowledge", "title": "建议1"}]

        mock_db = AsyncMock()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_db
        mock_session_cm.__aexit__.return_value = None

        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=enterprises),
        ), patch(
            "app.services.scheduler_service.async_session_factory",
            return_value=mock_session_cm,
        ), patch(
            "app.services.evolution.advisor.advisor_service.generate_suggestions",
            new=AsyncMock(return_value=mock_suggestions),
        ) as mock_gen:
            await evolution_advisor_generate()

        assert mock_gen.call_count == 2

    async def test_single_failure_does_not_block_others(self, v3_tables):
        """单个企业失败不影响其他企业。"""
        enterprises = [("ent-001", "企业A"), ("ent-002", "企业B")]

        mock_good_db = AsyncMock()
        mock_good_cm = AsyncMock()
        mock_good_cm.__aenter__.return_value = mock_good_db
        mock_good_cm.__aexit__.return_value = None

        mock_bad_cm = AsyncMock()
        mock_bad_cm.__aenter__.side_effect = RuntimeError("连接失败")
        mock_bad_cm.__aexit__.return_value = None

        with patch(
            "app.services.scheduler_service._list_active_enterprises",
            new=AsyncMock(return_value=enterprises),
        ), patch(
            "app.services.scheduler_service.async_session_factory",
            side_effect=[mock_bad_cm, mock_good_cm],
        ), patch(
            "app.services.evolution.advisor.advisor_service.generate_suggestions",
            new=AsyncMock(return_value=[]),
        ) as mock_gen:
            await evolution_advisor_generate()

        # 第二个企业仍被处理
        assert mock_gen.call_count == 1


# ============================================================
# _register_evolution_jobs 测试
# ============================================================


class TestRegisterEvolutionJobs:
    def test_registers_three_jobs_when_enabled(self, v3_tables):
        """启用时注册三个进化层任务。"""
        from app.services import scheduler_service

        with patch.object(scheduler_service, "_EVOLUTION_SCHEDULER_ENABLED", True), \
             patch.object(scheduler_service.scheduler, "add_job") as mock_add:
            _register_evolution_jobs()

        assert mock_add.call_count == 3
        # id 是关键字参数（kwargs），不是位置参数
        registered_ids = {call.kwargs["id"] for call in mock_add.call_args_list}
        assert _EVOLUTION_OPTIMIZE_JOB_ID in registered_ids
        assert _EVOLUTION_METRICS_JOB_ID in registered_ids
        assert _EVOLUTION_ADVISOR_JOB_ID in registered_ids

    def test_skips_when_disabled(self, v3_tables):
        """禁用时跳过任务注册。"""
        from app.services import scheduler_service

        with patch.object(scheduler_service, "_EVOLUTION_SCHEDULER_ENABLED", False), \
             patch.object(scheduler_service.scheduler, "add_job") as mock_add:
            _register_evolution_jobs()

        assert mock_add.call_count == 0

    def test_registration_failure_is_non_fatal(self, v3_tables):
        """注册失败不抛异常（非致命）。"""
        from app.services import scheduler_service

        with patch.object(scheduler_service, "_EVOLUTION_SCHEDULER_ENABLED", True), \
             patch.object(scheduler_service.scheduler, "add_job",
                          side_effect=RuntimeError("调度器未启动")):
            # 不应抛异常
            _register_evolution_jobs()
