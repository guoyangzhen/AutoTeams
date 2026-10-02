"""模型 API 配置端点（enterprise 级，prefix=/llm-config，无尾斜杠）。

端点：
- GET  /llm-config/{enterprise_id}   读取配置（掩码视图，成员可读）
- PUT  /llm-config/{enterprise_id}   创建/更新配置（仅管理员）

安全约束：
- 鉴权：get_current_user
- 企业隔离：只能访问 current_user.enterprise_id 对应的配置
- 密钥：仅返回掩码，明文密钥绝不进入响应体
- 限流：rate_limit_api / rate_limit_admin
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enterprise import Enterprise
from app.models.user import User
from app.schemas.llm_config import LLMApiConfigUpdate
from app.services.llm_config import get_config, to_view, upsert_config
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-config", tags=["模型 API 配置"])


async def _ensure_enterprise_exists(
    db: AsyncSession, enterprise_id: str
) -> Enterprise:
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)
    return enterprise


def _verify_enterprise_access(current_user: User, enterprise_id: str) -> None:
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id:
        raise HTTPException(
            status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED
        )


def _verify_enterprise_admin(current_user: User, enterprise_id: str) -> None:
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id or current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


@router.get("/{enterprise_id}", response_model=None)
@rate_limit_api()
async def get_llm_config(
    enterprise_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)
    config = await get_config(db, enterprise_id)
    view = to_view(config)
    return success_response(data=view.model_dump(mode="json") if view else None)


@router.put("/{enterprise_id}", response_model=None)
@rate_limit_admin()
async def update_llm_config(
    enterprise_id: str,
    data: LLMApiConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)
    try:
        config = await upsert_config(db, enterprise_id, data)
    except (SQLAlchemyError, OSError) as e:
        logger.error("保存模型 API 配置失败: %s", type(e).__name__, exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
    view = to_view(config)
    return success_response(
        data=view.model_dump(mode="json"), message="模型 API 配置已保存"
    )
