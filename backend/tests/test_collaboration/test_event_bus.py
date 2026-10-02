"""事件驱动协作总线测试（PRD §5.8 + §8.5）。

测试覆盖：
- publish：事件持久化 + 订阅者派发
- list_events：分页 + 类型过滤
- subscribe/unsubscribe：订阅管理
- run_demo_case：7 步演示案例（询盘→报价→审批→成交→售后）
"""
import pytest
from unittest.mock import AsyncMock

from app.services.collaboration.event_bus import (
    EventBus,
    event_bus,
    EVENT_NEW_INQUIRY,
    EVENT_PRODUCT_QUERY,
    EVENT_QUOTATION,
    EVENT_FINANCIAL_REVIEW,
    EVENT_APPROVAL,
    EVENT_CUSTOMER_SYNC,
    EVENT_AFTER_SALES,
    DEMO_CASE_STEPS,
)
from app.models.collaboration import CollaborationEvent

# 共享 fixtures（conftest_extensions 未被 pytest 自动发现，需显式导入）


# ============================================================
# 测试辅助
# ============================================================


async def _seed_enterprise(db):
    """创建企业。"""
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


async def _seed_agents(db, enterprise_id, count=5):
    """创建 N 个 Agent。"""
    import uuid
    from app.models.agent import Agent

    agent_ids = []
    for i in range(count):
        agent = Agent(
            enterprise_id=enterprise_id,
            name=f"测试 Agent {i+1}",
            description="测试用",
            system_prompt="你是测试 Agent",
            status="ready",
            version="1.0.0",
            config={},
        )
        db.add(agent)
        await db.flush()
        agent_ids.append(agent.id)
    await db.commit()
    return agent_ids


# ============================================================
# publish 测试
# ============================================================


class TestPublishEvent:
    async def test_publish_persists_event(self, db_session, v3_tables):
        """发布事件：持久化到数据库。"""
        eid = await _seed_enterprise(db_session)
        bus = EventBus()

        event_id = await bus.publish(
            db_session, eid, EVENT_NEW_INQUIRY,
            payload={"customer": "华智制造", "product": "SL-T100"},
        )

        assert event_id is not None
        from sqlalchemy import select
        result = await db_session.execute(
            select(CollaborationEvent).where(CollaborationEvent.id == event_id)
        )
        event = result.scalar_one()
        assert event.event_type == EVENT_NEW_INQUIRY
        assert event.payload["customer"] == "华智制造"
        assert event.status == "processed"

    async def test_publish_dispatches_to_subscribers(self, db_session, v3_tables):
        """发布事件：派发到已注册的订阅者。"""
        eid = await _seed_enterprise(db_session)
        bus = EventBus()

        received_events = []

        async def handler(event):
            received_events.append(event)

        bus.subscribe(EVENT_NEW_INQUIRY, handler)
        await bus.publish(db_session, eid, EVENT_NEW_INQUIRY, payload={"test": True})

        assert len(received_events) == 1
        assert received_events[0].event_type == EVENT_NEW_INQUIRY

    async def test_publish_subscriber_failure_does_not_block_others(
        self, db_session, v3_tables
    ):
        """单个订阅者失败不影响其他订阅者与事件持久化。"""
        eid = await _seed_enterprise(db_session)
        bus = EventBus()

        second_called = []

        async def failing_handler(event):
            raise RuntimeError("订阅者失败")

        async def ok_handler(event):
            second_called.append(event)

        bus.subscribe(EVENT_QUOTATION, failing_handler)
        bus.subscribe(EVENT_QUOTATION, ok_handler)

        # 不应抛出异常
        event_id = await bus.publish(
            db_session, eid, EVENT_QUOTATION, payload={"amount": 10000}
        )

        assert event_id is not None
        assert len(second_called) == 1

    async def test_publish_with_agent_ids(self, db_session, v3_tables):
        """发布事件：source/target agent_id 正确记录。"""
        eid = await _seed_enterprise(db_session)
        agent_ids = await _seed_agents(db_session, eid, 2)
        bus = EventBus()

        event_id = await bus.publish(
            db_session, eid, EVENT_PRODUCT_QUERY,
            payload={"query": "产品参数"},
            source_agent_id=agent_ids[0],
            target_agent_id=agent_ids[1],
        )

        from sqlalchemy import select
        result = await db_session.execute(
            select(CollaborationEvent).where(CollaborationEvent.id == event_id)
        )
        event = result.scalar_one()
        assert event.source_agent_id == agent_ids[0]
        assert event.target_agent_id == agent_ids[1]


