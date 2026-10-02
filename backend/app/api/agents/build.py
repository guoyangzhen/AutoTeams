"""LangGraph Agent 构建端点（耐久 Worker + HITL）。

端点：
- POST /agents/build_via_graph：提交可恢复构建任务，立即返回 task_id。
- POST /agents/build/jobs/{task_id}/resume：为 paused HITL 任务记录审批并重新入队。
- GET  /agents/build/jobs/{task_id}：读取持久化进度、结果与错误。
- GET  /agents/build/{thread_id}/state：兼容查询已生成 LangGraph thread 的状态快照。
"""
import logging

import chromadb.errors
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.database import async_session_factory
from app.models.agent_build_task import AgentBuildTask
from app.models.user import User
from app.schemas.agent import BuildAgentViaGraphRequest, ResumeBuildRequest
from app.services.agent_build_queue import enqueue_agent_build, resume_agent_build_task
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.metrics import errors_total
from app.utils.rate_limit import rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Agent"])


def _assert_enterprise_access(task: AgentBuildTask, current_user: User) -> None:
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)
    if current_user.enterprise_id is not None and task.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)
    if str(task.owner_user_id) != str(current_user.id) and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)


def _task_payload(task: AgentBuildTask) -> dict:
    result = task.result if isinstance(task.result, dict) else {}
    return {
        "task_id": task.id,
        "status": task.status,
        "thread_id": task.thread_id,
        "current_step": task.current_step,
        "attempt": task.attempt,
        "agent_id": result.get("agent_id"),
        "test_result": result.get("test_result"),
        "messages": result.get("messages", []),
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


@router.post("/build_via_graph", response_model=None, status_code=202)
@rate_limit_api()
async def build_agent_via_graph(
    data: BuildAgentViaGraphRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """提交 LangGraph 构建任务，避免扫描/LLM/向量化占用 HTTP 请求生命周期。"""
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    try:
        async with async_session_factory() as db:
            task, created = await enqueue_agent_build(
                db,
                enterprise_id=current_user.enterprise_id,
                owner_user_id=str(current_user.id),
                name=data.name,
                description=data.description or "",
                folder_path=data.folder_path,
                require_approval=data.require_approval,
            )
            await log_audit(
                db, current_user, "enqueue_agent_build", "agent_build_task", task.id,
                request=request,
                details={"queued": created, "require_approval": data.require_approval},
            )
            await db.commit()
            return success_response(
                _task_payload(task) | {"deduplicated": not created},
                message="Agent 构建任务已排队" if created else "相同 Agent 构建任务已在执行或等待审批",
            )
    except ValueError as exc:
        logger.warning("Agent 构建参数错误: %s", exc)
        raise HTTPException(status_code=400, detail=ErrorCode.AGENT_BUILD_INVALID) from exc
    except (httpx.HTTPError, RuntimeError, OSError, TypeError, chromadb.errors.ChromaError) as exc:
        errors_total.labels(module=__name__, exception_type=type(exc).__name__).inc()
        logger.error("提交 Agent 构建任务失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from exc


@router.get("/build/jobs/{task_id}", response_model=None)
@rate_limit_api()
async def get_agent_build_task(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    async with async_session_factory() as db:
        task = (await db.execute(select(AgentBuildTask).where(AgentBuildTask.id == task_id))).scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail=ErrorCode.AGENT_THREAD_NOT_FOUND)
        _assert_enterprise_access(task, current_user)
        return success_response(_task_payload(task))


@router.post("/build/jobs/{task_id}/resume", response_model=None, status_code=202)
@rate_limit_api()
async def resume_agent_build(
    task_id: str,
    data: ResumeBuildRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """记录 HITL 审批并将 paused 构建重新入队，由 Worker 继续执行。"""
    async with async_session_factory() as db:
        task = (await db.execute(select(AgentBuildTask).where(AgentBuildTask.id == task_id).with_for_update())).scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail=ErrorCode.AGENT_THREAD_NOT_FOUND)
        _assert_enterprise_access(task, current_user)
        try:
            task = await resume_agent_build_task(
                db, task=task, approved=data.approved, comment=data.comment
            )
        except ValueError:
            raise HTTPException(status_code=409, detail=ErrorCode.AGENT_RESUME_BUILD_INVALID) from None
        await log_audit(
            db, current_user, "resume_agent_build", "agent_build_task", task.id,
            request=request, details={"approved": data.approved, "thread_id": task.thread_id},
        )
        await db.commit()
        return success_response(_task_payload(task), message="审批已记录，Agent 构建任务已重新入队")


@router.get("/build/{thread_id}/state", response_model=None)
@rate_limit_api()
async def get_build_state(
    thread_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """兼容读取 LangGraph 状态快照；新客户端优先使用 /build/jobs/{task_id}。"""
    from app.services.agent_graph import build_agent_graph

    try:
        app = build_agent_graph(async_session_factory, require_approval=True)
        snapshot = await app.aget_state({"configurable": {"thread_id": thread_id}})
        if snapshot is None or not snapshot.values:
            raise HTTPException(status_code=404, detail=ErrorCode.AGENT_THREAD_NOT_FOUND)
        values = snapshot.values
        enterprise_id = values.get("enterprise_id")
        if current_user.enterprise_id is None and current_user.role != "admin":
            raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)
        if current_user.enterprise_id is not None and enterprise_id != current_user.enterprise_id:
            raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)
        return success_response({
            "thread_id": thread_id,
            "current_step": values.get("current_step"),
            "status": values.get("status"),
            "agent_id": values.get("agent_id"),
            "messages": values.get("messages", []),
            "test_result": values.get("test_result"),
            "next_step": snapshot.next,
            "is_paused": bool(snapshot.next),
        })
    except HTTPException:
        raise
    except (ValueError, httpx.HTTPError, RuntimeError, OSError, TypeError, chromadb.errors.ChromaError) as exc:
        errors_total.labels(module=__name__, exception_type=type(exc).__name__).inc()
        logger.error("查询 LangGraph 构建状态失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from exc
