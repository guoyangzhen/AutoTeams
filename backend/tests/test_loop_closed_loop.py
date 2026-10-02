"""O-07/O-08: Loop 闭环测试。

覆盖：
1. loop_engine.apply_optimization —— 创建版本快照 / 应用 feedback 更新 prompt / 标记 applied
2. scheduler_service.start_scheduler —— 注册两个 cron 任务（mock scheduler）
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from sqlalchemy import select

from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.optimization_history import OptimizationHistory
from app.services.loop_engine import loop_engine


async def _create_agent(db_session, system_prompt: str = "你是助手", config: dict | None = None):
    """创建测试用 Agent + 所属企业 + 用户，返回 (agent, user)。"""
    enterprise = Enterprise(name="闭环测试企业")
    db_session.add(enterprise)
    await db_session.flush()
    await db_session.refresh(enterprise)

    user = User(
        email="closed-loop@test.com",
        password_hash="hash",
        name="闭环测试用户",
        enterprise_id=enterprise.id,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    agent = Agent(
        enterprise_id=enterprise.id,
        name="闭环测试助手",
        description="",
        system_prompt=system_prompt,
        config=config or {},
        version="1.0.0",
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)
    return agent, user


async def _create_optimization(db_session, agent, opt_type="feedback", output_data=None):
    history = OptimizationHistory(
        agent_id=agent.id,
        type=opt_type,
        input_data={},
        output_data=output_data or {},
        applied=False,
    )
    db_session.add(history)
    await db_session.flush()
    await db_session.refresh(history)
    return history


class TestApplyOptimization:
    """apply_optimization 服务层测试。"""

    @pytest.mark.asyncio
    async def test_apply_optimization_creates_snapshot(self, db_session):
        """应用优化应创建 Agent 版本快照（可回滚）并递增版本号。"""
        agent, user = await _create_agent(db_session, config={"top_k": 5, "n_results": 5})
        history = await _create_optimization(
            db_session, agent,
            opt_type="optimization",
            output_data={"keep_results": [0, 1, 2], "refined_query": "优化后查询", "issue": "相关性差"},
        )

        result = await loop_engine.apply_optimization(
            db_session, agent.id, history.id, user_id=user.id
        )

        assert result["applied"] is True
        assert result["version_snapshot_id"]

        # 应存在一条 is_active=True 的版本快照
        ver_rows = (
            await db_session.execute(
                select(AgentVersion).where(AgentVersion.agent_id == agent.id)
            )
        ).scalars().all()
        assert len(ver_rows) >= 1
        assert any(v.is_active for v in ver_rows)
        # 版本号应递增（1.0.0 → 1.0.1）
        await db_session.refresh(agent)
        assert agent.version == "1.0.1"

    @pytest.mark.asyncio
    async def test_apply_feedback_updates_prompt(self, db_session):
        """应用 feedback 类型应把不满意原因作为注意事项追加到 system_prompt。"""
        agent, user = await _create_agent(db_session, system_prompt="你是助手")
        history = await _create_optimization(
            db_session, agent,
            opt_type="feedback",
            output_data={
                "satisfaction_rate": 0.4,
                "issues": [
                    {"reason": "检索结果不准确", "improvement": "优化关键词", "priority": "high"},
                    {"reason": "回答不完整", "improvement": "补充上下文", "priority": "medium"},
                ],
            },
        )

        await loop_engine.apply_optimization(
            db_session, agent.id, history.id, user_id=user.id
        )

        await db_session.refresh(agent)
        assert "【Loop 反馈优化注意事项】" in agent.system_prompt
        assert "检索结果不准确" in agent.system_prompt
        assert "优化关键词" in agent.system_prompt
        # 原始 prompt 保留
        assert agent.system_prompt.startswith("你是助手")

    @pytest.mark.asyncio
    async def test_apply_marks_applied(self, db_session):
        """应用后 OptimizationHistory.applied 应为 True 且 applied_at 已设置。"""
        agent, user = await _create_agent(db_session)
        history = await _create_optimization(
            db_session, agent,
            opt_type="gap",
            output_data={"gaps": ["缺少年假政策"], "suggestions": ["补充考勤文档"], "priority_questions": ["年假怎么算"]},
        )
        assert history.applied is False
        assert history.applied_at is None

        await loop_engine.apply_optimization(
            db_session, agent.id, history.id, user_id=user.id
        )

        await db_session.refresh(history)
        assert history.applied is True
        assert history.applied_at is not None
        # gap 类型应写回待补充文件清单建议
        assert "applied_suggestion" in (history.output_data or {})
        assert "recommended_files" in history.output_data["applied_suggestion"]

    @pytest.mark.asyncio
    async def test_apply_already_applied_raises(self, db_session):
        """已应用的记录再次应用应抛 ValueError。"""
        agent, user = await _create_agent(db_session)
        history = await _create_optimization(db_session, agent)
        await loop_engine.apply_optimization(db_session, agent.id, history.id, user_id=user.id)

        with pytest.raises(ValueError, match="optimization_already_applied"):
            await loop_engine.apply_optimization(db_session, agent.id, history.id, user_id=user.id)

    @pytest.mark.asyncio
    async def test_apply_not_found_raises(self, db_session):
        """不存在的优化记录应抛 ValueError。"""
        agent, _ = await _create_agent(db_session)
        with pytest.raises(ValueError, match="optimization_not_found"):
            await loop_engine.apply_optimization(db_session, agent.id, "nonexistent-opt-id")


class TestSchedulerService:
    """scheduler_service 启动/注册测试（mock 调度器，不启动真实 APScheduler）。"""

    def test_scheduler_jobs_registered(self, monkeypatch):
        """start_scheduler 应注册 5 个 cron 任务（2 个 loop_engine + 3 个 WT4 进化层）。"""
        from app.services import scheduler_service

        # 启用调度器（conftest 默认关闭）
        monkeypatch.setattr(scheduler_service.settings, "LOOP_SCHEDULER_ENABLED", True)

        mock_sched = MagicMock()
        mock_sched.running = False  # 避免命中「已在运行」分支
        monkeypatch.setattr(scheduler_service, "scheduler", mock_sched)

        scheduler_service.start_scheduler()

        # 应注册 5 个任务：2 个 loop_engine + 3 个 WT4 进化层
        assert mock_sched.add_job.call_count == 5
        job_ids = {call.kwargs.get("id") for call in mock_sched.add_job.call_args_list}
        # loop_engine 任务
        assert "auto_optimize_all_agents" in job_ids
        assert "auto_scan_knowledge_gaps" in job_ids
        # WT4 进化层任务
        assert "evolution_continuous_optimize" in job_ids
        assert "evolution_org_metrics_snapshot" in job_ids
        assert "evolution_advisor_generate" in job_ids
        # 应启动调度器
        mock_sched.start.assert_called_once()

    def test_start_scheduler_disabled_is_noop(self, monkeypatch):
        """LOOP_SCHEDULER_ENABLED=false 时不应启动调度器。"""
        from app.services import scheduler_service

        monkeypatch.setattr(scheduler_service.settings, "LOOP_SCHEDULER_ENABLED", False)
        mock_sched = MagicMock()
        mock_sched.running = False
        monkeypatch.setattr(scheduler_service, "scheduler", mock_sched)

        scheduler_service.start_scheduler()

        mock_sched.add_job.assert_not_called()
        mock_sched.start.assert_not_called()

    def test_shutdown_scheduler_when_not_running_is_noop(self, monkeypatch):
        """调度器未运行时 shutdown 应安全跳过。"""
        from app.services import scheduler_service

        mock_sched = MagicMock()
        mock_sched.running = False
        monkeypatch.setattr(scheduler_service, "scheduler", mock_sched)

        scheduler_service.shutdown_scheduler()  # 不应抛异常
        mock_sched.shutdown.assert_not_called()
