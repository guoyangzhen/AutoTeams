"""AutoTeams 4.0 业务规程（Flow-Core SOP）API 路由。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.flow_card import FlowCardModel
from app.models.user import User
from app.models.flow_run import FlowRun
from app.services.flow_core.schema import FlowCard, FlowExecutionState, FlowRunLimits
from app.services.flow_core.engine import FlowEngine
from app.services.flow_core.safe_eval import SafeEvalError, validate_condition_expression
from app.services.flow_core import run_store
from app.services.flow_core.run_store import create_run
from app.services.flow_core.sop_synthesizer import SOPSynthesizer
from app.utils.security import get_current_user
from app.utils.audit import log_audit
from app.utils.response import success_response
from app.utils.tenant_scope import require_enterprise_bound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flow-core", tags=["AutoTeams Flow-Core SOP"])


class SynthesizeRequest(BaseModel):
    text_sop: str
    name: Optional[str] = None
    flow_id: Optional[str] = None


class StartFlowRequest(BaseModel):
    """启动执行。客户端只能提供初始槽位与执行上限，不能提供权威状态。"""

    model_config = ConfigDict(extra="forbid")

    flow_id: str
    initial_slots: Optional[Dict[str, Any]] = None
    max_steps: int = Field(default=200, ge=1, le=10000)
    max_wall_clock_seconds: int = Field(default=300, ge=1, le=86400)


class StepFlowRequest(BaseModel):
    """单步推进。``extra="forbid"`` 让客户端提交的 ``state`` / ``approval_granted``
    直接 422 —— 执行状态与审批都由服务器持有（AUD-15）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="服务器签发的执行运行 ID")
    user_input: Optional[str] = None
    expected_version: Optional[int] = Field(default=None, ge=1, description="乐观锁版本号，不匹配返回 409")


class ApproveFlowRunRequest(BaseModel):
    """人工审批。决定只对 (run_id, node_id, 审批人) 生效。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    decision: Literal["granted", "rejected"]
    comment: Optional[str] = Field(default=None, max_length=2000)
    expected_version: Optional[int] = Field(default=None, ge=1)


@router.get("/cards", response_model=None)
async def list_flow_cards(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前企业所有已沉淀的业务 SOP 规程卡。"""
    stmt = select(FlowCardModel).where(
        FlowCardModel.enterprise_id == current_user.enterprise_id,
        FlowCardModel.is_active ,
    ).order_by(FlowCardModel.created_at.desc())
    result = await db.execute(stmt)
    records = result.scalars().all()
    data = [r.flow_data for r in records]
    return success_response(data=data)


@router.post("/cards", response_model=None, status_code=status.HTTP_201_CREATED)
async def save_flow_card(
    flow_card: FlowCard,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存或更新业务 SOP 规程卡。"""
    # P0 安全修复：condition_expression 为用户可控输入，入库前必须通过 AST 白名单校验，
    # 拒绝函数调用/属性访问等可被用于代码执行的表达式。
    for edge in flow_card.edges:
        if edge.condition_expression:
            try:
                validate_condition_expression(edge.condition_expression)
            except SafeEvalError as e:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"条件表达式不合法（边 {edge.source_node_id} → {edge.target_node_id}）: {e}",
                ) from e

    # 检查是否已存在对应 flow_id
    stmt = select(FlowCardModel).where(
        FlowCardModel.enterprise_id == current_user.enterprise_id,
        FlowCardModel.flow_id == flow_card.flow_id,
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        existing.name = flow_card.name
        existing.version = flow_card.version
        existing.description = flow_card.description
        existing.flow_data = flow_card.model_dump()
    else:
        model = FlowCardModel(
            id=str(uuid.uuid4()),
            enterprise_id=current_user.enterprise_id,
            flow_id=flow_card.flow_id,
            name=flow_card.name,
            version=flow_card.version,
            description=flow_card.description,
            flow_data=flow_card.model_dump(),
        )
        db.add(model)

    await db.commit()
    return success_response(data=flow_card.model_dump(), message="FlowCard 规程保存成功")


@router.get("/cards/{flow_id}", response_model=None)
async def get_flow_card(
    flow_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询单个 SOP 规程卡详情。"""
    stmt = select(FlowCardModel).where(
        FlowCardModel.enterprise_id == current_user.enterprise_id,
        FlowCardModel.flow_id == flow_id,
    )
    res = await db.execute(stmt)
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="规程卡不存在")
    return success_response(data=record.flow_data)


@router.post("/synthesize", response_model=None)
async def synthesize_sop(
    payload: SynthesizeRequest,
    current_user: User = Depends(get_current_user),
):
    """通过自然语言文本经验一键提炼编译为标准 FlowCard。"""
    flow_card = SOPSynthesizer.synthesize_from_text(
        text_sop=payload.text_sop,
        name=payload.name,
        flow_id=payload.flow_id,
    )
    return success_response(data=flow_card.model_dump(), message="成功提炼生成 SOP 状态机规程")


