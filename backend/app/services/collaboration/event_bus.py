"""WT4 事件驱动协作总线（PRD §5.8 + 重构方案 §7.6 阶段2）。

事件类型（7 步演示案例，PRD §8.5，P2 字段对齐后与前端 CollaborationEventType 统一）：
    inquiry_received → product_query → quotation_generated → approval_submitted
    → approval_approved → order_synced → after_sales

设计：
- publish：持久化事件 + 派发到进程内订阅者（同步 await，便于测试与编排）
- subscribe：注册事件处理器（MVP 进程内，P1 可扩展为跨进程消息队列）
- run_demo_case：完整跑通 7 步业务链路（询盘→报价→审批→成交→售后），
  在 approval 步骤创建审批门（人机协作 MVP 模式 1：人类审批介入）。
- list_events：分页查询事件历史

工程约束（spec §2.2）：
- service 层写操作显式 await db.commit()
- 后台/编排任务用独立 session 时由调用方负责（本 service 接收外部 db）
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collaboration import CollaborationEvent
from app.services.collaboration.human_ai_collaboration import (
    HumanAICollaboration,
    human_ai_collaboration,
)
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


# 7 步演示案例事件类型（P2 字段对齐: 与前端 CollaborationEventType 统一）
EVENT_INQUIRY_RECEIVED = "inquiry_received"
EVENT_PRODUCT_QUERY = "product_query"
EVENT_QUOTATION_GENERATED = "quotation_generated"
EVENT_APPROVAL_SUBMITTED = "approval_submitted"
EVENT_APPROVAL_APPROVED = "approval_approved"
EVENT_ORDER_SYNCED = "order_synced"
EVENT_AFTER_SALES = "after_sales"
# 扩展事件类型（前端 CollaborationEventType 预留）
EVENT_HANDOFF = "handoff"
EVENT_ESCALATION = "escalation"
EVENT_ERROR = "error"

# 向后兼容别名（值已对齐前端，旧常量名保留供过渡引用）
EVENT_NEW_INQUIRY = EVENT_INQUIRY_RECEIVED
EVENT_QUOTATION = EVENT_QUOTATION_GENERATED
EVENT_FINANCIAL_REVIEW = EVENT_APPROVAL_SUBMITTED
EVENT_APPROVAL = EVENT_APPROVAL_APPROVED
EVENT_CUSTOMER_SYNC = EVENT_ORDER_SYNCED

DEMO_CASE_STEPS = (
    EVENT_INQUIRY_RECEIVED,
    EVENT_PRODUCT_QUERY,
    EVENT_QUOTATION_GENERATED,
    EVENT_APPROVAL_SUBMITTED,
    EVENT_APPROVAL_APPROVED,
    EVENT_ORDER_SYNCED,
    EVENT_AFTER_SALES,
)

# 事件处理器类型：async (event: CollaborationEvent) -> None
EventHandler = Callable[[CollaborationEvent], Awaitable[None]]


class EventBus:
    """事件驱动协作总线。

    Usage::

        bus = EventBus()
        bus.subscribe(EVENT_NEW_INQUIRY, handle_inquiry)
        event_id = await bus.publish(db, enterprise_id, EVENT_NEW_INQUIRY, {...})
        items, total = await bus.list_events(db, enterprise_id, limit=20, offset=0)
    """

    def __init__(self, collaboration_svc: Optional[HumanAICollaboration] = None) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._collaboration_svc = collaboration_svc or human_ai_collaboration

    # ========================================================
    # subscribe / unsubscribe
    # ========================================================

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """订阅事件类型。同一类型可多个处理器，按注册顺序执行。"""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """取消订阅（测试用，便于隔离）。"""
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def clear_subscribers(self) -> None:
        """清空所有订阅（测试隔离用）。"""
        self._handlers.clear()

    # ========================================================
    # publish
    # ========================================================

    async def publish(
        self,
        db: AsyncSession,
        enterprise_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        source_agent_id: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> str:
        """发布事件：持久化 + 派发到订阅者。

        订阅者执行失败不阻断事件持久化与其他订阅者（错误计数 + 日志）。
        Returns: event_id
        """
        event = CollaborationEvent(
            id=str(uuid.uuid4()),
            enterprise_id=enterprise_id,
            event_type=event_type,
            payload=payload or {},
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            status="pending",
            created_at=created_at,
        )
        db.add(event)
        await db.flush()  # 拿到 created_at

        # 派发到订阅者（best-effort，单点失败不影响其他）
        event.status = "processed"
        handlers = list(self._handlers.get(event_type, []))
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                errors_total.labels(
                    module=__name__, exception_type=type(e).__name__
                ).inc()
                logger.error(
                    "事件处理器执行失败: event=%s type=%s error=%s",
                    event.id, event_type, e, exc_info=True,
                )
                event.status = "failed"

        await db.commit()
        logger.info(
            "事件已发布: enterprise=%s type=%s event=%s status=%s",
            enterprise_id, event_type, event.id, event.status,
        )
        return event.id

    # ========================================================
    # list_events
    # ========================================================

    async def list_events(
        self,
        db: AsyncSession,
        enterprise_id: str,
        event_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[CollaborationEvent], int]:
        """分页查询事件历史。"""
        conditions = [CollaborationEvent.enterprise_id == enterprise_id]
        if event_type:
            conditions.append(CollaborationEvent.event_type == event_type)

        count_result = await db.execute(
            select(func.count(CollaborationEvent.id)).where(*conditions)
        )
        total = int(count_result.scalar() or 0)

        result = await db.execute(
            select(CollaborationEvent)
            .where(*conditions)
            .order_by(CollaborationEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(result.scalars().all())
        return items, total

    # ========================================================
    # 7 步演示案例
    # ========================================================

    async def run_demo_case(
        self,
        db: AsyncSession,
        enterprise_id: str,
        agent_ids: dict[str, str],
        inquiry_payload: Optional[dict[str, Any]] = None,
        approval_mode: str = "human_approval_gate",
    ) -> dict[str, Any]:
        """跑通 7 步演示案例（询盘→报价→审批→成交→售后）。

        Args:
            enterprise_id: 企业 ID
            agent_ids: 角色到 Agent ID 的映射，需包含：
                sales / product_expert / finance / customer_service / after_sales
            inquiry_payload: 初始询盘负载（默认示例：华智制造询盘 SL-T100）
            approval_mode: 审批模式（human_approval_gate / advise_confirm）

        Returns:
            {"events": [event_id,...], "approval_gate_id": str | None, "completed": bool}

        7 步映射（PRD §8.5，P2 对齐后的事件类型）：
            1. inquiry_received   → sales Agent
            2. product_query      → product_expert Agent
            3. quotation_generated → sales Agent
            4. approval_submitted → finance Agent
            5. approval_approved  → 创建审批门（人类审批介入 / AI 提议确认）
            6. order_synced       → customer_service Agent
            7. after_sales        → after_sales Agent
        """
        sales = agent_ids.get("sales")
        product_expert = agent_ids.get("product_expert")
        finance = agent_ids.get("finance")
        customer_service = agent_ids.get("customer_service")
        after_sales = agent_ids.get("after_sales")

        if inquiry_payload is None:
            inquiry_payload = {
                "customer": "华智制造",
                "opportunity_id": "OPP-001",
                "product": "SL-T100 温湿度传感器",
                "quantity": 200,
                "status": "报价中",
            }

        event_ids: list[str] = []
        approval_gate_id: Optional[str] = None

        # 为 7 步业务链路生成差异化的时间戳（真实业务流程跨越一个工作时段，
        # 而非全部挤在同一秒）。step 1 最早、step 7 最新，每步间隔约 40 分钟，
        # 从约 4 小时前逐步推进到当前，保证「实时运转」展示的是先后衔接的事件。
        _now = datetime.now(timezone.utc)
        _step_times = [_now - timedelta(minutes=(7 - k) * 40) for k in range(1, 8)]

        # 1. 收到新询盘
        event_ids.append(
            await self.publish(
                db, enterprise_id, EVENT_NEW_INQUIRY,
                payload={**inquiry_payload, "step": 1, "action": "提取客户需求"},
                source_agent_id=sales,
                created_at=_step_times[0],
            )
        )
        # 2. 产品参数查询
        event_ids.append(
            await self.publish(
                db, enterprise_id, EVENT_PRODUCT_QUERY,
                payload={**inquiry_payload, "step": 2, "action": "补充产品参数",
                         "query": "SL-T100 测温范围"},
                source_agent_id=sales, target_agent_id=product_expert,
                created_at=_step_times[1],
            )
        )
        # 3. 销售报价
        event_ids.append(
            await self.publish(
                db, enterprise_id, EVENT_QUOTATION,
                payload={**inquiry_payload, "step": 3, "action": "生成报价单",
                         "amount": 180000, "currency": "CNY"},
                source_agent_id=sales,
                created_at=_step_times[2],
            )
        )
        # 4. 财务审核
        event_ids.append(
            await self.publish(
                db, enterprise_id, EVENT_FINANCIAL_REVIEW,
                payload={**inquiry_payload, "step": 4, "action": "报价合规审核",
                         "amount": 180000, "approval_tier": "总监（5-20万）"},
                source_agent_id=finance,
                created_at=_step_times[3],
            )
        )
        # 5. 总监审批（人机协作：创建审批门）
        gate = await self._collaboration_svc.create_approval_gate(
            db,
            enterprise_id=enterprise_id,
            process_id=f"quotation-{inquiry_payload.get('opportunity_id', 'OPP-001')}",
            node_id="director_approval",
            agent_id=sales,
            mode=approval_mode,
            payload={"amount": 180000, "opportunity_id": inquiry_payload.get("opportunity_id", "OPP-001")},
        )
        approval_gate_id = gate.get("gate_id")
        event_ids.append(
            await self.publish(
                db, enterprise_id, EVENT_APPROVAL,
                payload={**inquiry_payload, "step": 5, "action": "总监审批",
                         "approval_gate_id": approval_gate_id, "mode": approval_mode},
                source_agent_id=sales,
                created_at=_step_times[4],
            )
        )
        # 6. 客服同步
        event_ids.append(
            await self.publish(
                db, enterprise_id, EVENT_CUSTOMER_SYNC,
                payload={**inquiry_payload, "step": 6, "action": "同步订单信息",
                         "order_status": "已成交"},
                source_agent_id=customer_service,
                created_at=_step_times[5],
            )
        )
        # 7. 售后接管
        event_ids.append(
            await self.publish(
                db, enterprise_id, EVENT_AFTER_SALES,
                payload={**inquiry_payload, "step": 7, "action": "售后跟进",
                         "feedback": "客户已收到交付"},
                source_agent_id=after_sales,
                created_at=_step_times[6],
            )
        )

        logger.info(
            "7 步演示案例已完成: enterprise=%s events=%d approval_gate=%s",
            enterprise_id, len(event_ids), approval_gate_id,
        )
        return {
            "events": event_ids,
            "approval_gate_id": approval_gate_id,
            "completed": True,
            "steps": len(event_ids),
        }


# 模块级单例（无状态，可安全共享）
event_bus = EventBus()
