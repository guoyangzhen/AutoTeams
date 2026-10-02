"""生命周期管理器测试（PRD §5.6）。

覆盖 MVP 3 阶段：
- Recruit → Training → Production
- 阶段转换条件校验
- 非法转换拒绝
- 生命周期历史记录
- list_workforce 分页
"""
import pytest

from app.services.workforce.lifecycle_manager import LifecycleManager
from app.models.agent import Agent
from app.models.workforce import WorkforceLifecycle
from app.schemas.workforce import LifecycleStage, MVP_STAGES, MVP_TRANSITIONS


async def _create_test_agent(db_session, enterprise_id="ent-lc-1", **kwargs):
    """创建测试 Agent。"""
    agent = Agent(
        enterprise_id=enterprise_id,
        name="测试AI员工",
        description="测试用",
        system_prompt=kwargs.get("system_prompt", "你是测试AI员工"),
        status="ready",
        version="1.0.0",
        config={},
        lifecycle_stage=kwargs.get("lifecycle_stage", "recruit"),
        file_count=kwargs.get("file_count", 10),
        knowledge_count=kwargs.get("knowledge_count", 50),
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


class TestLifecycleStages:
    """MVP 阶段定义测试。"""

    def test_mvp_has_three_stages(self):
        """MVP 支持 3 阶段：Recruit / Training / Production。"""
        assert LifecycleStage.RECRUIT in MVP_STAGES
        assert LifecycleStage.TRAINING in MVP_STAGES
        assert LifecycleStage.PRODUCTION in MVP_STAGES
        assert len(MVP_STAGES) == 3

    def test_mvp_transitions(self):
        """MVP 阶段转换映射正确。"""
        assert LifecycleStage.TRAINING in MVP_TRANSITIONS[LifecycleStage.RECRUIT]
        assert LifecycleStage.PRODUCTION in MVP_TRANSITIONS[LifecycleStage.TRAINING]
        # Production 是终态
        assert MVP_TRANSITIONS[LifecycleStage.PRODUCTION] == set()


class TestLifecycleGet:
    """get_lifecycle 测试。"""

    async def test_get_lifecycle_recruit(self, db_session):
        """查询 recruit 阶段的 Agent。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit")
        mgr = LifecycleManager()
        result = await mgr.get_lifecycle(db_session, agent.id)

        assert result["stage"] == "recruit"
        assert "stage_entered_at" in result
        assert isinstance(result["history"], list)

    async def test_get_lifecycle_with_history(self, db_session):
        """查询有历史记录的 Agent。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="training")
        # 手动添加历史记录
        lc1 = WorkforceLifecycle(
            agent_id=agent.id, enterprise_id=agent.enterprise_id,
            stage="recruit", stage_entered_at=agent.created_at,
            transition_reason="创建",
        )
        db_session.add(lc1)
        await db_session.commit()

        mgr = LifecycleManager()
        result = await mgr.get_lifecycle(db_session, agent.id)

        assert result["stage"] == "training"
        assert len(result["history"]) >= 1

    async def test_get_lifecycle_agent_not_found(self, db_session):
        """Agent 不存在时抛出 ValueError。"""
        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="不存在"):
            await mgr.get_lifecycle(db_session, "nonexistent-agent-id")


