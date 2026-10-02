"""人机协作模式测试（PRD §5.9，MVP 2 种模式）。

测试覆盖：
- 模式 1：人类审批介入（human_approval_gate）
- 模式 2：AI 提议人类确认（advise_and_confirm）
- approve_gate / reject_gate
- P1 模式 human_takeover 抛出 NotImplementedError
"""
import pytest

from app.services.collaboration.human_ai_collaboration import (
    HumanAICollaboration,
    human_ai_collaboration,
    MODE_ADVISE_CONFIRM,
    MODE_HUMAN_APPROVAL_GATE,
    MODE_HUMAN_TAKEOVER,
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
)
from app.models.collaboration import ApprovalGate

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


async def _seed_agent(db, enterprise_id):
    from app.models.agent import Agent

    agent = Agent(
        enterprise_id=enterprise_id,
        name="测试 Agent",
        description="测试用",
        system_prompt="你是测试 Agent",
        status="ready",
        version="1.0.0",
        config={},
    )
    db.add(agent)
    await db.commit()
    return agent.id


# ============================================================
# 模式 1：人类审批介入
# ============================================================


class TestHumanApprovalGate:
    async def test_create_human_approval_gate(self, db_session, v3_tables):
        """创建审批门（模式 1）。"""
        eid = await _seed_enterprise(db_session)
        agent_id = await _seed_agent(db_session, eid)

        result = await human_ai_collaboration.human_approval_gate(
            db_session,
            enterprise_id=eid,
            process_id="quotation-OPP-001",
            node_id="director_approval",
            agent_id=agent_id,
            payload={"amount": 180000},
        )

        assert result["status"] == STATUS_PENDING
        assert result["mode"] == MODE_HUMAN_APPROVAL_GATE
        assert "gate_id" in result

        # 验证 DB 记录
        from sqlalchemy import select
        gate_result = await db_session.execute(
            select(ApprovalGate).where(ApprovalGate.id == result["gate_id"])
        )
        gate = gate_result.scalar_one()
        assert gate.process_id == "quotation-OPP-001"
        assert gate.node_id == "director_approval"
        assert gate.agent_id == agent_id
        assert gate.status == STATUS_PENDING

    async def test_human_approval_gate_without_agent(self, db_session, v3_tables):
        """无 Agent 的审批门（人工流程节点）。"""
        eid = await _seed_enterprise(db_session)

        result = await human_ai_collaboration.human_approval_gate(
            db_session,
            enterprise_id=eid,
            process_id="process-1",
            node_id="manual_review",
        )

        assert result["status"] == STATUS_PENDING
        from sqlalchemy import select
        gate_result = await db_session.execute(
            select(ApprovalGate).where(ApprovalGate.id == result["gate_id"])
        )
        gate = gate_result.scalar_one()
        assert gate.agent_id is None


# ============================================================
# 模式 2：AI 提议人类确认
# ============================================================


class TestAdviseAndConfirm:
    async def test_advise_and_confirm_creates_gate(self, db_session, v3_tables):
        """AI 提议人类确认：创建审批门（模式 2）。"""
        eid = await _seed_enterprise(db_session)
        agent_id = await _seed_agent(db_session, eid)

        result = await human_ai_collaboration.advise_and_confirm(
            db_session,
            enterprise_id=eid,
            agent_id=agent_id,
            proposal="建议向客户发送报价单 SL-T100 ×200 = 180000 CNY",
            proposal_data={"amount": 180000, "product": "SL-T100"},
        )

        assert result["status"] == STATUS_PENDING
        assert result["mode"] == MODE_ADVISE_CONFIRM

        from sqlalchemy import select
        gate_result = await db_session.execute(
            select(ApprovalGate).where(ApprovalGate.id == result["gate_id"])
        )
        gate = gate_result.scalar_one()
        assert gate.agent_id == agent_id
        assert gate.process_id.startswith(f"advise-{agent_id}")


# ============================================================
# P1 模式：人类接管（预留接口）
# ============================================================


class TestHumanTakeover:
    async def test_human_takeover_raises_not_implemented(
        self, db_session, v3_tables
    ):
        """P1 human_takeover 抛出 NotImplementedError。"""
        eid = await _seed_enterprise(db_session)
        agent_id = await _seed_agent(db_session, eid)

        with pytest.raises(NotImplementedError):
            await human_ai_collaboration.human_takeover(
                db_session,
                enterprise_id=eid,
                agent_id=agent_id,
                task_id="task-1",
            )


