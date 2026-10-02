"""WT4 进化层 API 路由（spec.md §10.7 WT4 部分）。

端点（前缀 ``/api/v1/evolution``，全部无尾斜杠）：
- POST   /{enterprise_id}/suggestions/generate          触发生成建议（LLM + 规则化回退）
- GET    /{enterprise_id}/suggestions                   建议列表（分页）
- POST   /suggestions/{suggestion_id}/apply             一键应用建议
- POST   /suggestions/{suggestion_id}/reject           拒绝建议
- GET    /{enterprise_id}/metrics                       5 类指标 + 成熟度
- GET    /{enterprise_id}/timeline                       Evolution Timeline（分页）
- GET    /{enterprise_id}/optimizations                  持续优化历史（分页）
- POST   /{enterprise_id}/optimizations/{agent_id}/apply   应用优化项
- POST   /{enterprise_id}/rollback-check/{agent_id}        RAG 退化自动回滚检查

工程约束（spec §2.1 / §2.2 / §2.6）：
- 无尾斜杠
- 列表端点含 limit/offset 分页（Query(ge=1, le=100) / Query(ge=0)）
- 鉴权：get_current_user；企业隔离 + 管理员校验
- 限流：rate_limit_api / rate_limit_admin
- 企业相关响应排除敏感字段（AdvisorSuggestion 不含 system_prompt 等）
- 错误响应统一使用 ErrorCode 常量
- LLM 调用前用户输入经 wrap_untrusted 包裹（在 advisor service 层已实现）
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.evolution import AdvisorSuggestion as AdvisorSuggestionModel, OrgMetrics as OrgMetricsModel
from app.models.optimization_history import OptimizationHistory
from app.models.user import User
from app.schemas.evolution import (
    AdvisorSuggestion,
    ApplySuggestionResponse,
    MaturityRating,
    OrgMetricsResponse,
    RejectSuggestionRequest,
    RejectSuggestionResponse,
    SuggestionListResponse,
    TimelineEntry,
    TimelineResponse,
)
from app.services.evolution.advisor import advisor_service
from app.services.evolution.continuous_optimizer import continuous_optimizer
from app.services.evolution.org_analytics import org_analytics
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evolution", tags=["AI Evolution"])


# ============================================================
# 权限校验（与 workforce.py 保持一致）
# ============================================================


def _verify_enterprise_access(current_user: User, enterprise_id: str) -> None:
    """校验当前用户属于目标企业（或为系统超管）。"""
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)


def _verify_enterprise_admin(current_user: User, enterprise_id: str) -> None:
    """校验当前用户是目标企业的管理员（或系统超管）。"""
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
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)
    if not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_DELETED)
    return enterprise


def _suggestion_to_view(s: AdvisorSuggestion) -> AdvisorSuggestion:
    """ORM → schema 视图（已使用 from_attributes，显式构造便于未来字段裁剪）。"""
    return AdvisorSuggestion.model_validate(s)


# ============================================================
# AI Advisor：建议生成 / 列表 / 应用 / 拒绝
# ============================================================


@router.post("/{enterprise_id}/suggestions/generate", response_model=None)
@rate_limit_admin()
async def generate_suggestions(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """触发生成 AI 优化建议（PRD §5.4，4 类：knowledge/process/capability/organization）。

    LLM 优先 + 规则化回退；建议持久化到 advisor_suggestions 表（状态 pending）。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    created = await advisor_service.generate_suggestions(db, enterprise_id)

    await log_audit(
        db, current_user, "generate_suggestions", "enterprise", enterprise_id,
        request=request, details={"count": len(created)},
    )

    return success_response(
        data={"suggestions": created, "count": len(created)},
        message=f"已生成 {len(created)} 条建议",
    )


