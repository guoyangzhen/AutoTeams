"""WT4 协作 API 路由（spec.md §10.7 WT4 部分）。

端点（前缀 ``/api/v1/collaboration``，全部无尾斜杠）：
- POST   /events                                发布事件（事件驱动协作）
- GET    /events                                查询事件列表（分页 + 类型过滤）
- POST   /events/demo-case                      运行 7 步演示案例
- GET    /approvals                             审批门列表（分页）
- GET    /approvals/{gate_id}                   审批门详情
- POST   /approvals/{gate_id}/approve           审批通过
- POST   /approvals/{gate_id}/reject            审批拒绝
- POST   /human-takeover                        人类接管（当前明确返回 501）
- POST   /rollback                              触发回滚（操作级）

- POST   /rollback/process                      流程级回滚
- POST   /snapshots                             创建操作前快照
- GET    /snapshots                             快照列表（分页）

工程约束（spec §2.1 / §2.2 / §2.6）：
- 无尾斜杠
- 列表端点含 limit/offset 分页（Query(ge=1, le=100) / Query(ge=0)）
- 鉴权：get_current_user；企业隔离 + 管理员校验
- 限流：rate_limit_api / rate_limit_admin
- 错误响应统一使用 ErrorCode 常量
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.models.agent import Agent
from app.models.collaboration import (
    CollaborationEvent,
)
from app.models.enterprise import Enterprise
from app.models.user import User
from app.schemas.collaboration import (
    ApprovalGateView,
    ApproveGateRequest,
    ApproveGateResponse,
    CollaborationEvent as CollaborationEventSchema,
    EventListResponse,
    PublishEventRequest,
    PublishEventResponse,
    RejectGateRequest,
    RejectGateResponse,
    RollbackRequest,
    RollbackResponse,
)
from app.services.collaboration.event_bus import event_bus
from app.services.collaboration.human_ai_collaboration import (
    human_ai_collaboration,
)
from app.services.collaboration.rollback import rollback_manager
from app.services.cognition.vitals import collect_vitals
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/collaboration", tags=["Collaboration"])


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


def _verify_enterprise_admin(current_user: User, enterprise_id: str) -> None:
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if (
        current_user.enterprise_id != enterprise_id
        or current_user.role != "admin"
    ):
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


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


# ============================================================
# 事件驱动协作
# ============================================================


@router.post("/events", response_model=None)
@rate_limit_api()
async def publish_event(
    request: Request,
    data: PublishEventRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """发布事件（spec.md §10.7 POST /collaboration/events）。"""
    await _ensure_enterprise_exists(db, data.enterprise_id)
    _verify_enterprise_access(current_user, data.enterprise_id)

    event_id = await event_bus.publish(
        db,
        enterprise_id=data.enterprise_id,
        event_type=data.event_type,
        payload=data.payload,
        source_agent_id=data.source_agent_id,
        target_agent_id=data.target_agent_id,
    )

    await log_audit(
        db, current_user, "publish_event", "collaboration_event", event_id,
        request=request,
        details={"enterprise_id": data.enterprise_id,
                 "event_type": data.event_type},
    )

    response = PublishEventResponse(event_id=event_id, status="processed")
    return success_response(data=response.model_dump(mode="json"))


@router.get("/events", response_model=None)
@rate_limit_api()
async def list_events(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    event_type: Optional[str] = Query(None, alias="type"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询事件列表（spec.md §10.7 GET /collaboration/events）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    items, total = await event_bus.list_events(
        db,
        enterprise_id=enterprise_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )

    response = EventListResponse(
        items=[
            CollaborationEventSchema(
                event_id=e.id,
                enterprise_id=e.enterprise_id,
                event_type=e.event_type,
                payload=e.payload or {},
                source_agent_id=e.source_agent_id,
                target_agent_id=e.target_agent_id,
                status=e.status,
                created_at=e.created_at,
            )
            for e in items
        ],
        total=total,
    )
    return success_response(data=response.model_dump(mode="json"))


@router.get("/events/stream", response_model=None)
async def stream_events(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """协作事件流 SSE 推流（UI v4 §七 P1-5）。

    驾驶舱「实时运转」的数据源。修复审计问题：该页面标称「实时」、
    带脉冲绿点，实际却零轮询，是纯静态快照。

    实现说明：当前部署形态为 SQLite 单机 + 无 Redis，
    进程内 EventBus 无法跨 worker 广播，故采用「服务端增量轮询 + SSE 推送」——
    对前端而言是标准 SSE 实时流，且天然兼容多 worker 部署。
    前端可无缝降级到 GET /collaboration/events 轮询（弱网保命路径）。

    每帧：{events:[...], vitals:{...}, done:false}
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)
    # 提前释放请求 session，SSE 可能长时间持有
    await db.close()

    async def event_generator():
        last_seen_id: Optional[str] = None
        last_vitals_sig: Optional[str] = None
        elapsed = 0.0
        # 缩短 SSE 单次连接时长：生产链路为 浏览器→Cloudflare Worker→ngrok→nginx→后端，
        # 超长连接（此前 30 分钟）易被 Cloudflare/ngrok/浏览器在中间切断，表现为
        # 控制台 net::ERR_ABORTED。改为较短生命周期（120s）后由服务端主动收尾
        # （done:true），前端无缝降级到轮询，消除长连接被掐断的报错。
        max_seconds = 120.0
        interval = 3.0
        first_frame = True

        try:
            while elapsed < max_seconds:
                if await request.is_disconnected():
                    break

                async with async_session_factory() as sdb:
                    # 首帧给最近 30 条建立上下文，后续只推增量
                    q = (
                        select(CollaborationEvent)
                        .where(CollaborationEvent.enterprise_id == enterprise_id)
                        .order_by(CollaborationEvent.created_at.desc())
                        .limit(30 if first_frame else 20)
                    )
                    rows = list((await sdb.execute(q)).scalars().all())
                    vitals = await collect_vitals(sdb, enterprise_id)

                # 按时间正序推送，前端按到达顺序插入
                rows.reverse()
                if not first_frame and last_seen_id:
                    # 只保留上次游标之后的新事件
                    idx = next(
                        (i for i, e in enumerate(rows) if e.id == last_seen_id), None
                    )
                    rows = rows[idx + 1:] if idx is not None else rows

                new_events = [
                    {
                        "event_id": e.id,
                        "enterprise_id": e.enterprise_id,
                        "event_type": e.event_type,
                        "payload": e.payload or {},
                        "source_agent_id": e.source_agent_id,
                        "target_agent_id": e.target_agent_id,
                        "status": e.status,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                    }
                    for e in rows
                ]
                if rows:
                    last_seen_id = rows[-1].id

                vitals_sig = json.dumps(vitals, sort_keys=True, default=str)
                vitals_changed = vitals_sig != last_vitals_sig
                if vitals_changed:
                    last_vitals_sig = vitals_sig

                # 无变化时不发帧，仅靠注释行保活，避免无谓流量
                if new_events or vitals_changed or first_frame:
                    payload = {
                        "events": new_events,
                        "vitals": vitals if vitals_changed or first_frame else None,
                        "full_refresh": first_frame,
                        "done": False,
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                else:
                    yield ": keepalive\n\n"

                first_frame = False
                await asyncio.sleep(interval)
                elapsed += interval

            yield f"data: {json.dumps({'done': True, 'timeout': True}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "协作事件 SSE 异常: enterprise=%s error=%s", enterprise_id, e, exc_info=True
            )
            yield f"data: {json.dumps({'error': '事件流中断', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/events/demo-case", response_model=None)
