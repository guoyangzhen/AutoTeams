"""回滚机制测试（PRD §5.10 回滚）。

测试覆盖：
- create_snapshot：操作前创建状态快照
- rollback_operation：操作级回滚（恢复 Agent 状态）
- rollback_process：流程级回滚（多 Agent 快照批量回滚）
- list_snapshots：分页查询
- 验证回滚字段正确恢复
"""
import pytest

from app.services.collaboration.rollback import (
    RollbackManager,
    rollback_manager,
)
from app.models.collaboration import OperationSnapshot
from app.models.agent import Agent

# 共享 fixtures（conftest_extensions 未被 pytest 自动发现，需显式导入）


# ============================================================
# 测试辅助
# ============================================================


async def _seed_enterprise(db):
    from sqlalchemy import text
    import uuid
    from datetime import datetime, timezone

    eid = f"ent-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    await db.execute(
        text(
            "INSERT INTO enterprises (id, name, is_active, invite_max_uses, invite_used_count, created_at, updated_at) "
            "VALUES (:id, :name, 1, 10, 0, :now, :now)"
        ),
        {"id": eid, "name": "测试企业", "now": now},
    )
    await db.commit()
    return eid


async def _seed_agent(db, enterprise_id, name="测试 Agent", **kwargs):
    """创建 Agent 并设置可回滚字段。"""
    agent = Agent(
        enterprise_id=enterprise_id,
        name=name,
        description="测试用",
        system_prompt=kwargs.get("system_prompt", "你是测试 Agent"),
        status="ready",
        version="1.0.0",
        config=kwargs.get("config", {"temperature": 0.3}),
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


# ============================================================
# create_snapshot 测试
# ============================================================


class TestCreateSnapshot:
    async def test_create_snapshot_auto_captures_agent_state(
        self, db_session, v3_tables
    ):
        """自动捕获 Agent 状态创建快照。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(
            db_session, eid, system_prompt="原始 prompt", config={"k": "v"}
        )

        snap_id = await rollback_manager.create_snapshot(
            db_session, agent.id, "update_config"
        )

        assert snap_id is not None

        from sqlalchemy import select
        result = await db_session.execute(
            select(OperationSnapshot).where(OperationSnapshot.id == snap_id)
        )
        snap = result.scalar_one()
        assert snap.agent_id == agent.id
        assert snap.operation == "update_config"
        # 快照状态含 Agent 可回滚字段
        state = snap.snapshot.get("state", {})
        assert state.get("system_prompt") == "原始 prompt"
        assert state.get("config") == {"k": "v"}

    async def test_create_snapshot_with_explicit_state(
        self, db_session, v3_tables
    ):
        """使用显式 state 创建快照。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        explicit_state = {"system_prompt": "显式 prompt", "config": {"custom": True}}
        snap_id = await rollback_manager.create_snapshot(
            db_session, agent.id, "custom_op",
            state=explicit_state,
        )

        from sqlalchemy import select
        result = await db_session.execute(
            select(OperationSnapshot).where(OperationSnapshot.id == snap_id)
        )
        snap = result.scalar_one()
        assert snap.snapshot["state"] == explicit_state

    async def test_create_snapshot_with_process_id(self, db_session, v3_tables):
        """带 process_id 的快照：operation 存储为 "{process_id}:{operation}"。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        snap_id = await rollback_manager.create_snapshot(
            db_session, agent.id, "send_quotation",
            process_id="quotation-OPP-001",
        )

        from sqlalchemy import select
        result = await db_session.execute(
            select(OperationSnapshot).where(OperationSnapshot.id == snap_id)
        )
        snap = result.scalar_one()
        assert snap.operation == "quotation-OPP-001:send_quotation"
        assert snap.snapshot.get("_process_id") == "quotation-OPP-001"

    async def test_create_snapshot_invalid_agent_raises(
        self, db_session, v3_tables
    ):
        """不存在的 Agent ID 抛出 ValueError。"""
        await _seed_enterprise(db_session)
        with pytest.raises(ValueError, match="不存在"):
            await rollback_manager.create_snapshot(
                db_session, "invalid-agent-id", "test_op"
            )


# ============================================================
# rollback_operation 测试
# ============================================================


class TestRollbackOperation:
    async def test_rollback_restores_agent_state(self, db_session, v3_tables):
        """回滚后 Agent 状态恢复到快照。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(
            db_session, eid,
            system_prompt="原始 prompt",
            config={"temperature": 0.3},
        )

        # 创建快照
        snap_id = await rollback_manager.create_snapshot(
            db_session, agent.id, "update_config"
        )

        # 修改 Agent 状态
        agent.system_prompt = "修改后的 prompt"
        agent.config = {"temperature": 0.9, "max_tokens": 4000}
        await db_session.commit()

        # 验证修改已生效
        await db_session.refresh(agent)
        assert agent.system_prompt == "修改后的 prompt"

        # 回滚
        result = await rollback_manager.rollback_operation(
            db_session, snap_id
        )

        assert result["rolled_back"] is True
        assert result["agent_id"] == agent.id

        # 验证状态已恢复
        await db_session.refresh(agent)
        assert agent.system_prompt == "原始 prompt"
        assert agent.config == {"temperature": 0.3}

    async def test_rollback_invalid_snapshot_raises(self, db_session, v3_tables):
        """回滚不存在的快照 ID 抛出 ValueError。"""
        await _seed_enterprise(db_session)
        with pytest.raises(ValueError, match="不存在"):
            await rollback_manager.rollback_operation(
                db_session, "invalid-snap-id"
            )

    async def test_rollback_with_explicit_state(self, db_session, v3_tables):
        """使用显式 state 创建的快照也能回滚。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid, system_prompt="当前 prompt")

        explicit_state = {"system_prompt": "回滚目标 prompt", "config": {}}
        snap_id = await rollback_manager.create_snapshot(
            db_session, agent.id, "custom_op",
            state=explicit_state,
        )

        await rollback_manager.rollback_operation(db_session, snap_id)

        await db_session.refresh(agent)
        assert agent.system_prompt == "回滚目标 prompt"


# ============================================================
# rollback_process 测试（流程级回滚）
# ============================================================


class TestRollbackProcess:
    async def test_rollback_process_restores_all_agents(self, db_session, v3_tables):
        """流程级回滚：恢复该流程下所有 Agent 的快照。"""
        eid = await _seed_enterprise(db_session)
        agent1 = await _seed_agent(
            db_session, eid, name="销售 Agent", system_prompt="销售原始 prompt"
        )
        agent2 = await _seed_agent(
            db_session, eid, name="客服 Agent", system_prompt="客服原始 prompt"
        )

        # 为两个 Agent 在同一流程下创建快照
        await rollback_manager.create_snapshot(
            db_session, agent1.id, "step1", process_id="quotation-OPP-001"
        )
        await rollback_manager.create_snapshot(
            db_session, agent2.id, "step2", process_id="quotation-OPP-001"
        )

        # 修改两个 Agent
        agent1.system_prompt = "销售修改后"
        agent2.system_prompt = "客服修改后"
        await db_session.commit()

        # 流程级回滚
        result = await rollback_manager.rollback_process(
            db_session, eid, "quotation-OPP-001"
        )

        assert result["rolled_back"] is True
        assert result["rolled_back_count"] == 2
        assert set(result["agent_ids"]) == {agent1.id, agent2.id}

        # 验证恢复
        await db_session.refresh(agent1)
        await db_session.refresh(agent2)
        assert agent1.system_prompt == "销售原始 prompt"
        assert agent2.system_prompt == "客服原始 prompt"

    async def test_rollback_process_no_snapshots_returns_not_rolled(
        self, db_session, v3_tables
    ):
        """无快照的流程回滚返回 rolled_back=False。"""
        eid = await _seed_enterprise(db_session)

        result = await rollback_manager.rollback_process(
            db_session, eid, "nonexistent-process"
        )

        assert result["rolled_back"] is False
        assert result["rolled_back_count"] == 0

    async def test_rollback_process_isolates_by_enterprise(
        self, db_session, v3_tables
    ):
        """流程级回滚隔离企业：不回滚其他企业的快照。"""
        eid1 = await _seed_enterprise(db_session)
        # 创建第二个企业
        from sqlalchemy import text
        import uuid
        from datetime import datetime, timezone
        eid2 = f"ent-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        await db_session.execute(
            text(
                "INSERT INTO enterprises (id, name, is_active, invite_max_uses, invite_used_count, created_at, updated_at) "
                "VALUES (:id, :name, 1, 10, 0, :now, :now)"
            ),
            {"id": eid2, "name": "企业2", "now": now},
        )
        await db_session.commit()

        agent1 = await _seed_agent(db_session, eid1, system_prompt="企业1原始")
        agent2 = await _seed_agent(db_session, eid2, system_prompt="企业2原始")

        # 两个企业的 Agent 使用相同 process_id
        await rollback_manager.create_snapshot(
            db_session, agent1.id, "step1", process_id="proc-001"
        )
        await rollback_manager.create_snapshot(
            db_session, agent2.id, "step1", process_id="proc-001"
        )

        agent1.system_prompt = "企业1修改"
        agent2.system_prompt = "企业2修改"
        await db_session.commit()

        # 企业 1 触发流程级回滚
        result = await rollback_manager.rollback_process(
            db_session, eid1, "proc-001"
        )

        # 应只回滚企业 1 的快照
        assert result["rolled_back_count"] == 1
        assert result["agent_ids"] == [agent1.id]

        # 企业 2 的 Agent 不应被恢复
        await db_session.refresh(agent2)
        assert agent2.system_prompt == "企业2修改"


# ============================================================
# list_snapshots 测试
# ============================================================


class TestListSnapshots:
    async def test_list_snapshots_pagination(self, db_session, v3_tables):
        """快照分页查询。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        # 创建 3 个快照
        for i in range(3):
            await rollback_manager.create_snapshot(
                db_session, agent.id, f"op-{i}"
            )

        items, total = await rollback_manager.list_snapshots(
            db_session, agent.id, limit=2, offset=0
        )
        assert total == 3
        assert len(items) == 2

        items_p2, total = await rollback_manager.list_snapshots(
            db_session, agent.id, limit=2, offset=2
        )
        assert len(items_p2) == 1

    async def test_list_snapshots_empty(self, db_session, v3_tables):
        """无快照时返回空。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        items, total = await rollback_manager.list_snapshots(
            db_session, agent.id
        )
        assert total == 0
        assert items == []

    async def test_get_snapshot_returns_none_for_invalid_id(
        self, db_session, v3_tables
    ):
        """get_snapshot 对无效 ID 返回 None。"""
        await _seed_enterprise(db_session)
        result = await rollback_manager.get_snapshot(db_session, "invalid-id")
        assert result is None
