"""文档处理 API（耐久队列版，AUD-17）。

变更说明
--------
历史实现把 `asyncio.create_task` 挂在请求进程里，任务状态与"谁在执行"都活在
单个进程的内存中：

* 请求落到另一个实例时 `/process/{id}/cancel` 查不到协程，取消无效；
* 滚动更新启动任一实例，`lifespan` 会把所有 `processing` 任务标成 failed，
  误伤仍由其他实例执行中的任务；
* 崩溃后任务永远停在 `processing`。

现在：

* `POST /start` 只**入队**（`status=pending` + 幂等键 + 配置快照），不启动协程；
* `POST /{id}/cancel` 只写数据库取消信号 `cancel_requested`，worker 观察后
  协作式停止 —— 这是跨进程的，不要求取消方与执行方同进程；
* 执行由独立 worker（`scripts/run_processing_worker.py`）以数据库租约驱动，
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.processing_task import ProcessingTask
from app.models.user import User
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.metrics import errors_total
from app.utils.rate_limit import rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/process", tags=["文档处理"])


class ProcessStartRequest(BaseModel):
    folder_path: str
    enterprise_id: str
    agent_name: str
    agent_description: Optional[str] = None
    # P1-FE: Setup 向导收集的附加配置（保存到 agent.config）
    model: Optional[str] = None
    skills: Optional[list[str]] = None
    index_strategy: Optional[str] = None


async def _update_task(task_id: str, **fields: Any) -> None:
    """在独立 session 中更新任务字段（供 worker 与运维脚本复用）。"""
    from app.database import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(select(ProcessingTask).where(ProcessingTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        await db.commit()


@router.post("/start")
@rate_limit_api()
async def start_processing(
    request: Request,
    data: ProcessStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建并入队一个文档处理任务。

    AUD-17：本端点不再启动任何协程。执行由耐久 worker 领取，返回后任务处于
    `pending`，由 worker 推进为 `processing`。
    """
    from app.services.processing_queue import (
        enqueue_processing_task,
        find_active_by_idempotency_key,
        make_idempotency_key,
    )

    # P0-01: 强制使用当前用户的企业 ID，忽略请求体中的 enterprise_id
    if not current_user.enterprise_id:
        raise HTTPException(
            status_code=403,
            detail=ErrorCode.ENTERPRISE_ACCESS_DENIED,
        )
    enterprise_id = current_user.enterprise_id

    idempotency_key = make_idempotency_key(enterprise_id, data.folder_path, data.agent_name)

    # 幂等：同一 (企业, 目录, Agent 名) 的未完成任务直接复用，避免重复执行副作用。
    existing = await find_active_by_idempotency_key(db, idempotency_key)
    if existing is not None:
        return success_response(
            existing.to_dict(),
            message="同一目录与 Agent 已有处理任务在队列中，已返回既有任务",
        )

    task = ProcessingTask(
        user_id=current_user.id,
        enterprise_id=enterprise_id,
        folder_path=data.folder_path,
        agent_name=data.agent_name,
        agent_description=data.agent_description or "",
        status="pending",
        progress=0.0,
        message="任务已入队，等待 worker 领取",
        total_files=0,
        processed_files=0,
        failed_files=0,
        knowledge_count=0,
    )
    db.add(task)
    await db.flush()
    task_id = task.id
    await db.commit()

    run_config: Dict[str, Any] = {
        "model": data.model,
        "skills": data.skills,
        "index_strategy": data.index_strategy,
    }
    await enqueue_processing_task(
        db, task_id=task_id, idempotency_key=idempotency_key, run_config=run_config
    )

    # P1/P2-INFRA: 记录处理任务启动审计日志
    await log_audit(
        db, current_user, "start", "processing_task", task_id,
        request=request,
        details={
            "folder_path": data.folder_path,
            "agent_name": data.agent_name,
            "enterprise_id": enterprise_id,
        },
    )
    await db.commit()
    await db.refresh(task)
    return success_response(task.to_dict(), message="任务已入队，等待 worker 处理")


async def _verify_task_access(
    db: AsyncSession, task_id: str, current_user: User
) -> ProcessingTask:
    """P0-01: 校验任务存在且归属当前用户的企业。

    普通企业用户：必须匹配 enterprise_id；
    超级管理员（enterprise_id=None）：放行（仅校验存在性）。
    """
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    query = select(ProcessingTask).where(ProcessingTask.id == task_id)
    if current_user.enterprise_id is not None:
        query = query.where(ProcessingTask.enterprise_id == current_user.enterprise_id)
    result = await db.execute(query)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=ErrorCode.TASK_NOT_FOUND)
    return task


@router.get("/{task_id}")
@rate_limit_api()
async def get_task_status(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await _verify_task_access(db, task_id, current_user)
    return success_response(task.to_dict())


@router.get("")
@rate_limit_api()
async def list_tasks(
    request: Request,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.enterprise_id:
        # 未归属企业的账号没有任何企业任务，返回空列表而不是 403：
        # 这是"查不到"，不是"被禁止"，与既有契约保持一致。
        return success_response({"tasks": [], "total": 0})

    base_conditions = []
    if current_user.enterprise_id is not None:
        base_conditions.append(ProcessingTask.enterprise_id == current_user.enterprise_id)
    if status:
        base_conditions.append(ProcessingTask.status == status)

    total_res = await db.execute(
        select(func.count())
        .select_from(ProcessingTask)
        .where(*base_conditions)
    )
    total = int(total_res.scalar_one())

    result = await db.execute(
        select(ProcessingTask)
        .where(*base_conditions)
        .order_by(ProcessingTask.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    tasks = result.scalars().all()
    return success_response({
        "tasks": [t.to_dict() for t in tasks],
        "total": total,
    })


@router.post("/{task_id}/cancel")
@rate_limit_api()
async def cancel_task(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消处理任务（仅 pending/processing 状态可取消）。

    AUD-17：取消通过数据库 `cancel_requested` 标志传递，**不要求与执行进程相同**。
    原本这里查的是进程内 `asyncio.Task` 字典，请求落到另一个实例就完全无效。
    """
    from app.services.processing_queue import request_cancel

    task = await _verify_task_access(db, task_id, current_user)
    if task.status not in ("pending", "processing"):
        raise HTTPException(status_code=400, detail=ErrorCode.TASK_ALREADY_FINISHED)

    previous_status = task.status
    await request_cancel(db, task_id)

    # pending 任务还没被领取，直接置为终态；processing 任务由 worker 观察标志后收尾。
    if task.status == "pending":
        task.status = "cancelled"
        task.message = "用户手动取消"
        task.completed_at = datetime.now(timezone.utc)

    # P1/P2-INFRA: 记录任务取消审计日志
    await log_audit(
        db, current_user, "cancel", "processing_task", task_id,
        request=request,
        details={"previous_status": previous_status, "cancel_via": "db_flag"},
    )
    await db.commit()
    await db.refresh(task)
    return success_response(task.to_dict())


@router.get("/{task_id}/report")
@rate_limit_api()
async def get_task_report(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    task = await _verify_task_access(db, task_id, current_user)

    return success_response({
        "task_id": task_id,
        "status": task.status,
        "summary": (
            f"共成功处理 {task.processed_files} 个文件，"
            f"失败 {task.failed_files} 个，"
            f"生成 {task.knowledge_count} 条知识"
        ),
        "files_processed": task.processed_files,
        "files_failed": task.failed_files,
        "knowledge_count": task.knowledge_count,
        "processing_time_seconds": task.processing_time_seconds,
    })


__all__ = ["errors_total", "router"]