@rate_limit_admin()
async def run_demo_case(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    sales_agent_id: str = Query(..., description="销售 Agent ID"),
    product_expert_agent_id: str = Query(..., description="产品专家 Agent ID"),
    finance_agent_id: str = Query(..., description="财务 Agent ID"),
    customer_service_agent_id: str = Query(..., description="客服 Agent ID"),
    after_sales_agent_id: str = Query(..., description="售后 Agent ID"),
    approval_mode: str = Query("human_approval_gate"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """运行 7 步演示案例（询盘→报价→审批→成交→售后，PRD §8.5）。

    在 approval 步骤创建审批门（人机协作 MVP 模式 1）。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    agent_ids = {
        "sales": sales_agent_id,
        "product_expert": product_expert_agent_id,
        "finance": finance_agent_id,
        "customer_service": customer_service_agent_id,
        "after_sales": after_sales_agent_id,
    }

    # 校验所有 Agent 属于该企业
    for aid in agent_ids.values():
        result = await db.execute(select(Agent).where(Agent.id == aid))
        agent = result.scalar_one_or_none()
        if agent is None or agent.enterprise_id != enterprise_id:
            raise HTTPException(
                status_code=404, detail=ErrorCode.AGENT_NOT_FOUND
            )

    result = await event_bus.run_demo_case(
        db,
        enterprise_id=enterprise_id,
        agent_ids=agent_ids,
        approval_mode=approval_mode,
    )

    await log_audit(
        db, current_user, "run_demo_case", "enterprise", enterprise_id,
        request=request,
        details={"events": len(result.get("events", [])),
                 "approval_gate_id": result.get("approval_gate_id")},
    )

    return success_response(
        data=result,
        message=f"7 步演示案例已完成（{len(result.get('events', []))} 个事件）",
    )


# ============================================================
# 审批门（人机协作）
# ============================================================


@router.get("/approvals", response_model=None)
@rate_limit_api()
async def list_approvals(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批门列表（分页）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    items, total = await human_ai_collaboration.list_gates(
        db,
        enterprise_id=enterprise_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    # 审批节点英文标识 → 中文名（英文保留在括号内附在中文后）
    # 用于把 director_approval 这类 node_id 转为用户可读的「总监审批（director_approval）」。
    NODE_LABELS = {
        "director_approval": "总监审批",
        "manager_approval": "经理审批",
        "finance_approval": "财务审批",
        "hr_approval": "人事审批",
        "procurement_approval": "采购审批",
        "technical_approval": "技术审批",
        "legal_approval": "法务审批",
        "ceo_approval": "总经理审批",
        "human_confirm": "人工确认",
        "human_approval_gate": "人工审批",
    }

    def _node_label(node_id: Optional[str]) -> str:
        if not node_id:
            return ""
        zh = NODE_LABELS.get(node_id)
        if zh:
            return f"{zh}（{node_id}）"
        return node_id

    # 收集本批审批门涉及的所有 agent_id，批量查询姓名（避免 N+1）
    agent_ids = [g.agent_id for g in items if g.agent_id]
    agent_name_map: dict[str, str] = {}
    if agent_ids:
        rows = (await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))).scalars().all()
        agent_name_map = {a.id: a.name for a in rows}

    return success_response(
        data={
            "items": [
                {
                    **ApprovalGateView(
                        id=g.id,
                        enterprise_id=g.enterprise_id,
                        process_id=g.process_id,
                        node_id=g.node_id,
                        agent_id=g.agent_id,
                        status=g.status,
                        approver_id=g.approver_id,
                        decided_at=g.decided_at,
                        created_at=g.created_at,
                    ).model_dump(mode="json"),
                    # 附加用户友好字段：申请人中文名 + 审批节点中文标签
                    "agent_name": agent_name_map.get(g.agent_id or "", g.agent_id or ""),
                    "node_label": _node_label(g.node_id),
                }
                for g in items
            ],
            "total": total,
        }
    )


@router.get("/approvals/{gate_id}", response_model=None)
@rate_limit_api()
async def get_approval(
    request: Request,
    gate_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批门详情。"""
    gate = await human_ai_collaboration.get_gate(db, gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    _verify_enterprise_access(current_user, gate.enterprise_id)

    response = ApprovalGateView(
        id=gate.id,
        enterprise_id=gate.enterprise_id,
        process_id=gate.process_id,
        node_id=gate.node_id,
        agent_id=gate.agent_id,
        status=gate.status,
        approver_id=gate.approver_id,
        decided_at=gate.decided_at,
        created_at=gate.created_at,
    )
    return success_response(data=response.model_dump(mode="json"))


@router.post("/approvals/{gate_id}/approve", response_model=None)
@rate_limit_api()
async def approve_gate(
    request: Request,
    gate_id: str,
    data: ApproveGateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批通过 → 流程继续（spec.md §10.7 POST /collaboration/approvals/{id}/approve）。"""
    gate = await human_ai_collaboration.get_gate(db, gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    _verify_enterprise_access(current_user, gate.enterprise_id)

    try:
        result = await human_ai_collaboration.approve_gate(
            db, gate_id, current_user.id, comment=data.comment
        )
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND) from e
        if "已处理" in msg:
            raise HTTPException(
                status_code=400, detail=ErrorCode.INVALID_REQUEST
            ) from e
        raise HTTPException(status_code=400, detail=ErrorCode.OPERATION_FAILED) from e

    await log_audit(
        db, current_user, "approve_gate", "approval_gate", gate_id,
        request=request, details={"comment": data.comment},
    )

    response = ApproveGateResponse(
        approved=True, process_resumed=result.get("process_resumed", True)
    )
    return success_response(data=response.model_dump(mode="json"))


@router.post("/approvals/{gate_id}/reject", response_model=None)
@rate_limit_api()
async def reject_gate(
    request: Request,
    gate_id: str,
    data: RejectGateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批拒绝 → 流程终止（spec.md §10.7 POST /collaboration/approvals/{id}/reject）。"""
    gate = await human_ai_collaboration.get_gate(db, gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    _verify_enterprise_access(current_user, gate.enterprise_id)

    try:
        await human_ai_collaboration.reject_gate(
            db, gate_id, current_user.id, reason=data.reason
        )
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND) from e
        if "已处理" in msg:
            raise HTTPException(
                status_code=400, detail=ErrorCode.INVALID_REQUEST
            ) from e
        raise HTTPException(status_code=400, detail=ErrorCode.OPERATION_FAILED) from e

    await log_audit(
        db, current_user, "reject_gate", "approval_gate", gate_id,
        request=request, details={"reason": data.reason},
    )

    response = RejectGateResponse(rejected=True)
    return success_response(data=response.model_dump(mode="json"))


# ------------------------------------------------------------
# 蓝图与前端规范别名路由：/approval-gates/{gate_id}/approve|reject
# ------------------------------------------------------------
@router.post("/approval-gates/{gate_id}/approve", response_model=None)
@rate_limit_api()
async def approve_gate_alias(
    request: Request,
    gate_id: str,
    data: ApproveGateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批通过别名路由，兼容 /approval-gates/{id}/approve。"""
    return await approve_gate(request, gate_id, data, db, current_user)


@router.post("/approval-gates/{gate_id}/reject", response_model=None)
@rate_limit_api()
async def reject_gate_alias(
    request: Request,
    gate_id: str,
    data: RejectGateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批拒绝别名路由，兼容 /approval-gates/{id}/reject。"""
    return await reject_gate(request, gate_id, data, db, current_user)

# ============================================================
# 人类接管（能力隔离）
# ============================================================


@router.post("/human-takeover", response_model=None)
@rate_limit_admin()
async def request_human_takeover(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    agent_id: str = Query(..., description="需要接管的 Agent ID"),
    task_id: str = Query(..., description="需要接管的任务 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """人类接管接口的显式能力边界。

    该能力尚未具备可恢复的任务运行时和人工工作台，不能把请求悄然写成伪审批门，
    也不能将服务层 ``NotImplementedError`` 泄漏为 500。因此完成身份、企业和
    Agent 归属校验后稳定返回 501，调用方可据此降级到现有审批门工作流。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None or agent.enterprise_id != enterprise_id:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)

    raise HTTPException(
        status_code=501,
        detail={
            "code": "HUMAN_TAKEOVER_NOT_IMPLEMENTED",
            "message": "人类接管尚未交付，请改用人工审批门或人工确认工作流",
            "task_id": task_id,
        },
    )


# ============================================================
# 回滚（操作级 + 流程级）
# ============================================================


@router.post("/rollback", response_model=None)
@rate_limit_admin()
async def rollback_operation(
    request: Request,
    data: RollbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """操作级回滚（spec.md §10.7 POST /collaboration/rollback）。

    回滚到指定快照（恢复 Agent 状态）。
    """
    snapshot = await rollback_manager.get_snapshot(db, data.snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)

    # 校验 Agent 属于当前用户企业
    agent_result = await db.execute(select(Agent).where(Agent.id == snapshot.agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    _verify_enterprise_admin(current_user, agent.enterprise_id)

    try:
        result = await rollback_manager.rollback_operation(db, data.snapshot_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=ErrorCode.OPERATION_FAILED) from None

    await log_audit(
        db, current_user, "rollback_operation", "operation_snapshot",
        data.snapshot_id,
        request=request,
        details={"agent_id": result.get("agent_id"),
                 "operation": result.get("operation")},
    )

    response = RollbackResponse(
        rolled_back=True,
        agent_id=result.get("agent_id", ""),
        operation=result.get("operation", ""),
        snapshot_id=data.snapshot_id,
    )
    return success_response(data=response.model_dump(mode="json"))


@router.post("/rollback/process", response_model=None)
@rate_limit_admin()
async def rollback_process(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    process_id: str = Query(..., description="流程 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """流程级回滚：回滚该流程下所有 Agent 的操作快照（按时间倒序）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    result = await rollback_manager.rollback_process(
        db, enterprise_id, process_id
    )

    await log_audit(
        db, current_user, "rollback_process", "process", process_id,
        request=request,
        details={"enterprise_id": enterprise_id,
                 "rolled_back_count": result.get("rolled_back_count", 0),
                 "agent_ids": result.get("agent_ids", [])},
    )

    return success_response(
        data=result,
        message=f"流程级回滚完成（{result.get('rolled_back_count', 0)} 个快照）"
        if result.get("rolled_back") else "无可回滚快照",
    )


# ============================================================
# 操作前快照（创建 + 查询）
# ============================================================


@router.post("/snapshots", response_model=None)
@rate_limit_admin()
async def create_snapshot(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    operation: str = Query(..., description="操作名"),
    process_id: Optional[str] = Query(None, description="所属流程 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """操作前创建状态快照（PRD §5.10 回滚机制）。"""
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    _verify_enterprise_admin(current_user, agent.enterprise_id)

    try:
        snapshot_id = await rollback_manager.create_snapshot(
            db, agent_id, operation, process_id=process_id
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=ErrorCode.OPERATION_FAILED) from None

    await log_audit(
        db, current_user, "create_snapshot", "operation_snapshot", snapshot_id,
        request=request,
        details={"agent_id": agent_id, "operation": operation,
                 "process_id": process_id},
    )

    return success_response(
        data={"snapshot_id": snapshot_id},
        message="操作快照已创建",
    )


@router.get("/snapshots", response_model=None)
@rate_limit_api()
async def list_snapshots(
    request: Request,
    agent_id: str = Query(..., description="Agent ID"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """快照列表（分页）。"""
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    _verify_enterprise_access(current_user, agent.enterprise_id)

    items, total = await rollback_manager.list_snapshots(
        db, agent_id, limit=limit, offset=offset
    )

    return success_response(
        data={
            "items": [
                {
                    "id": s.id,
                    "agent_id": s.agent_id,
                    "operation": s.operation,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in items
            ],
            "total": total,
        }
    )