# ============================================================
# list_events 测试
# ============================================================


class TestListEvents:
    async def test_list_events_returns_paginated(self, db_session, v3_tables):
        """list_events 返回分页结果。"""
        eid = await _seed_enterprise(db_session)
        bus = EventBus()

        # 发布 5 个事件
        for i in range(5):
            await bus.publish(
                db_session, eid, EVENT_NEW_INQUIRY,
                payload={"index": i},
            )

        items, total = await bus.list_events(
            db_session, eid, limit=2, offset=0
        )
        assert total == 5
        assert len(items) == 2

        items_page2, total = await bus.list_events(
            db_session, eid, limit=2, offset=2
        )
        assert len(items_page2) == 2

    async def test_list_events_filter_by_type(self, db_session, v3_tables):
        """按 event_type 过滤。"""
        eid = await _seed_enterprise(db_session)
        bus = EventBus()

        await bus.publish(db_session, eid, EVENT_NEW_INQUIRY, payload={})
        await bus.publish(db_session, eid, EVENT_QUOTATION, payload={})
        await bus.publish(db_session, eid, EVENT_NEW_INQUIRY, payload={})

        items, total = await bus.list_events(
            db_session, eid, event_type=EVENT_NEW_INQUIRY
        )
        assert total == 2
        assert all(i.event_type == EVENT_NEW_INQUIRY for i in items)

    async def test_list_events_empty_when_no_data(self, db_session, v3_tables):
        """无事件时返回空。"""
        eid = await _seed_enterprise(db_session)
        bus = EventBus()

        items, total = await bus.list_events(db_session, eid)
        assert total == 0
        assert items == []


# ============================================================
# subscribe / unsubscribe 测试
# ============================================================


class TestSubscribe:
    def test_subscribe_and_unsubscribe(self):
        """订阅与取消订阅。"""
        bus = EventBus()

        async def handler(event):
            pass

        bus.subscribe(EVENT_NEW_INQUIRY, handler)
        assert EVENT_NEW_INQUIRY in bus._handlers
        assert len(bus._handlers[EVENT_NEW_INQUIRY]) == 1

        bus.unsubscribe(EVENT_NEW_INQUIRY, handler)
        assert len(bus._handlers[EVENT_NEW_INQUIRY]) == 0

    def test_subscribe_multiple_handlers(self):
        """同类型多处理器按注册顺序执行。"""
        bus = EventBus()

        async def h1(event):
            pass

        async def h2(event):
            pass

        bus.subscribe(EVENT_QUOTATION, h1)
        bus.subscribe(EVENT_QUOTATION, h2)
        assert len(bus._handlers[EVENT_QUOTATION]) == 2

    def test_clear_subscribers(self):
        """清空所有订阅者。"""
        bus = EventBus()

        async def handler(event):
            pass

        bus.subscribe(EVENT_NEW_INQUIRY, handler)
        bus.subscribe(EVENT_QUOTATION, handler)
        bus.clear_subscribers()
        assert len(bus._handlers) == 0


# ============================================================
# 7 步演示案例测试
# ============================================================


