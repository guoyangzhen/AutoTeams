"""影子模式 API 路由（愿景蓝图 §4.1.1 + 产品完善方案_v3.2 补1）。

端点（前缀 ``/api/v1/shadow``，全部无尾斜杠）：
- GET    /tasks                            影子任务列表（分页 + 状态过滤）
- POST   /tasks                            创建影子任务（记录问题 + 真人基线）
- GET    /tasks/summary                    阶段汇总（信任度佐证）
- POST   /tasks/{task_id}/record-ai        记录 AI 回答（shadowing → evaluating）
- POST   /tasks/{task_id}/generate-ai      让真实 Agent 作答（UI v4，替代前端伪造）
- POST   /tasks/{task_id}/evaluate         评估 AI vs 真人（evaluating → qualified）
- POST   /tasks/{task_id}/promote          晋升（qualified → autonomous）
- POST   /tasks/{task_id}/demote           降级（autonomous → evaluating / qualified → shadowing）

工程约束（对齐 spec §2.1 / §2.2 / §2.6）：
- 无尾斜杠；列表端点含 limit/offset 分页
- 鉴权：get_current_user；企业隔离 + 管理员校验
- 限流：rate_limit_api / rate_limit_admin
- 错误响应统一使用 ErrorCode 常量
"""
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.shadow import ShadowTask
from app.models.user import User
from app.schemas.shadow import (
    CreateShadowTaskRequest,
    EvaluateTaskRequest,
    RecordAiAnswerRequest,
    ShadowTaskListResponse,
    ShadowTaskView,
)
from app.services.llm_service import llm_service, ModelTier
from app.services.prompt_security import wrap_untrusted, SYSTEM_PROMPT_GUARDRAIL
from app.services.shadow import state_machine
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shadow", tags=["Shadow"])


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


async def _get_task_or_404(
    db: AsyncSession, task_id: str
) -> ShadowTask:
    task = await state_machine.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    return task