@router.get("/{enterprise_id}/suggestions", response_model=None)
@rate_limit_api()
async def list_suggestions(
    request: Request,
    enterprise_id: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """建议列表（分页，spec.md §10.7 GET /evolution/{enterprise_id}/suggestions）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    items, total = await advisor_service.list_suggestions(
        db, enterprise_id, status=status_filter, limit=limit, offset=offset
    )

    response = SuggestionListResponse(
        items=[_suggestion_to_view(s) for s in items],
        total=total,
    )
    return success_response(data=response.model_dump(mode="json"))


@router.post("/suggestions/{suggestion_id}/apply", response_model=None)
@rate_limit_admin()
async def apply_suggestion(
    request: Request,
    suggestion_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """一键应用建议（PRD §5.4：建议→确认→应用）。"""
    # 加载建议以校验企业归属
    result = await db.execute(
        select(AdvisorSuggestionModel).where(AdvisorSuggestionModel.id == suggestion_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    _verify_enterprise_admin(current_user, record.enterprise_id)

    try:
        apply_result = await advisor_service.apply_suggestion(db, suggestion_id)
    except ValueError as e:
        # 区分"不存在/已应用/已拒绝"
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND) from e
        raise HTTPException(status_code=400, detail=ErrorCode.OPERATION_FAILED) from e

    await log_audit(
        db, current_user, "apply_suggestion", "advisor_suggestion", suggestion_id,
        request=request,
        details={"affected_agents": apply_result.get("affected_agents", [])},
    )

    response = ApplySuggestionResponse(
        applied=True,
        suggestion_id=suggestion_id,
        affected_agents=apply_result.get("affected_agents", []),
        message=apply_result.get("message", ""),
    )
    return success_response(data=response.model_dump(mode="json"))


@router.post("/suggestions/{suggestion_id}/reject", response_model=None)
@rate_limit_admin()
async def reject_suggestion(
    request: Request,
    suggestion_id: str,
    data: RejectSuggestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """拒绝建议 → 记录并降频推送（PRD §5.4）。"""
    result = await db.execute(
        select(AdvisorSuggestionModel).where(AdvisorSuggestionModel.id == suggestion_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    _verify_enterprise_admin(current_user, record.enterprise_id)

    try:
        await advisor_service.reject_suggestion(
            db, suggestion_id, reason=data.reason
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=ErrorCode.OPERATION_FAILED) from e

    await log_audit(
        db, current_user, "reject_suggestion", "advisor_suggestion", suggestion_id,
        request=request, details={"reason": data.reason},
    )

    response = RejectSuggestionResponse(
        rejected=True, suggestion_id=suggestion_id
    )
    return success_response(data=response.model_dump(mode="json"))


# ============================================================
# AI Org Analytics：5 类指标 + 成熟度
# ============================================================


@router.get("/{enterprise_id}/metrics", response_model=None)
@rate_limit_api()
async def get_metrics(
    request: Request,
    enterprise_id: str,
    period: Optional[str] = Query(None, description="统计周期，如 2026-07"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """5 类指标 + 成熟度评级（spec.md §10.7 GET /evolution/{enterprise_id}/metrics）。

    返回 {agent_workload, process_efficiency, tool_usage, business_impact, maturity_level, period}。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    metrics = await org_analytics.get_metrics(db, enterprise_id, period=period)

    response = OrgMetricsResponse(**metrics)
    return success_response(data=response.model_dump(mode="json"))


@router.get("/{enterprise_id}/maturity", response_model=None)
@rate_limit_api()
async def get_maturity(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """成熟度评级详情（L1-L5，MVP 目标 L2，PRD §5.5）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    rating = await org_analytics.get_maturity_level(db, enterprise_id)
    response = MaturityRating(**rating)
    return success_response(data=response.model_dump(mode="json"))


# ============================================================
# Evolution Timeline（聚合建议/指标/优化历史事件）
# ============================================================


@router.get("/{enterprise_id}/timeline", response_model=None)
@rate_limit_api()
async def get_timeline(
    request: Request,
    enterprise_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Evolution Timeline（分页，spec.md §10.7 GET /evolution/{enterprise_id}/timeline）。

    聚合来源（按时间倒序）：
    - AdvisorSuggestion（suggestion_generated / suggestion_applied / suggestion_rejected）
    - OrgMetrics（metric_recorded）
    - OptimizationHistory（optimization_applied）
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    entries: list[TimelineEntry] = []

    # 1. AdvisorSuggestion 事件
    sugg_result = await db.execute(
        select(AdvisorSuggestionModel)
        .where(AdvisorSuggestionModel.enterprise_id == enterprise_id)
        .order_by(AdvisorSuggestionModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    for s in sugg_result.scalars():
        if s.status == "applied":
            event_type = "suggestion_applied"
            ts = s.applied_at or s.created_at
            summary = f"应用建议：{s.title}"
        elif s.status == "rejected":
            event_type = "suggestion_rejected"
            ts = s.created_at
            summary = f"拒绝建议：{s.title}"
        else:
            event_type = "suggestion_generated"
            ts = s.created_at
            summary = f"生成建议：{s.title}"
        entries.append(TimelineEntry(
            timestamp=ts,
            event_type=event_type,
            summary=summary,
            details={"type": s.type, "suggestion_id": s.id},
        ))

    # 2. OrgMetrics 事件（仅 maturity 类型，避免噪音）
    metric_result = await db.execute(
        select(OrgMetricsModel)
        .where(
            OrgMetricsModel.enterprise_id == enterprise_id,
            OrgMetricsModel.metric_type == "maturity",
        )
        .order_by(OrgMetricsModel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    for m in metric_result.scalars():
        value = m.metric_value or {}
        level = value.get("level", "L1") if isinstance(value, dict) else "L1"
        entries.append(TimelineEntry(
            timestamp=m.created_at,
            event_type="metric_recorded",
            summary=f"成熟度评级：{level}",
            details=value if isinstance(value, dict) else {},
        ))

    # 3. OptimizationHistory 事件（联表 Agent 过滤企业）
    opt_result = await db.execute(
        select(OptimizationHistory)
        .join(Agent, OptimizationHistory.agent_id == Agent.id)
        .where(Agent.enterprise_id == enterprise_id)
        .order_by(OptimizationHistory.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    for o in opt_result.scalars():
        entries.append(TimelineEntry(
            timestamp=o.created_at,
            event_type="optimization_applied" if o.applied else "optimization_generated",
            summary=f"优化项（{o.type}）" + ("已应用" if o.applied else "待应用"),
            details={"agent_id": o.agent_id, "type": o.type},
        ))

    # 按时间倒序合并后截断
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    total = len(entries)
    paged = entries[offset: offset + limit]

    response = TimelineResponse(items=paged, total=total)
    return success_response(data=response.model_dump(mode="json"))


# ============================================================
# 持续优化器：历史 / 应用 / 自动回滚检查
# ============================================================


@router.get("/{enterprise_id}/optimizations", response_model=None)
@rate_limit_api()
async def list_optimizations(
    request: Request,
    enterprise_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """企业级优化历史（分页）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    result = await continuous_optimizer.get_enterprise_optimization_history(
        db, enterprise_id, limit=limit, offset=offset
    )
    return success_response(data=result)


@router.post(
    "/{enterprise_id}/optimizations/{agent_id}/apply",
    response_model=None,
)
@rate_limit_admin()
async def apply_optimization(
    request: Request,
    enterprise_id: str,
    agent_id: str,
    optimization_id: str = Query(..., description="OptimizationHistory.id"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """应用一条优化项到 Agent 配置（委托 loop_engine.apply_optimization）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    # 校验 Agent 属于该企业
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None or agent.enterprise_id != enterprise_id:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)

    try:
        result = await continuous_optimizer.apply_optimization(
            db, agent_id, optimization_id, user_id=current_user.id
        )
    except ValueError as e:
        msg = str(e)
        if "not_found" in msg:
            raise HTTPException(
                status_code=404, detail=ErrorCode.OPTIMIZATION_NOT_FOUND
            ) from e
        if "already_applied" in msg:
            raise HTTPException(
                status_code=400, detail=ErrorCode.OPTIMIZATION_ALREADY_APPLIED
            ) from e
        raise HTTPException(
            status_code=400, detail=ErrorCode.OPTIMIZATION_APPLY_FAILED
        ) from e

    await log_audit(
        db, current_user, "apply_optimization", "optimization_history",
        optimization_id,
        request=request,
        details={"agent_id": agent_id, "result": result},
    )

    return success_response(data=result, message="优化项已应用")


@router.post(
    "/{enterprise_id}/rollback-check/{agent_id}",
    response_model=None,
)
@rate_limit_admin()
async def check_and_rollback(
    request: Request,
    enterprise_id: str,
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """RAG 质量退化检查 + 自动回滚（委托 loop_engine.auto_rollback_if_degraded）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None or agent.enterprise_id != enterprise_id:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)

    result = await continuous_optimizer.check_and_auto_rollback(db, agent_id)

    await log_audit(
        db, current_user, "rollback_check", "agent", agent_id,
        request=request, details=result,
    )

    return success_response(
        data=result,
        message="回滚检查已完成"
        + ("（已触发回滚）" if result.get("triggered") else "（未触发回滚）"),
    )
