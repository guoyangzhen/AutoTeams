"""业务效果指标 API。

GET /metrics/business：聚合效率/成本/覆盖/质量/趋势指标，供业务效果仪表盘使用。
- 指定 agent_id 时校验企业隔离（与 loop 端点一致）；
- 未指定 agent_id 时聚合当前用户企业下全部智能体（Home 概览场景）。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.user import User
from app.services.metrics_service import metrics_service
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.metrics import errors_total
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["Business Metrics"])


async def _verify_agent_access(
    db: AsyncSession, agent_id: str, current_user: User
) -> Agent:
    """校验 Agent 存在且归属当前用户企业（与 loop._verify_agent_access 一致）。

    普通企业用户：必须匹配 enterprise_id；
    超级管理员（enterprise_id=None）：放行。
    """
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    query = select(Agent).where(Agent.id == agent_id)
    if current_user.enterprise_id is not None:
        query = query.where(Agent.enterprise_id == current_user.enterprise_id)
    result = await db.execute(query)
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    return agent


@router.get("/business")
@rate_limit_api()
async def get_business_metrics(
    request: Request,
    agent_id: Optional[str] = Query(default=None, description="指定智能体 ID，留空则聚合当前企业全部智能体"),
    range_days: int = Query(default=30, ge=1, le=90, description="聚合时间窗口（天）"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """业务效果仪表盘：效率/成本/覆盖/质量/趋势指标聚合。"""
    try:
        if agent_id:
            await _verify_agent_access(db, agent_id, current_user)
        result = await metrics_service.get_business_metrics(
            db,
            agent_id=agent_id,
            enterprise_id=current_user.enterprise_id,
            range_days=range_days,
        )
        return success_response(result)
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"业务指标参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.METRICS_INVALID) from e
    except SQLAlchemyError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"业务指标数据库异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
    except (RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"业务指标聚合失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
