"""WT3 Workforce API 路由（spec.md §10.7 WT3 部分）。

端点（前缀 ``/api/v1/workforce``，全部无尾斜杠）：
- POST   /generate                                 触发生成（返回推荐岗位列表）
- POST   /confirm                                   确认推荐（批量创建 Agent）
- GET    /{enterprise_id}                           列出企业 Workforce（分页）
- GET    /{agent_id}/lifecycle                      查询 Agent 生命周期状态 + 历史
- POST   /{agent_id}/transition                     Agent 阶段转换
- GET    /{agent_id}/memory/{conversation_id}        查询 Agent 三层记忆

工程约束（spec §2.1 / §2.2）：
- 无尾斜杠（路径用 "/{...}" 而非 "/{...}/"）
- 列表端点含 limit/offset 分页（Query(ge=1, le=100) / Query(ge=0)）
- 鉴权：get_current_user；企业隔离 + 管理员校验
- 限流：rate_limit_api / rate_limit_admin
- 企业相关响应对非管理员排除敏感字段（此处主要体现为 system_prompt 不在响应中）
- 错误响应统一使用 ErrorCode 常量
- LLM 调用前用户输入经 prompt_security.wrap_untrusted 包裹（在 service 层已实现）
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.agent_kpi import AgentKPI
from app.models.collaboration import CollaborationEvent
from app.models.enterprise import Enterprise
from app.models.user import User
from app.schemas.workforce import (
    GenerateRequest,
    ConfirmRequest,
    TransitionRequest,
    AgentRunMetrics,
)
from app.services.workforce.generator import WorkforceGenerator
from app.services.workforce.lifecycle_manager import LifecycleManager
from app.services.memory.memory_manager import MemoryManager
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user
from app.utils.audit import log_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workforce", tags=["AI Workforce"])

# 复用的服务实例（无状态，可安全共享）
_generator = WorkforceGenerator()
_lifecycle_mgr = LifecycleManager()
_memory_mgr = MemoryManager()


# ============================================================
# 权限校验
# ============================================================


def _verify_enterprise_access(current_user: User, enterprise_id: str) -> None:
    """校验当前用户属于目标企业（或为系统超管）。

    - 系统超管（enterprise_id is None 且 role == 'admin'）：放行
    - 企业成员（enterprise_id 匹配）：放行
    - 其他：403
    """
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


async def _ensure_enterprise_exists(db: AsyncSession, enterprise_id: str) -> Enterprise:
    """校验企业存在且未软删除，返回 Enterprise。"""
    result = await db.execute(select(Enterprise).where(Enterprise.id == enterprise_id))
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)
    if not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_DELETED)
    return enterprise


async def _load_agent_with_access(
    db: AsyncSession,
    current_user: User,
    agent_id: str,
) -> Agent:
    """加载 Agent 并校验当前用户对其企业有访问权。

    - Agent 不存在 → 404 AGENT_NOT_FOUND
    - 用户非该企业成员/非超管 → 403
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    _verify_enterprise_access(current_user, agent.enterprise_id)
    return agent


def _build_agent_metrics(agent: Agent) -> AgentRunMetrics:
    """从 Agent 记录构建运行指标骨架（不含统计值）。

    统计值由 _attach_real_metrics 从 AgentKPI / Message / CollaborationEvent
    真实聚合后填充，见该函数说明。
    """
    return AgentRunMetrics(
        agent_id=agent.id,
        agent_name=agent.name,
        position=agent.position_id or "",
        lifecycle_stage=agent.lifecycle_stage or "recruit",
        tasks_total=0,
        tasks_completed=0,
        tasks_failed=0,
        avg_response_time_ms=0.0,
        avg_satisfaction=0.0,
        kpi_performance={},
        tool_usage={},
        last_active_at=agent.updated_at,
    )