class TestRunDemoCase:
    async def test_demo_case_publishes_7_events(self, db_session, v3_tables):
        """7 步演示案例发布 7 个事件。"""
        eid = await _seed_enterprise(db_session)
        agent_ids = await _seed_agents(db_session, eid, 5)

        bus = EventBus()
        agent_map = {
            "sales": agent_ids[0],
            "product_expert": agent_ids[1],
            "finance": agent_ids[2],
            "customer_service": agent_ids[3],
            "after_sales": agent_ids[4],
        }

        result = await bus.run_demo_case(db_session, eid, agent_map)

        assert result["completed"] is True
        assert len(result["events"]) == 7
        assert result["approval_gate_id"] is not None
        assert result["steps"] == 7

    async def test_demo_case_events_in_correct_order(
        self, db_session, v3_tables
    ):
        """事件类型顺序与 PRD §8.5 一致：询盘→报价→审批→成交→售后。"""
        eid = await _seed_enterprise(db_session)
        agent_ids = await _seed_agents(db_session, eid, 5)
        bus = EventBus()
        agent_map = {
            "sales": agent_ids[0],
            "product_expert": agent_ids[1],
            "finance": agent_ids[2],
            "customer_service": agent_ids[3],
            "after_sales": agent_ids[4],
        }

        result = await bus.run_demo_case(db_session, eid, agent_map)
        event_ids = result["events"]

        # 读取所有事件，按创建时间排序
        from sqlalchemy import select
        events_result = await db_session.execute(
            select(CollaborationEvent)
            .where(CollaborationEvent.id.in_(event_ids))
            .order_by(CollaborationEvent.created_at.asc())
        )
        events = events_result.scalars().all()

        event_types = [e.event_type for e in events]
        assert event_types == list(DEMO_CASE_STEPS)

    async def test_demo_case_creates_approval_gate(self, db_session, v3_tables):
        """7 步演示案例在 approval 步骤创建审批门。"""
        eid = await _seed_enterprise(db_session)
        agent_ids = await _seed_agents(db_session, eid, 5)
        bus = EventBus()
        agent_map = {
            "sales": agent_ids[0],
            "product_expert": agent_ids[1],
            "finance": agent_ids[2],
            "customer_service": agent_ids[3],
            "after_sales": agent_ids[4],
        }

        result = await bus.run_demo_case(db_session, eid, agent_map)

        # 验证审批门存在
        from sqlalchemy import select
        from app.models.collaboration import ApprovalGate
        gate_result = await db_session.execute(
            select(ApprovalGate).where(ApprovalGate.id == result["approval_gate_id"])
        )
        gate = gate_result.scalar_one()
        assert gate.status == "pending"
        assert gate.enterprise_id == eid

    async def test_demo_case_with_custom_inquiry(self, db_session, v3_tables):
        """自定义询盘 payload。"""
        eid = await _seed_enterprise(db_session)
        agent_ids = await _seed_agents(db_session, eid, 5)
        bus = EventBus()
        agent_map = {
            "sales": agent_ids[0],
            "product_expert": agent_ids[1],
            "finance": agent_ids[2],
            "customer_service": agent_ids[3],
            "after_sales": agent_ids[4],
        }
        custom_payload = {
            "customer": "自定义客户",
            "opportunity_id": "OPP-CUSTOM",
            "product": "自定义产品",
            "quantity": 999,
            "status": "自定义状态",
        }

        result = await bus.run_demo_case(
            db_session, eid, agent_map, inquiry_payload=custom_payload
        )

        # 验证询盘事件含自定义客户
        from sqlalchemy import select
        first_event = await db_session.execute(
            select(CollaborationEvent)
            .where(CollaborationEvent.id == result["events"][0])
        )
        event = first_event.scalar_one()
        assert event.payload["customer"] == "自定义客户"
        assert event.payload["opportunity_id"] == "OPP-CUSTOM"


# ============================================================
# 7 步事件类型常量测试
# ============================================================


class TestDemoCaseSteps:
    def test_demo_case_steps_count(self):
        """7 步事件类型常量。"""
        assert len(DEMO_CASE_STEPS) == 7

    def test_demo_case_steps_order(self):
        """步骤顺序正确。"""
        assert DEMO_CASE_STEPS == (
            EVENT_NEW_INQUIRY,
            EVENT_PRODUCT_QUERY,
            EVENT_QUOTATION,
            EVENT_FINANCIAL_REVIEW,
            EVENT_APPROVAL,
            EVENT_CUSTOMER_SYNC,
            EVENT_AFTER_SALES,
        )
