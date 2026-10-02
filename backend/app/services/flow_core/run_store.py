"""Flow 运行态持久化与审批授权（AUD-15）。

本模块是 API 层唯一允许写 ``flow_runs`` / ``flow_approvals`` 的地方，集中处理：

- 租户边界：所有读写都走 ``app.utils.tenant_scope``，跨企业一律 404。
- 乐观锁：状态推进用 ``UPDATE ... WHERE version = :expected``，命中 0 行即 409。
- 审批授权：只有被授权的审批人（企业管理员，或节点 ``assignee_role`` 匹配的角色）
  才能产生审批记录；审批记录与审批人身份、决定 ID 一并落库。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow_run import FlowApproval, FlowRun
from app.models.user import User
from app.services.flow_core.schema import FlowApprovalDecision, FlowCard, FlowExecutionState, FlowNode
from app.utils.tenant_scope import ROLE_ADMIN, load_tenant_scoped, require_enterprise_bound

logger = logging.getLogger(__name__)

RUN_NOT_FOUND = "规程执行实例不存在"
VERSION_CONFLICT = "规程执行状态已被其他请求更新，请重新读取后重试"
TERMINAL_STATUSES = ("completed", "failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND)


def _version_conflict() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=VERSION_CONFLICT)


async def create_run(
    db: AsyncSession,
    *,
    enterprise_id: str,
    actor: User,
    flow: FlowCard,
    state: FlowExecutionState,
) -> FlowRun:
    """创建一次规程执行运行态（服务器权威状态，客户端不参与）。"""
    run = FlowRun(
        id=str(uuid.uuid4()),
        enterprise_id=enterprise_id,
        flow_id=flow.flow_id,
        flow_version=flow.version,
        current_node_id=state.current_node_id,
        state=state.model_dump(mode="json"),
        status=state.status,
        version=1,
        step_count=state.step_count,
        last_output=state.last_output,
        error_message=state.error_message,
        started_by=actor.id,
        last_actor_id=actor.id,
    )
    if state.status in TERMINAL_STATUSES:
        run.finished_at = _now()
    db.add(run)
    await db.flush()
    return run


async def load_run(db: AsyncSession, run_id: str, user: User) -> FlowRun:
    """按企业边界加载运行态；不存在与跨企业统一 404。"""
    return await load_tenant_scoped(
        db,
        FlowRun,
        FlowRun.id,
        run_id,
        FlowRun.enterprise_id,
        user,
        not_found_detail=RUN_NOT_FOUND,
    )


async def save_state(
    db: AsyncSession,
    run: FlowRun,
    state: FlowExecutionState,
    *,
    actor: User,
    expected_version: Optional[int] = None,
) -> FlowRun:
    """带乐观锁地写回执行状态。"""
    if expected_version is not None and run.version != expected_version:
        raise _version_conflict()
    if run.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"规程执行已结束（{run.status}），不能继续推进",
        )

    values = {
        "current_node_id": state.current_node_id,
        "state": state.model_dump(mode="json"),
        "status": state.status,
        "step_count": state.step_count,
        "last_output": state.last_output,
        "error_message": state.error_message,
        "last_actor_id": actor.id,
        "updated_at": _now(),
        "version": run.version + 1,
    }
    if state.status in TERMINAL_STATUSES:
        values["finished_at"] = _now()

    result = await db.execute(
        update(FlowRun)
        .where(FlowRun.id == run.id, FlowRun.version == run.version)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise _version_conflict()
    await db.refresh(run)
    return run


def assert_approver_authorized(approver: User, node: FlowNode) -> None:
    """审批人授权检查：不通过直接 403，且不产生任何审批记录。

    规则：
    - 调用者必须已归属企业（企业外用户不能审批企业规程）。
    - 节点显式声明 ``assignee_role`` 时，仅该角色的用户可审批。
    - 未声明时，仅企业管理员可审批（普通成员无权代表企业放行）。
    """
    require_enterprise_bound(approver)
    required_role = node.assignee_role
    if required_role:
        if approver.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"当前节点需要角色 '{required_role}' 审批，你无权审批该节点",
            )
        return
    if approver.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该审批节点未指定审批角色，仅企业管理员可审批",
        )


async def record_approval(
    db: AsyncSession,
    *,
    run: FlowRun,
    node: FlowNode,
    approver: User,
    granted: bool,
    comment: Optional[str] = None,
) -> FlowApprovalDecision:
    """写入审批记录并返回供引擎消费的审批决定对象。"""
    decision_id = str(uuid.uuid4())
    decided_at = _now().isoformat()
    approval = FlowApproval(
        id=decision_id,
        run_id=run.id,
        node_id=node.node_id,
        enterprise_id=run.enterprise_id,
        approver_user_id=approver.id,
        approver_email=approver.email,
        approver_role=approver.role,
        decision="granted" if granted else "rejected",
        comment=comment,
        applied_version=run.version + 1,
    )
    db.add(approval)
    await db.flush()
    logger.info(
        "Flow 审批落库 run=%s node=%s approver=%s decision=%s",
        run.id, node.node_id, approver.email, approval.decision,
    )
    return FlowApprovalDecision(
        node_id=node.node_id,
        granted=granted,
        approver_user_id=approver.id,
        approver_email=approver.email,
        approver_role=approver.role,
        decision_id=decision_id,
        decided_at=decided_at,
        comment=comment,
    )


async def list_approvals(db: AsyncSession, run: FlowRun) -> list[FlowApproval]:
    result = await db.execute(
        select(FlowApproval).where(FlowApproval.run_id == run.id).order_by(FlowApproval.created_at)
    )
    return list(result.scalars().all())
