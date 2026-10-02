"""WT4 交互式企业访谈 API 路由（spec.md §10.7 WT4 部分）。

端点（前缀 ``/api/v1/interview``，全部无尾斜杠）：
- POST   /sessions                          启动访谈会话
- GET    /sessions/{session_id}             会话状态（已回答/总数/完成度）
- GET    /sessions/{session_id}/next-question  下一问（按优先级）
- POST   /sessions/{session_id}/answers     提交回答（触发完成度更新 + 增量重编译）

工程约束（spec §2.1 / §2.2 / §2.6）：
- 无尾斜杠
- 列表端点含 limit/offset 分页（本组无列表端点，但会话状态含分页式字段）
- 鉴权：get_current_user；企业隔离
- 限流：rate_limit_api
- 错误响应统一使用 ErrorCode 常量
- LLM 调用前用户输入经 wrap_untrusted 包裹（MVP 暂不调用 LLM，service 层保留入口）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enterprise import Enterprise
from app.models.interview import InterviewSession
from app.models.user import User
from app.schemas.interview import (
    NextQuestionResponse,
    SessionStatusResponse,
    StartSessionRequest,
    StartSessionResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
)
from app.services.interview.interview_engine import interview_engine
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interview", tags=["Interview"])


# ============================================================
# 权限校验
# ============================================================


def _verify_enterprise_access(current_user: User, enterprise_id: str) -> None:
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id:
        raise HTTPException(
            status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED
        )


async def _ensure_enterprise_exists(
    db: AsyncSession, enterprise_id: str
) -> Enterprise:
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(
            status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND
        )
    if not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_DELETED)
    return enterprise


async def _load_session_with_access(
    db: AsyncSession, current_user: User, session_id: str
) -> InterviewSession:
    """加载访谈会话并校验当前用户对其企业有访问权。"""
    result = await db.execute(
        select(InterviewSession).where(InterviewSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    _verify_enterprise_access(current_user, session.enterprise_id)
    return session


# ============================================================
# 启动访谈会话
# ============================================================


@router.post("/sessions", response_model=None)
@rate_limit_api()
async def start_session(
    request: Request,
    data: StartSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """启动访谈会话（spec.md §10.7 POST /interview/sessions）。

    基于问题库创建会话 + 实例化 7 大类问题记录。
    """
    await _ensure_enterprise_exists(db, data.enterprise_id)
    _verify_enterprise_access(current_user, data.enterprise_id)

    result = await interview_engine.start_session(
        db, data.enterprise_id, current_user.id
    )

    await log_audit(
        db, current_user, "start_interview", "interview_session", result["session_id"],
        request=request,
        details={"enterprise_id": data.enterprise_id,
                 "total_count": result["total_count"]},
    )

    response = StartSessionResponse(
        session_id=result["session_id"],
        status=result["status"],
        total_count=result["total_count"],
    )
    return success_response(data=response.model_dump(mode="json"))


# ============================================================
# 会话状态
# ============================================================


@router.get("/sessions/{session_id}", response_model=None)
@rate_limit_api()
async def get_session_status(
    request: Request,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """会话状态（spec.md §10.7 GET /interview/sessions/{id}）。

    返回 {status, answered_count, total_count, completeness}。
    """
    await _load_session_with_access(db, current_user, session_id)

    status_data = await interview_engine.get_session_status(db, session_id)

    response = SessionStatusResponse(
        session_id=status_data["session_id"],
        status=status_data["status"],
        answered_count=status_data["answered_count"],
        total_count=status_data["total_count"],
        completeness=status_data["completeness"],
    )
    return success_response(data=response.model_dump(mode="json"))


# ============================================================
# 下一问
# ============================================================


@router.get("/sessions/{session_id}/next-question", response_model=None)
@rate_limit_api()
async def get_next_question(
    request: Request,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下一问（spec.md §10.7 GET /interview/sessions/{id}/next-question）。

    无下一问时 question_id 为 None（会话已完成）。
    """
    await _load_session_with_access(db, current_user, session_id)

    next_q = await interview_engine.get_next_question(db, session_id)

    response = NextQuestionResponse(
        question_id=next_q.get("question_id"),
        category=next_q.get("category"),
        question=next_q.get("question"),
        priority=next_q.get("priority"),
        affected_field=next_q.get("affected_field"),
    )
    return success_response(data=response.model_dump(mode="json"))


# ============================================================
# 提交回答
# ============================================================


@router.post("/sessions/{session_id}/answers", response_model=None)
@rate_limit_api()
async def submit_answer(
    request: Request,
    session_id: str,
    data: SubmitAnswerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交回答（spec.md §10.7 POST /interview/sessions/{id}/answers）。

    记录回答 → 更新完成度 → 触发增量重编译（best-effort） → 返回下一问。
    """
    await _load_session_with_access(db, current_user, session_id)

    try:
        result = await interview_engine.submit_answer(
            db, session_id, data.question_id, data.answer
        )
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg or "不属于" in msg:
            raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND) from e
        if "已回答" in msg:
            raise HTTPException(
                status_code=400, detail=ErrorCode.INVALID_REQUEST
            ) from e
        if "已完成" in msg:
            raise HTTPException(
                status_code=400, detail=ErrorCode.INVALID_REQUEST
            ) from e
        raise HTTPException(status_code=400, detail=ErrorCode.OPERATION_FAILED) from e

    await log_audit(
        db, current_user, "submit_answer", "interview_question", data.question_id,
        request=request,
        details={"session_id": session_id,
                 "updated_completeness": result.get("updated_completeness"),
                 "recompile_triggered": result.get("recompile_triggered")},
    )

    next_q_data = result.get("next_question") or {}
    next_response = NextQuestionResponse(
        question_id=next_q_data.get("question_id"),
        category=next_q_data.get("category"),
        question=next_q_data.get("question"),
        priority=next_q_data.get("priority"),
        affected_field=next_q_data.get("affected_field"),
    )

    response = SubmitAnswerResponse(
        updated_completeness=result["updated_completeness"],
        recompile_triggered=result.get("recompile_triggered", False),
        next_question=next_response if next_response.question_id else None,
    )
    return success_response(data=response.model_dump(mode="json"))
