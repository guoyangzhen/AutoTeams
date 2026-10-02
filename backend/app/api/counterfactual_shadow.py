"""双盲反事实影子评估 API 路由（AutoTeams 5.0 战役 4）。

端点（前缀 ``/api/v1/shadow/counterfactual``，全部无尾斜杠）：
- POST /sessions                      提交一次反事实推演（双盲差分 + 免干预转正裁决）
- GET  /sessions                      会话列表（分页 + 按数字员工过滤）
- GET  /sessions/{session_id}         会话详情（含四维差分瀑布）
- POST /sessions/{session_id}/replay  重放裁决（不落库，用于推演演练）
- GET  /promotion-gate                免干预转正准入看板（按数字员工）

工程约束（对齐既有 shadow 路由规范）：
- 无尾斜杠；列表端点含 limit/offset 分页
- 鉴权：get_current_user；企业隔离 + 管理员校验
- 限流：rate_limit_api / rate_limit_admin
- 错误响应统一使用 ErrorCode 常量
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.counterfactual_shadow import (
    CounterfactualDiff,
    ShadowEvaluationSession,
)
from app.models.enterprise import Enterprise
from app.models.user import User
from app.schemas.counterfactual_shadow import (
    CounterfactualDiffView,
    CounterfactualSessionListResponse,
    CounterfactualSessionView,
    CounterfactualVerdictView,
    CreateCounterfactualSessionRequest,
    PromotionDecisionView,
    PromotionGateView,
)
from app.services.shadow import counterfactual_evaluator as evaluator
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shadow/counterfactual", tags=["ShadowCounterfactual"])


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
    if current_user.enterprise_id != enterprise_id or current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


async def _ensure_enterprise_exists(db: AsyncSession, enterprise_id: str) -> Enterprise:
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


async def _get_session_or_404(
    db: AsyncSession, session_id: str
) -> ShadowEvaluationSession:
    session = await evaluator.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    return session


# ============================================================
# 视图转换
# ============================================================


def _diff_view(diff: CounterfactualDiff) -> CounterfactualDiffView:
    return CounterfactualDiffView(
        diff_id=diff.diff_id,
        session_id=diff.session_id,
        dimension=diff.dimension,
        human_value=diff.human_value or "",
        agent_value=diff.agent_value or "",
        delta_score=diff.delta_score or 0.0,
        assessment=diff.assessment or "parity",
        note=diff.note or "",
        created_at=diff.created_at,
    )


def _session_view(
    session: ShadowEvaluationSession,
    diffs: Optional[list[CounterfactualDiff]] = None,
) -> CounterfactualSessionView:
    view = CounterfactualSessionView(
        session_id=session.session_id,
        enterprise_id=session.enterprise_id,
        employee_badge=session.employee_badge,
        scenario=session.scenario,
        human_action_snapshot=session.human_action_snapshot,
        agent_proposal_snapshot=session.agent_proposal_snapshot,
        semantic_alignment_score=session.semantic_alignment_score or 0.0,
        time_saving_seconds=session.time_saving_seconds or 0.0,
        cost_delta_yuan=session.cost_delta_yuan or 0.0,
        expected_net_benefit_yuan=session.expected_net_benefit_yuan or 0.0,
        human_duration_seconds=session.human_duration_seconds or 0.0,
        agent_duration_seconds=session.agent_duration_seconds or 0.0,
        human_cost_yuan=session.human_cost_yuan or 0.0,
        agent_cost_yuan=session.agent_cost_yuan or 0.0,
        guardrail_breach_count=session.guardrail_breach_count or 0,
        is_qualified=bool(session.is_qualified),
        consecutive_pass_streak=session.consecutive_pass_streak or 0,
        is_auto_promoted=bool(session.is_auto_promoted),
        promoted_at=session.promoted_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
    if diffs:
        view.diffs = [_diff_view(d) for d in diffs]
    view.verdict = CounterfactualVerdictView(
        is_qualified=bool(session.is_qualified),
        semantic_alignment_score=session.semantic_alignment_score or 0.0,
        time_saving_seconds=session.time_saving_seconds or 0.0,
        cost_delta_yuan=session.cost_delta_yuan or 0.0,
        expected_net_benefit_yuan=session.expected_net_benefit_yuan or 0.0,
        guardrail_breach_count=session.guardrail_breach_count or 0,
        reasons=list(evaluator.explain_stored(session)),
    )
    view.promotion = PromotionDecisionView(
        is_auto_promoted=bool(session.is_auto_promoted),
        consecutive_pass_streak=session.consecutive_pass_streak or 0,
        required_streak=evaluator.PROMOTION_STREAK_THRESHOLD,
        remaining_to_promotion=max(
            0, evaluator.PROMOTION_STREAK_THRESHOLD - (session.consecutive_pass_streak or 0)
        ),
        hit_threshold=bool(session.is_auto_promoted),
    )
    return view


# ============================================================
# 推演提交
# ============================================================


@router.post("/sessions", response_model=None)
@rate_limit_api()
async def create_session(
    request: Request,
    data: CreateCounterfactualSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交一次反事实推演：双盲差分裁决 + 免干预转正裁决。"""
    await _ensure_enterprise_exists(db, data.enterprise_id)
    _verify_enterprise_access(current_user, data.enterprise_id)

    required_streak = data.required_streak
    if required_streak is not None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)

    result = await evaluator.record_session(
        db,
        enterprise_id=data.enterprise_id,
        employee_badge=data.employee_badge,
        scenario=data.scenario,
        human_action_snapshot=data.human_action_snapshot,
        agent_proposal_snapshot=data.agent_proposal_snapshot,
        human_duration_seconds=data.human_duration_seconds,
        agent_duration_seconds=data.agent_duration_seconds,
        human_cost_yuan=data.human_cost_yuan,
        agent_cost_yuan=data.agent_cost_yuan,
        guardrail_breach_count=data.guardrail_breach_count,
        required_streak=required_streak or evaluator.PROMOTION_STREAK_THRESHOLD,
        hourly_labor_rate_yuan=data.hourly_labor_rate_yuan,
    )

    await log_audit(
        db, current_user, "create_counterfactual_session",
        "shadow_evaluation_session", result.session.session_id,
        request=request,
        details={
            "enterprise_id": data.enterprise_id,
            "employee_badge": data.employee_badge,
            "is_qualified": result.session.is_qualified,
            "is_auto_promoted": result.session.is_auto_promoted,
        },
    )
    await db.commit()

    view = _session_view(result.session, result.diffs)
    view.verdict = (
        CounterfactualVerdictView.model_validate(result.verdict.to_dict())
        if result.verdict
        else None
    )
    view.promotion = (
        PromotionDecisionView.model_validate(result.promotion.to_dict())
        if result.promotion
        else None
    )
    return success_response(data=view.model_dump(mode="json"))