def _to_view(task: ShadowTask) -> ShadowTaskView:
    return ShadowTaskView(
        id=task.id,
        enterprise_id=task.enterprise_id,
        agent_id=task.agent_id,
        task_type=task.task_type,
        question=task.question,
        human_answer=task.human_answer,
        ai_answer=task.ai_answer,
        confidence=task.confidence,
        status=task.status,
        eval_result=task.eval_result or "pending",
        promoted_at=task.promoted_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


# ============================================================
# 任务创建 / 查询
# ============================================================


@router.post("/tasks", response_model=None)
@rate_limit_api()
async def create_task(
    request: Request,
    data: CreateShadowTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建影子任务（记录问题 + 真人基线，shadowing 阶段）。"""
    await _ensure_enterprise_exists(db, data.enterprise_id)
    _verify_enterprise_access(current_user, data.enterprise_id)

    if data.agent_id:
        agent_result = await db.execute(
            select(Agent).where(Agent.id == data.agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None or agent.enterprise_id != data.enterprise_id:
            raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)

    task = ShadowTask(
        enterprise_id=data.enterprise_id,
        agent_id=data.agent_id,
        task_type=data.task_type,
        question=data.question,
        human_answer=data.human_answer,
        status="shadowing",
        eval_result="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    await log_audit(
        db, current_user, "create_shadow_task", "shadow_task", task.id,
        request=request,
        details={"enterprise_id": data.enterprise_id,
                 "task_type": data.task_type},
    )

    return success_response(data=_to_view(task).model_dump(mode="json"))


@router.get("/tasks", response_model=None)
@rate_limit_api()
async def list_tasks(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    agent_id: Optional[str] = Query(None, description="按 Agent 过滤"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """影子任务列表（分页 + 状态 / Agent 过滤）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    if agent_id:
        items, total = await state_machine.list_tasks_by_agent(
            db, enterprise_id, agent_id, limit, offset
        )
    else:
        items, total = await state_machine.list_tasks(
            db, enterprise_id, status, limit, offset
        )

    response = ShadowTaskListResponse(
        items=[_to_view(t) for t in items],
        total=total,
    )
    return success_response(data=response.model_dump(mode="json"))


@router.get("/tasks/summary", response_model=None)
@rate_limit_api()
async def get_summary(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """影子模式阶段汇总（各阶段任务数 / 匹配数 / 平均置信度）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    data = await state_machine.summary(db, enterprise_id)
    return success_response(data=data)


# ============================================================
# 状态机转换
# ============================================================


@router.post("/tasks/{task_id}/record-ai", response_model=None)
@rate_limit_admin()
async def record_ai_answer(
    request: Request,
    task_id: str,
    data: RecordAiAnswerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """记录 AI 回答 → shadowing → evaluating。"""
    task = await _get_task_or_404(db, task_id)
    _verify_enterprise_admin(current_user, task.enterprise_id)

    try:
        state_machine.apply_transition(task, "record_ai")
    except state_machine.ShadowStateError:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST) from None

    task.ai_answer = data.ai_answer
    task.confidence = data.confidence
    await db.commit()
    await db.refresh(task)

    await log_audit(
        db, current_user, "record_ai_shadow", "shadow_task", task.id,
        request=request,
        details={"task_id": task_id, "confidence": data.confidence},
    )

    return success_response(data=_to_view(task).model_dump(mode="json"))


@router.post("/tasks/{task_id}/generate-ai", response_model=None)
@rate_limit_admin()
async def generate_ai_answer(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """让真实 Agent 对该影子任务作答（UI v4 §六 战场三）。

    修复：前端此前用 `AI 对「{问题}」的模拟回答` 直接编造答案写库，
    污染了影子模式的核心资产 —— 人机对照样本。影子模式的全部价值
    就在于「AI 答案 vs 真人答案」可比对，样本一旦造假则整个信任
    建立机制失去意义。

    本端点调用真实 LLM 生成回答，并让模型自评置信度。
    LLM 不可用时返回 503 且不写库 —— 宁可没有答案，也不要假答案。
    """
    task = await _get_task_or_404(db, task_id)
    _verify_enterprise_admin(current_user, task.enterprise_id)

    try:
        state_machine.apply_transition(task, "record_ai")
    except state_machine.ShadowStateError:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST) from None

    # 载入该任务归属的 Agent，用其身份与职责作答
    agent_name = "AI 员工"
    agent_desc = ""
    if task.agent_id:
        ar = await db.execute(select(Agent).where(Agent.id == task.agent_id))
        agent = ar.scalar_one_or_none()
        if agent:
            agent_name = agent.name
            agent_desc = agent.description or ""

    system_prompt = (
        f"你是企业中的 AI 员工「{agent_name}」。{agent_desc}\n"
        f"当前处于影子学习阶段：你需要独立回答业务问题，"
        f"你的回答将与资深同事的处理方式做对照，用于评估你是否可以独立上岗。\n"
        "要求：直接给出专业、可执行的答复，控制在 200 字以内；"
        "最后单独一行输出 `CONFIDENCE: 0.xx` 表示你对本次回答的置信度。"
    )
    user_prompt = wrap_untrusted(task.question, "业务问题")

    try:
        raw = await llm_service.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL + "\n" + system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.STRONG,
            max_tokens=800,
        )
    except Exception as e:
        logger.error("影子任务 AI 作答失败: task=%s error=%s", task_id, e, exc_info=True)
        # 不写入任何内容 —— 宁可没有答案，也不要假答案
        raise HTTPException(
            status_code=503,
            detail="AI 服务当前不可用，无法生成真实回答。请稍后重试或检查模型配置。",
        ) from e

    answer, confidence = _split_answer_confidence(raw)
    if not answer:
        raise HTTPException(status_code=503, detail="AI 返回内容为空，未写入样本")

    task.ai_answer = answer
    task.confidence = confidence
    await db.commit()
    await db.refresh(task)

    await log_audit(
        db, current_user, "generate_ai_shadow", "shadow_task", task.id,
        request=request,
        details={"task_id": task_id, "confidence": confidence},
    )

    return success_response(
        data=_to_view(task).model_dump(mode="json"),
        message="AI 已完成作答",
    )


def _split_answer_confidence(raw: str) -> tuple[str, float]:
    """从 LLM 输出中分离答案正文与自评置信度。

    约定末行为 `CONFIDENCE: 0.xx`；缺失或不合法时置信度回退 0.5
    （表示「未知」而非「高可信」，避免虚高误导人工评估）。
    """
    text = (raw or "").strip()
    confidence = 0.5
    lines = text.splitlines()
    if lines:
        last = lines[-1].strip()
        match = re.match(r"^CONFIDENCE\s*[:：]\s*([0-9]*\.?[0-9]+)$", last, re.IGNORECASE)
        if match:
            try:
                confidence = max(0.0, min(1.0, float(match.group(1))))
            except ValueError:
                confidence = 0.5
            lines = lines[:-1]
    return "\n".join(lines).strip(), confidence


@router.post("/tasks/{task_id}/evaluate", response_model=None)
@rate_limit_admin()
async def evaluate_task(
    request: Request,
    task_id: str,
    data: EvaluateTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """评估 AI vs 真人 → 命中则（可选）晋升，否则保持 evaluating。"""
    task = await _get_task_or_404(db, task_id)
    _verify_enterprise_admin(current_user, task.enterprise_id)

    try:
        # 先从 evaluating 走到 qualified（若合法）
        state_machine.apply_transition(task, "evaluate")
    except state_machine.ShadowStateError:
        # 非 evaluating 状态也允许评估（标记结果），但不做晋升
        pass

    task.eval_result = "match" if data.match else "mismatch"
    # 命中且 auto_qualify 时晋升为 qualified
    if data.match and not data.auto_qualify:
        task.status = "evaluating"
    if data.match and data.auto_qualify:
        task.status = "qualified"
    await db.commit()
    await db.refresh(task)

    await log_audit(
        db, current_user, "evaluate_shadow", "shadow_task", task.id,
        request=request,
        details={"task_id": task_id, "match": data.match,
                 "auto_qualify": data.auto_qualify},
    )

    return success_response(data=_to_view(task).model_dump(mode="json"))


@router.post("/tasks/{task_id}/promote", response_model=None)
@rate_limit_admin()
async def promote_task(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """晋升 qualified → autonomous。"""
    task = await _get_task_or_404(db, task_id)
    _verify_enterprise_admin(current_user, task.enterprise_id)

    try:
        state_machine.apply_transition(task, "promote")
    except state_machine.ShadowStateError:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST) from None

    from app.utils.time import utcnow
    task.promoted_at = utcnow()
    await db.commit()
    await db.refresh(task)

    await log_audit(
        db, current_user, "promote_shadow", "shadow_task", task.id,
        request=request, details={"task_id": task_id},
    )

    return success_response(data=_to_view(task).model_dump(mode="json"))


@router.post("/tasks/{task_id}/demote", response_model=None)
@rate_limit_admin()
async def demote_task(
    request: Request,
    task_id: str,
    target: str = Query("evaluating", description="降级目标：evaluating / shadowing"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """降级：autonomous → evaluating（异常）或 qualified → shadowing（重训）。"""
    task = await _get_task_or_404(db, task_id)
    _verify_enterprise_admin(current_user, task.enterprise_id)

    action = "demote_evaluate" if target == "evaluating" else "demote_shadow"
    try:
        state_machine.apply_transition(task, action)
    except state_machine.ShadowStateError:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST) from None

    task.eval_result = "pending"
    await db.commit()
    await db.refresh(task)

    await log_audit(
        db, current_user, "demote_shadow", "shadow_task", task.id,
        request=request, details={"task_id": task_id, "target": target},
    )

    return success_response(data=_to_view(task).model_dump(mode="json"))