async def _attach_real_metrics(
    db: AsyncSession, enterprise_id: str, metrics: list[AgentRunMetrics]
) -> list[AgentRunMetrics]:
    """用真实数据填充员工运行指标（UI v4 §一 罪二）。

    背景：此前 _build_agent_metrics 把 tasks_total / avg_satisfaction 等
    全部写死为 0，导致「AI 员工」页所有指标恒为 0、满意度恒为「暂无」，
    前端看起来像功能没做，实则是后端从未聚合。

    数据来源（均为真实业务数据，无兜底假值）：
    - AgentKPI：任务数、响应时长、满意度（0-5）、准确率
    - CollaborationEvent：该 Agent 参与的协作事件成败计数

    无 KPI 记录的 Agent 保持 0 值 —— 诚实反映「尚未开始工作」。
    """
    if not metrics:
        return metrics

    agent_ids = [m.agent_id for m in metrics]

    # —— KPI 聚合：按 agent 汇总任务数与加权满意度 ——
    kpi_rows = await db.execute(
        select(
            AgentKPI.agent_id,
            func.sum(AgentKPI.task_count),
            func.avg(AgentKPI.avg_response_time),
            func.avg(AgentKPI.satisfaction_score),
            func.avg(AgentKPI.accuracy),
            func.avg(AgentKPI.first_resolution_rate),
        )
        .where(
            AgentKPI.enterprise_id == enterprise_id,
            AgentKPI.agent_id.in_(agent_ids),
        )
        .group_by(AgentKPI.agent_id)
    )
    kpi_map: dict[str, dict] = {}
    for aid, task_count, resp_ms, satisfaction, accuracy, resolution in kpi_rows:
        kpi_map[aid] = {
            "task_count": int(task_count or 0),
            "avg_response_time": float(resp_ms or 0.0),
            "satisfaction": float(satisfaction or 0.0),
            "accuracy": float(accuracy or 0.0),
            "first_resolution_rate": float(resolution or 0.0),
        }

    # —— 协作事件：按 Agent 统计成功/失败，补足 KPI 未覆盖的执行情况 ——
    evt_rows = await db.execute(
        select(
            CollaborationEvent.source_agent_id,
            CollaborationEvent.status,
            func.count(CollaborationEvent.id),
        )
        .where(
            CollaborationEvent.enterprise_id == enterprise_id,
            CollaborationEvent.source_agent_id.in_(agent_ids),
        )
        .group_by(CollaborationEvent.source_agent_id, CollaborationEvent.status)
    )
    evt_map: dict[str, dict[str, int]] = {}
    for aid, status, count in evt_rows:
        bucket = evt_map.setdefault(aid, {"total": 0, "failed": 0})
        bucket["total"] += int(count or 0)
        if status in ("failed", "error"):
            bucket["failed"] += int(count or 0)

    for m in metrics:
        kpi = kpi_map.get(m.agent_id)
        evt = evt_map.get(m.agent_id, {"total": 0, "failed": 0})

        # 任务量：优先 KPI 记录，缺失时用协作事件数兜底（两者都是真实计数）
        total = (kpi or {}).get("task_count", 0) or evt["total"]
        failed = evt["failed"]
        m.tasks_total = total
        m.tasks_failed = failed
        m.tasks_completed = max(0, total - failed)

        if kpi:
            m.avg_response_time_ms = round(kpi["avg_response_time"], 2)
            # 0-5 分制，与 AgentKPI.satisfaction_score 口径一致
            m.avg_satisfaction = round(kpi["satisfaction"], 2)
            m.kpi_performance = {
                "accuracy": round(kpi["accuracy"], 4),
                "first_resolution_rate": round(kpi["first_resolution_rate"], 4),
            }

    return metrics


# ============================================================
# POST /generate — 触发生成（返回推荐岗位列表）
# ============================================================