# ============================================================
# approve_gate / reject_gate 测试
# ============================================================


class TestApproveGate:
    async def test_approve_pending_gate(self, db_session, v3_tables):
        """审批通过 pending → approved。"""
        eid = await _seed_enterprise(db_session)
        approver_id = "user-001"

        gate = await human_ai_collaboration.human_approval_gate(
            db_session, eid, "process-1", "node-1"
        )

        result = await human_ai_collaboration.approve_gate(
            db_session, gate["gate_id"], approver_id, comment="同意"
        )

        assert result["approved"] is True
        assert result["process_resumed"] is True

        from sqlalchemy import select
        gate_result = await db_session.execute(
            select(ApprovalGate).where(ApprovalGate.id == gate["gate_id"])
        )
        updated = gate_result.scalar_one()
        assert updated.status == STATUS_APPROVED
        assert updated.approver_id == approver_id
        assert updated.decided_at is not None

    async def test_approve_nonexistent_gate_raises(self, db_session, v3_tables):
        """审批不存在的 gate_id 抛出 ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await human_ai_collaboration.approve_gate(
                db_session, "invalid-id", "user-1"
            )

    async def test_approve_already_processed_gate_raises(
        self, db_session, v3_tables
    ):
        """重复审批已处理的 gate 抛出 ValueError。"""
        eid = await _seed_enterprise(db_session)
        gate = await human_ai_collaboration.human_approval_gate(
            db_session, eid, "process-1", "node-1"
        )
        await human_ai_collaboration.approve_gate(
            db_session, gate["gate_id"], "user-1"
        )

        with pytest.raises(ValueError, match="已处理"):
            await human_ai_collaboration.approve_gate(
                db_session, gate["gate_id"], "user-2"
            )


class TestRejectGate:
    async def test_reject_pending_gate(self, db_session, v3_tables):
        """审批拒绝 pending → rejected。"""
        eid = await _seed_enterprise(db_session)

        gate = await human_ai_collaboration.human_approval_gate(
            db_session, eid, "process-1", "node-1"
        )

        result = await human_ai_collaboration.reject_gate(
            db_session, gate["gate_id"], "user-1", reason="金额超标"
        )

        assert result["rejected"] is True

        from sqlalchemy import select
        gate_result = await db_session.execute(
            select(ApprovalGate).where(ApprovalGate.id == gate["gate_id"])
        )
        updated = gate_result.scalar_one()
        assert updated.status == STATUS_REJECTED
        assert updated.approver_id == "user-1"

    async def test_reject_nonexistent_gate_raises(self, db_session, v3_tables):
        """拒绝不存在的 gate_id 抛出 ValueError。"""
        with pytest.raises(ValueError, match="不存在"):
            await human_ai_collaboration.reject_gate(
                db_session, "invalid-id", "user-1", reason="测试"
            )


# ============================================================
# list_gates 测试
# ============================================================


class TestListGates:
    async def test_list_gates_pagination(self, db_session, v3_tables):
        """审批门分页查询。"""
        eid = await _seed_enterprise(db_session)

        # 创建 3 个审批门
        for i in range(3):
            await human_ai_collaboration.human_approval_gate(
                db_session, eid, f"process-{i}", f"node-{i}"
            )

        items, total = await human_ai_collaboration.list_gates(
            db_session, eid, limit=2, offset=0
        )
        assert total == 3
        assert len(items) == 2

        items_p2, total = await human_ai_collaboration.list_gates(
            db_session, eid, limit=2, offset=2
        )
        assert len(items_p2) == 1

    async def test_list_gates_filter_by_status(self, db_session, v3_tables):
        """按状态过滤审批门。"""
        eid = await _seed_enterprise(db_session)

        gate1 = await human_ai_collaboration.human_approval_gate(
            db_session, eid, "process-1", "node-1"
        )
        gate2 = await human_ai_collaboration.human_approval_gate(
            db_session, eid, "process-2", "node-2"
        )

        # 通过 gate1
        await human_ai_collaboration.approve_gate(
            db_session, gate1["gate_id"], "user-1"
        )

        pending_items, pending_total = await human_ai_collaboration.list_gates(
            db_session, eid, status="pending"
        )
        approved_items, approved_total = await human_ai_collaboration.list_gates(
            db_session, eid, status="approved"
        )

        assert pending_total == 1
        assert approved_total == 1
        assert pending_items[0].id == gate2["gate_id"]
        assert approved_items[0].id == gate1["gate_id"]