@router.get("/sessions", response_model=None)
@rate_limit_api()
async def list_sessions(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    employee_badge: Optional[str] = Query(None, description="按数字员工名牌过滤"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """反事实推演会话列表（分页）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    sessions, total = await evaluator.list_sessions(
        db, enterprise_id, employee_badge, limit, offset
    )
    response = CounterfactualSessionListResponse(
        items=[_session_view(s) for s in sessions], total=total
    )
    return success_response(data=response.model_dump(mode="json"))


@router.get("/sessions/{session_id}", response_model=None)
@rate_limit_api()
async def get_session_detail(
    request: Request,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """会话详情（含四维差分瀑布）。"""
    session = await _get_session_or_404(db, session_id)
    _verify_enterprise_access(current_user, session.enterprise_id)
    diffs = await evaluator.get_diffs(db, session_id)
    return success_response(data=_session_view(session, diffs).model_dump(mode="json"))


@router.post("/sessions/{session_id}/replay", response_model=None)
@rate_limit_api()
async def replay_session(
    request: Request,
    session_id: str,
    hourly_labor_rate_yuan: float = Query(
        evaluator.DEFAULT_HOURLY_LABOR_RATE_YUAN, gt=0.0, le=100000.0
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重放裁决：按当前时薪重算净收益，不落库（用于收益折现推演）。"""
    session = await _get_session_or_404(db, session_id)
    _verify_enterprise_access(current_user, session.enterprise_id)

    verdict = evaluator.evaluate_session(
        human_action_snapshot=session.human_action_snapshot,
        agent_proposal_snapshot=session.agent_proposal_snapshot,
        human_duration_seconds=session.human_duration_seconds,
        agent_duration_seconds=session.agent_duration_seconds,
        human_cost_yuan=session.human_cost_yuan,
        agent_cost_yuan=session.agent_cost_yuan,
        guardrail_breach_count=session.guardrail_breach_count,
        hourly_labor_rate_yuan=hourly_labor_rate_yuan,
    )
    return success_response(
        data={
            "verdict": verdict.to_dict(),
            "diffs": evaluator.build_diff_rows(
                verdict,
                session.human_action_snapshot,
                session.agent_proposal_snapshot,
            ),
        }
    )


# ============================================================
# 免干预转正准入看板
# ============================================================


@router.get("/promotion-gate", response_model=None)
@rate_limit_api()
async def get_promotion_gate(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    employee_badge: str = Query(..., description="数字员工名牌"),
    required_streak: int = Query(
        evaluator.PROMOTION_STREAK_THRESHOLD, ge=1, le=1000
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """免干预转正准入看板：连续达标进度、剩余笔数与阈值口径。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    total = int(
        await db.scalar(
            select(func.count())
            .select_from(ShadowEvaluationSession)
            .where(
                ShadowEvaluationSession.enterprise_id == enterprise_id,
                ShadowEvaluationSession.employee_badge == employee_badge,
            )
        )
        or 0
    )
    qualified = int(
        await db.scalar(
            select(func.count())
            .select_from(ShadowEvaluationSession)
            .where(
                ShadowEvaluationSession.enterprise_id == enterprise_id,
                ShadowEvaluationSession.employee_badge == employee_badge,
                ShadowEvaluationSession.is_qualified.is_(True),
            )
        )
        or 0
    )
    promoted = await evaluator.is_promoted(db, enterprise_id, employee_badge)
    streak = await evaluator.current_streak(db, enterprise_id, employee_badge)

    gate = PromotionGateView(
        enterprise_id=enterprise_id,
        employee_badge=employee_badge,
        consecutive_pass_streak=streak,
        required_streak=required_streak,
        remaining_to_promotion=max(0, required_streak - streak),
        is_auto_promoted=promoted,
        total_sessions=total,
        qualified_sessions=qualified,
        semantic_alignment_threshold=evaluator.SEMANTIC_ALIGNMENT_THRESHOLD,
        max_cost_delta_yuan=evaluator.MAX_COST_DELTA_YUAN,
        max_guardrail_breaches=evaluator.MAX_GUARDRAIL_BREACHES,
    )
    return success_response(data=gate.model_dump(mode="json"))


@router.post("/promotion-gate", response_model=None)
@rate_limit_admin()
async def reset_promotion_gate(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    employee_badge: str = Query(..., description="数字员工名牌"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重置准入看板：清空该员工的转正标记与连续记录（仅管理员，回退/演练用）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    from app.utils.time import utcnow

    result = await db.execute(
        select(ShadowEvaluationSession).where(
            ShadowEvaluationSession.enterprise_id == enterprise_id,
            ShadowEvaluationSession.employee_badge == employee_badge,
            ShadowEvaluationSession.is_auto_promoted.is_(True),
        )
    )
    reset_count = 0
    for session in result.scalars().all():
        session.is_auto_promoted = False
        session.promoted_at = None
        session.consecutive_pass_streak = 0
        session.updated_at = utcnow()
        reset_count += 1
    await db.commit()

    await log_audit(
        db, current_user, "reset_counterfactual_promotion",
        "shadow_evaluation_session", employee_badge,
        request=request,
        details={"enterprise_id": enterprise_id, "reset": reset_count},
    )
    await db.commit()

    return success_response(
        data={"enterprise_id": enterprise_id, "employee_badge": employee_badge,
              "reset": reset_count}
    )