class TestLifecycleTransition:
    """阶段转换测试。"""

    async def test_recruit_to_training(self, db_session):
        """Recruit → Training 转换成功。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit", system_prompt="你是AI员工")
        mgr = LifecycleManager()
        result = await mgr.transition(db_session, agent.id, "training", reason="配置已注入")

        assert result["new_stage"] == "training"
        assert result["status"] == "success"

    async def test_training_to_production(self, db_session):
        """Training → Production 转换成功（需知识库已注入）。"""
        agent = await _create_test_agent(
            db_session, lifecycle_stage="training",
            system_prompt="你是AI员工", file_count=10, knowledge_count=50,
        )
        mgr = LifecycleManager()
        result = await mgr.transition(db_session, agent.id, "production", reason="知识库已注入")

        assert result["new_stage"] == "production"
        assert result["status"] == "success"

    async def test_recruit_to_production_blocked(self, db_session):
        """Recruit → Production 直接转换被拒绝（MVP 不允许跳级）。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit")
        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="MVP 阶段不支持"):
            await mgr.transition(db_session, agent.id, "production")

    async def test_production_to_recruit_blocked(self, db_session):
        """Production → Recruit 回退被拒绝（MVP 中 Production 是终态）。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="production")
        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="MVP 阶段不支持"):
            await mgr.transition(db_session, agent.id, "recruit")

    async def test_same_stage_blocked(self, db_session):
        """同阶段转换被拒绝。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit")
        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="无需转换"):
            await mgr.transition(db_session, agent.id, "recruit")

    async def test_invalid_stage_blocked(self, db_session):
        """无效的阶段名称被拒绝。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit")
        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="无效的生命周期阶段"):
            await mgr.transition(db_session, agent.id, "invalid_stage")

    async def test_recruit_to_training_requires_system_prompt(self, db_session):
        """Recruit → Training 需要 system_prompt。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit", system_prompt=None)
        # 需要显式设置 system_prompt 为 None
        agent.system_prompt = None
        await db_session.commit()

        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="system_prompt"):
            await mgr.transition(db_session, agent.id, "training")

    async def test_training_to_production_requires_knowledge(self, db_session):
        """Training → Production 需要知识库已注入。"""
        agent = await _create_test_agent(
            db_session, lifecycle_stage="training",
            system_prompt="你是AI员工", file_count=0, knowledge_count=0,
        )
        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="知识库"):
            await mgr.transition(db_session, agent.id, "production")

    async def test_transition_records_history(self, db_session):
        """转换后写入生命周期历史记录。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit", system_prompt="你是AI员工")
        mgr = LifecycleManager()
        await mgr.transition(db_session, agent.id, "training", reason="测试记录")

        # 查询历史
        result = await mgr.get_lifecycle(db_session, agent.id)
        assert len(result["history"]) >= 1
        # 最新历史记录应为 training
        latest = result["history"][-1]
        assert latest["stage"] == "training"
        assert latest["transition_reason"] == "测试记录"

    async def test_transition_agent_not_found(self, db_session):
        """Agent 不存在时抛出 ValueError。"""
        mgr = LifecycleManager()
        with pytest.raises(ValueError, match="不存在"):
            await mgr.transition(db_session, "nonexistent", "training")

    async def test_invalid_stage_rejected(self, db_session):
        """非法阶段（不在 spec §10.6 的 7 个阶段中）被拒绝。"""
        agent = await _create_test_agent(db_session, lifecycle_stage="recruit", system_prompt="你是AI员工")
        mgr = LifecycleManager()
        # "paused" 不在 spec §10.6 定义的 7 个生命周期阶段中
        with pytest.raises(ValueError, match="无效"):
            await mgr.transition(db_session, agent.id, "paused", reason="连续失败")


class TestListWorkforce:
    """list_workforce 分页测试。"""

    async def test_list_workforce_pagination(self, db_session):
        """分页列出企业 Workforce。"""
        for i in range(5):
            await _create_test_agent(db_session, enterprise_id="ent-list-1", name=f"员工{i}")

        mgr = LifecycleManager()
        items, total = await mgr.list_workforce(db_session, "ent-list-1", limit=3, offset=0)
        assert total == 5
        assert len(items) == 3

    async def test_list_workforce_second_page(self, db_session):
        """第二页。"""
        for i in range(5):
            await _create_test_agent(db_session, enterprise_id="ent-list-2", name=f"员工{i}")

        mgr = LifecycleManager()
        items, total = await mgr.list_workforce(db_session, "ent-list-2", limit=3, offset=3)
        assert total == 5
        assert len(items) == 2

    async def test_list_workforce_empty(self, db_session):
        """空企业返回空列表。"""
        mgr = LifecycleManager()
        items, total = await mgr.list_workforce(db_session, "ent-empty", limit=20, offset=0)
        assert total == 0
        assert items == []

    async def test_list_workforce_enterprise_isolation(self, db_session):
        """企业隔离：只返回指定企业的 Agent。"""
        await _create_test_agent(db_session, enterprise_id="ent-iso-1", name="企业A员工")
        await _create_test_agent(db_session, enterprise_id="ent-iso-2", name="企业B员工")

        mgr = LifecycleManager()
        items_a, total_a = await mgr.list_workforce(db_session, "ent-iso-1", limit=20, offset=0)
        items_b, total_b = await mgr.list_workforce(db_session, "ent-iso-2", limit=20, offset=0)

        assert total_a == 1
        assert total_b == 1
        assert items_a[0].enterprise_id == "ent-iso-1"
        assert items_b[0].enterprise_id == "ent-iso-2"
