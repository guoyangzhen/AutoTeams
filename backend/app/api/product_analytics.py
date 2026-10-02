"""经营行动简报与首次导览的隐私优先产品分析 API。"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.product_event import ProductEvent
from app.models.user import User
from app.schemas.product_event import (
    ProductEventAccepted,
    ProductEventRequest,
    ProductEventSummary,
)
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

router = APIRouter(prefix="/product-analytics", tags=["Product Analytics"])


def _enterprise_id_or_403(current_user: User) -> str:
    """产品事件必须绑定企业，避免全局管理员无归属写入不可隔离的数据。"""
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    return str(current_user.enterprise_id)


def _require_enterprise_admin(current_user: User) -> str:
    enterprise_id = _enterprise_id_or_403(current_user)
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    return enterprise_id


@router.post("/events", response_model=None)
@rate_limit_api()
async def record_product_event(
    request: Request,
    payload: ProductEventRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """记录低风险产品交互事件。

    安全关键操作仍使用审计链；本端点只处理预定义的体验漏斗事件，
    不接受自由文本、业务资源 ID、文件路径或用户输入内容。
    """
    enterprise_id = _enterprise_id_or_403(current_user)
    event = ProductEvent(
        enterprise_id=enterprise_id,
        user_id=str(current_user.id),
        session_id=payload.session_id,
        event_name=payload.event_name,
        surface=payload.surface,
        journey_state=payload.journey_state,
        action=payload.action,
        properties={
            # 只保留可用于无障碍/体验质量切分的布尔值与小范围步骤号。
            "tour_step": payload.tour_step,
            "reduced_motion": payload.reduced_motion,
        },
    )
    db.add(event)
    await db.commit()
    return success_response(data=ProductEventAccepted().model_dump())


@router.get("/company-action-summary", response_model=None)
@rate_limit_api()
async def get_company_action_summary(
    request: Request,
    days: int = Query(default=30, ge=1, le=90, description="聚合窗口（天）"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回企业管理员可见的行动简报/导览聚合漏斗，不返回用户级事件明细。"""
    enterprise_id = _require_enterprise_admin(current_user)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base_filters = (
        ProductEvent.enterprise_id == enterprise_id,
        ProductEvent.created_at >= cutoff,
        ProductEvent.surface == "company_overview",
    )

    total_events = (
        await db.execute(select(func.count(ProductEvent.id)).where(*base_filters))
    ).scalar_one()
    unique_sessions = (
        await db.execute(
            select(func.count(func.distinct(ProductEvent.session_id))).where(*base_filters)
        )
    ).scalar_one()

    event_rows = (
        await db.execute(
            select(ProductEvent.event_name, func.count(ProductEvent.id))
            .where(*base_filters)
            .group_by(ProductEvent.event_name)
        )
    ).all()
    event_counts = {name: count for name, count in event_rows}

    action_rows = (
        await db.execute(
            select(ProductEvent.action, func.count(ProductEvent.id))
            .where(*base_filters, ProductEvent.event_name == "company_brief_action_clicked")
            .group_by(ProductEvent.action)
        )
    ).all()
    action_clicks = {action: count for action, count in action_rows if action}

    journey_rows = (
        await db.execute(
            select(ProductEvent.journey_state, func.count(ProductEvent.id))
            .where(*base_filters, ProductEvent.journey_state.is_not(None))
            .group_by(ProductEvent.journey_state)
        )
    ).all()
    journey_states = {state: count for state, count in journey_rows if state}

    summary = ProductEventSummary(
        window_days=days,
        total_events=total_events,
        unique_sessions=unique_sessions,
        brief_views=event_counts.get("company_brief_viewed", 0),
        brief_action_clicks=event_counts.get("company_brief_action_clicked", 0),
        tour_started=event_counts.get("company_tour_started", 0),
        tour_completed=event_counts.get("company_tour_completed", 0),
        tour_skipped=event_counts.get("company_tour_skipped", 0),
        action_clicks=action_clicks,
        journey_states=journey_states,
        generated_at=datetime.now(timezone.utc),
    )
    return success_response(data=summary.model_dump(mode="json"))