def _run_payload(run: FlowRun) -> Dict[str, Any]:
    return {
        "run_id": run.id,
        "flow_id": run.flow_id,
        "current_node_id": run.current_node_id,
        "status": run.status,
        "version": run.version,
        "step_count": run.step_count,
        "last_output": run.last_output,
        "error_message": run.error_message,
        "state": run.state,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


async def _load_flow_card(db: AsyncSession, flow_id: str, enterprise_id: str) -> FlowCard:
    stmt = select(FlowCardModel).where(
        FlowCardModel.enterprise_id == enterprise_id,
        FlowCardModel.flow_id == flow_id,
    )
    res = await db.execute(stmt)
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指定的规程不存在")
    return FlowCard.model_validate(record.flow_data)


@router.post("/execute/start", response_model=None, status_code=status.HTTP_201_CREATED)
async def start_flow_execution(
    payload: StartFlowRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """启动规程状态机执行：执行状态由服务器创建并落库（AUD-15）。"""
    enterprise_id = require_enterprise_bound(current_user)
    flow_card = await _load_flow_card(db, payload.flow_id, enterprise_id)

    state = await FlowEngine.start_flow(
        flow_card,
        initial_slots=payload.initial_slots,
        limits=FlowRunLimits(
            max_steps=payload.max_steps,
            max_wall_clock_seconds=payload.max_wall_clock_seconds,
        ),
    )
    run = await create_run(
        db, enterprise_id=enterprise_id, actor=current_user, flow=flow_card, state=state
    )
    await log_audit(
        db,
        current_user,
        action="flow_run_start",
        resource_type="flow_run",
        resource_id=run.id,
        details={"flow_id": flow_card.flow_id, "status": state.status, "node": state.current_node_id},
    )
    await db.commit()
    return success_response(data=_run_payload(run), message="规程状态机已启动")


@router.get("/execute/runs", response_model=None)
async def list_flow_runs(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前企业的执行运行（跨企业不可见）。"""
    enterprise_id = require_enterprise_bound(current_user)
    stmt = (
        select(FlowRun)
        .where(FlowRun.enterprise_id == enterprise_id)
        .order_by(FlowRun.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return success_response(data=[_run_payload(r) for r in result.scalars().all()])


@router.get("/execute/runs/{run_id}", response_model=None)
async def get_flow_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取执行运行（进程重启后仍可从数据库读回）。"""
    run = await run_store.load_run(db, run_id, current_user)
    approvals = await run_store.list_approvals(db, run)
    payload = _run_payload(run)
    payload["approvals"] = [
        {
            "id": a.id,
            "node_id": a.node_id,
            "approver_email": a.approver_email,
            "approver_role": a.approver_role,
            "decision": a.decision,
            "comment": a.comment,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in approvals
    ]
    return success_response(data=payload)


@router.post("/execute/step", response_model=None)
async def step_flow_execution(
    payload: StepFlowRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单步推进执行。状态一律从数据库读取，客户端提交的 state 会被 422 拒绝。"""
    require_enterprise_bound(current_user)
    run = await run_store.load_run(db, payload.run_id, current_user)
    flow_card = await _load_flow_card(db, run.flow_id, run.enterprise_id)
    state = FlowExecutionState.model_validate(run.state)

    next_state = await FlowEngine.step(flow=flow_card, state=state, user_input=payload.user_input)
    await run_store.save_state(
        db, run, next_state, actor=current_user, expected_version=payload.expected_version
    )
    await db.commit()
    return success_response(data=_run_payload(run), message="规程单步推进完成")


@router.post("/execute/runs/{run_id}/approve", response_model=None)
async def approve_flow_run(
    run_id: str,
    payload: ApproveFlowRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交人工审批并推进执行。

    审批绑定 (run_id, node_id, 审批人身份)；未授权调用返回 403 且不产生任何
    审批记录、不推进运行（AUD-15）。
    """
    require_enterprise_bound(current_user)
    run = await run_store.load_run(db, run_id, current_user)
    if payload.expected_version is not None and run.version != payload.expected_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=run_store.VERSION_CONFLICT)

    flow_card = await _load_flow_card(db, run.flow_id, run.enterprise_id)
    state = FlowExecutionState.model_validate(run.state)
    if state.status != "waiting_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"当前执行状态为 {state.status}，不处于等待审批",
        )
    if state.pending_approval_node_id != payload.node_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="提交的节点不是当前待审批节点",
        )
    node = FlowEngine.get_node(flow_card, state.current_node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前节点不存在")

    # 授权检查先于任何写操作：未授权不落库、不推进
    run_store.assert_approver_authorized(current_user, node)

    decision = await run_store.record_approval(
        db,
        run=run,
        node=node,
        approver=current_user,
        granted=payload.decision == "granted",
        comment=payload.comment,
    )
    next_state = await FlowEngine.step(flow=flow_card, state=state, approval=decision)
    await run_store.save_state(db, run, next_state, actor=current_user, expected_version=run.version)
    await log_audit(
        db,
        current_user,
        action="flow_run_approve",
        resource_type="flow_run",
        resource_id=run.id,
        details={
            "flow_id": run.flow_id,
            "node_id": node.node_id,
            "decision": payload.decision,
            "decision_id": decision.decision_id,
            "status_after": next_state.status,
        },
    )
    await db.commit()
    return success_response(data=_run_payload(run), message="审批已记录")