@router.post("/generate", response_model=None)
@rate_limit_admin()
async def generate_workforce(
    request: Request,
    data: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """触发 AI 员工 6 步生成流程的前 4 步（PRD §5.11）。

    步骤 1-4：岗位提取 → 优先级排序 → Agent 配置生成 → 推荐展示。
    返回推荐岗位列表（含 6 维度匹配评分），用户确认后调用 /confirm 创建。
    """
    await _ensure_enterprise_exists(db, data.enterprise_id)
    _verify_enterprise_admin(current_user, data.enterprise_id)

    result = await _generator.generate(db, data.enterprise_id)

    await log_audit(
        db, current_user, "generate", "workforce", data.enterprise_id,
        request=request,
        details={"recommended_count": result.get("total", 0)},
    )

    return success_response(
        data=result,
        message=f"已生成 {result.get('total', 0)} 个推荐岗位",
    )


# ============================================================
# POST /confirm — 确认推荐（批量创建 Agent）
# ============================================================


@router.post("/confirm", response_model=None)
@rate_limit_admin()
async def confirm_workforce(
    request: Request,
    data: ConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """确认推荐岗位并批量创建 Agent（PRD §5.11 步骤 5-6）。

    步骤 5-6：用户确认 → 正式创建（注入配置，状态设为 recruit）。
    返回成功创建的 Agent 列表与失败原因。
    """
    await _ensure_enterprise_exists(db, data.enterprise_id)
    _verify_enterprise_admin(current_user, data.enterprise_id)

    result = await _generator.confirm(
        db,
        data.enterprise_id,
        data.confirmed_position_ids,
        adjustments=data.adjustments,
    )

    await log_audit(
        db, current_user, "confirm", "workforce", data.enterprise_id,
        request=request,
        details={
            "created": len(result.get("created_agents", [])),
            "failed": len(result.get("failed", [])),
        },
    )

    return success_response(
        data=result,
        message=(
            f"已创建 {len(result.get('created_agents', []))} 个 AI 员工，"
            f"失败 {len(result.get('failed', []))} 个"
        ),
    )


# ============================================================
# GET /{enterprise_id} — 列出企业 Workforce（分页）
# ============================================================


@router.get("/{enterprise_id}", response_model=None)
@rate_limit_api()
async def list_workforce(
    request: Request,
    enterprise_id: str,
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出企业的 AI 员工（分页，spec.md §10.7）。

    响应中仅含运行指标，不含 system_prompt / 权限等敏感字段（对管理员与
    成员一致），符合 spec §2.1「企业相关响应对非管理员排除敏感字段」。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    items, total = await _lifecycle_mgr.list_workforce(
        db, enterprise_id, limit=limit, offset=offset
    )

    metrics = [_build_agent_metrics(a) for a in items]
    # 用真实 KPI / 协作事件填充指标（此前全部写死为 0）
    metrics = await _attach_real_metrics(db, enterprise_id, metrics)

    return success_response(
        data={
            "items": [m.model_dump(mode="json") for m in metrics],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


# ============================================================
# GET /{enterprise_id}/orchestration — 编排能力摘要（P0-2）
# ============================================================


@router.get("/{enterprise_id}/orchestration", response_model=None)
@rate_limit_api()
async def get_orchestration(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回企业 AI 劳动力的编排能力摘要（产品完善方案 P0-2）。

    让「AI 劳动力编排器」从代码变成可见能力：展示 5 种执行方式与
    MVP 当前默认执行方式，供协作工作台展示「任务如何执行」徽章。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    from app.services.workforce.orchestrator import ExecutionMethod, TaskComplexity

    methods = [
        {
            "key": m.value,
            "label": {
                "script": "脚本自动化",
                "workflow": "工作流引擎",
                "skill_model": "Skill + 模型",
                "single_agent": "单 Agent",
                "multi_agent": "多 Agent 编排",
            }[m.value],
            "status": "active" if m == ExecutionMethod.SINGLE_AGENT else "roadmap",
        }
        for m in ExecutionMethod
    ]

    return success_response(
        data={
            "methods": methods,
            "default_method": ExecutionMethod.SINGLE_AGENT.value,
            "default_method_label": "单 Agent",
            "complexity": [c.value for c in TaskComplexity],
        },
        message="编排能力摘要",
    )


# ============================================================
# GET /{agent_id}/lifecycle — 查询 Agent 生命周期状态 + 历史
# ============================================================


@router.get("/{agent_id}/lifecycle", response_model=None)
@rate_limit_api()
async def get_lifecycle(
    request: Request,
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询 Agent 的生命周期状态与历史轨迹（PRD §5.6）。"""
    await _load_agent_with_access(db, current_user, agent_id)

    try:
        result = await _lifecycle_mgr.get_lifecycle(db, agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND) from None

    return success_response(data=result)


# ============================================================
# POST /{agent_id}/transition — Agent 阶段转换
# ============================================================


@router.post("/{agent_id}/transition", response_model=None)
@rate_limit_admin()
async def transition_stage(
    request: Request,
    agent_id: str,
    data: TransitionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """触发 Agent 生命周期阶段转换（PRD §5.6）。

    MVP 3 阶段：recruit → training → production。
    转换条件由 LifecycleManager 校验（system_prompt/知识库注入等）。
    """
    agent = await _load_agent_with_access(db, current_user, agent_id)
    # 阶段转换需企业管理员权限
    _verify_enterprise_admin(current_user, agent.enterprise_id)

    try:
        result = await _lifecycle_mgr.transition(
            db, agent_id, data.target_stage, reason=data.reason
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST) from None

    await log_audit(
        db, current_user, "transition", "agent", agent_id,
        request=request,
        details={
            "new_stage": result.get("new_stage"),
            "reason": data.reason,
        },
    )

    return success_response(
        data=result,
        message=f"阶段已转换为 {result.get('new_stage')}",
    )


# ============================================================
# GET /{agent_id}/memory/{conversation_id} — 查询 Agent 三层记忆
# ============================================================


@router.get("/{agent_id}/memory/{conversation_id}", response_model=None)
@rate_limit_api()
async def query_memory(
    request: Request,
    agent_id: str,
    conversation_id: str,
    current_task: Optional[str] = Query(None, description="当前任务（用于语义检索长期记忆）"),
    top_k: int = Query(5, ge=1, le=20, description="长期记忆检索结果数"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询 Agent 的三层记忆（PRD §5.12）。

    返回结构：{short_term: [...], entity: [...], long_term: [...]}
    - short_term：当前对话上下文（最近 20 轮）
    - entity：Agent 关联的实体记忆（客户/商机/产品）
    - long_term：语义检索的交互摘要
    """
    agent = await _load_agent_with_access(db, current_user, agent_id)

    result = await _memory_mgr.retrieve(
        db,
        agent_id=agent_id,
        enterprise_id=agent.enterprise_id,
        conversation_id=conversation_id,
        current_task=current_task,
        top_k=top_k,
    )

    return success_response(data=result)
