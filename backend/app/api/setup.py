import httpx
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.setup_session import SetupSession as SetupSessionModel
from app.schemas.setup import (
    SetupStartRequest,
    SetupMessage,
    PlanConfirm,
)
from app.services.setup_dialogue import setup_dialogue_service
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.audit import log_audit
from app.utils.metrics import errors_total
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["设置向导"])


async def _verify_session_access(
    db: AsyncSession, session_id: str, current_user: User
) -> SetupSessionModel:
    """P0-01: 校验会话存在且归属当前用户。

    设置会话按用户维度隔离（包含用户敏感的需求对话），严格匹配 user_id。
    """
    result = await db.execute(
        select(SetupSessionModel).where(
            SetupSessionModel.id == session_id,
            SetupSessionModel.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=ErrorCode.SETUP_SESSION_NOT_FOUND)
    return session


@router.post("/start")
@rate_limit_api()
async def start_setup(
    request: Request,
    data: SetupStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P0-01: 强制使用当前用户的企业 ID，忽略请求体中的 enterprise_id
    if not current_user.enterprise_id:
        raise HTTPException(
            status_code=403,
            detail=ErrorCode.SETUP_ENTERPRISE_REQUIRED,
        )
    try:
        result = await setup_dialogue_service.start_session(
            folder_path=data.folder_path,
            enterprise_id=current_user.enterprise_id,
            user_id=current_user.id,
            db=db,
        )
        return success_response(result)
    except (SQLAlchemyError, httpx.HTTPError, RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"启动设置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.post("/{session_id}/message")
@rate_limit_api()
async def send_setup_message(
    request: Request,
    session_id: str,
    data: SetupMessage,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P0-01: 校验会话归属
    await _verify_session_access(db, session_id, current_user)
    try:
        result = await setup_dialogue_service.send_message(
            session_id=session_id,
            user_message=data.content,
            db=db,
        )
        return success_response({
            "session_id": str(result["session_id"]),
            "message": result["message"],
        })
    except ValueError as e:
        logger.warning(f"发送 setup 消息失败: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.SETUP_MESSAGE_INVALID) from e
    except (SQLAlchemyError, httpx.HTTPError, RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"发送消息失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.get("/{session_id}/plan")
@rate_limit_api()
async def get_setup_plan(
    session_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P0-01: 校验会话归属
    await _verify_session_access(db, session_id, current_user)
    try:
        plan = await setup_dialogue_service.get_plan(session_id, db)
        return success_response(plan)
    except ValueError as e:
        logger.warning(f"获取 setup 方案失败: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.SETUP_PLAN_NOT_FOUND) from e
    except (SQLAlchemyError, httpx.HTTPError, RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"获取方案失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.post("/{session_id}/confirm")
@rate_limit_api()
async def confirm_setup_plan(
    request: Request,
    session_id: str,
    data: PlanConfirm,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # P0-01: 校验会话归属
    await _verify_session_access(db, session_id, current_user)
    try:
        result = await setup_dialogue_service.confirm_plan(
            session_id=session_id,
            confirmed=data.confirmed,
            modifications=data.modifications,
            db=db,
        )
        # auto_apply=True（默认）且确认时，直接把 plan 作为 initial_state 调
        # build_agent_via_graph，不再返回待确认方案，跳过人工确认节点
        if data.auto_apply and data.confirmed and isinstance(result, dict):
            plan = result.get("plan", {}) or {}
            folder_path = plan.get("folder_path")
            if folder_path:
                import uuid as _uuid
                from app.services.agent_graph import build_agent_via_graph
                from app.database import async_session_factory

                thread_id = str(_uuid.uuid4())
                # P1-6.4: 优先使用 PlanConfirm 显式字段，未提供时回退到 modifications
                modifications = data.modifications or {}
                selected_model = data.model or modifications.get("model")
                selected_index = data.index_strategy or modifications.get("index_strategy")
                selected_skills = data.selected_skills or modifications.get("skills") or modifications.get("selected_skills")
                build_result = await build_agent_via_graph(
                    db_session_factory=async_session_factory,
                    enterprise_id=current_user.enterprise_id,
                    name=plan.get("name") or "未命名智能体",
                    description=plan.get("description") or "",
                    folder_path=folder_path,
                    require_approval=False,
                    thread_id=thread_id,
                    model=selected_model,
                    skills=selected_skills,
                    index_strategy=selected_index,
                )
                result["agent_id"] = build_result.get("agent_id")
                result["thread_id"] = thread_id
                result["build_status"] = build_result.get("status", "completed")
                result["redirect_url"] = f"/canvas/{thread_id}"
        # P1/P2-INFRA: 记录设置向导确认审计日志
        if isinstance(result, dict) and result.get("agent_id"):
            await log_audit(
                db, current_user, "create_agent", "agent", result["agent_id"],
                request=request,
                details={
                    "session_id": session_id,
                    "confirmed": data.confirmed,
                    "agent_name": result.get("agent_name"),
                },
            )
            await db.commit()
        return success_response(result)
    except ValueError as e:
        logger.warning(f"确认 setup 方案失败: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.SETUP_CONFIRM_FAILED) from e
    except (SQLAlchemyError, httpx.HTTPError, RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"确认方案失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
