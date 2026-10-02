import chromadb.errors
import httpx
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.user import User
from app.models.rag_evaluation import RAGEvaluation
from app.models.agent_version import AgentVersion
from app.models.optimization_history import OptimizationHistory
from app.services.loop_engine import loop_engine
from app.services.rag_evaluator import rag_evaluator
from app.utils.security import get_current_user
from app.utils.rbac import require_admin
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api, rate_limit_admin
from app.utils.audit import log_audit
from app.utils.metrics import errors_total
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/loop", tags=["Loop Engineering"])


class OptimizeRequest(BaseModel):
    # P0-DoS: 限制反馈分析输入长度，防止超大 payload 耗尽 LLM token 预算
    # 8K 字符 ≈ 2K tokens，足够描述一个优化诉求
    query: str = Field(..., max_length=8000)
    feedback: str = Field(..., max_length=8000)


async def _verify_agent_access(
    db: AsyncSession, agent_id: str, current_user: User
) -> Agent:
    """P0-01: 校验 Agent 存在且归属当前用户的企业。

    普通企业用户：必须匹配 enterprise_id；
    超级管理员（enterprise_id=None）：放行。
    """
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    query = select(Agent).where(Agent.id == agent_id)
    if current_user.enterprise_id is not None:
        query = query.where(Agent.enterprise_id == current_user.enterprise_id)
    result = await db.execute(query)
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.LOOP_AGENT_NOT_FOUND)
    return agent


@router.post("/{agent_id}/optimize")
@rate_limit_api()
async def optimize_retrieval(
    request: Request,
    agent_id: str,
    data: OptimizeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # P0-01/P0-05: 校验 agent 归属
    await _verify_agent_access(db, agent_id, current_user)
    try:
        result = await loop_engine.optimize_retrieval(
            db, agent_id, data.query, data.feedback
        )
        # P1/P2-INFRA: 记录检索优化审计日志
        await log_audit(
            db, current_user, "optimize_retrieval", "agent", agent_id,
            request=request,
            details={"query": data.query},
        )
        await db.commit()
        return success_response(result)
    except ValueError as e:
        logger.warning(f"优化检索参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.OPTIMIZE_RETRIEVAL_INVALID) from e
    except SQLAlchemyError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"优化检索数据库异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
    except (httpx.HTTPError, RuntimeError, OSError, TypeError, chromadb.errors.ChromaError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"优化检索失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.get("/{agent_id}/status")
@rate_limit_api()
async def get_loop_status(
    request: Request,
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # P0-01: 校验 agent 归属
    await _verify_agent_access(db, agent_id, current_user)
    try:
        result = await loop_engine.get_loop_status(agent_id, db)
        return success_response(result)
    except ValueError as e:
        logger.warning(f"获取状态参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.LOOP_STATUS_INVALID) from e
    except SQLAlchemyError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"获取状态数据库异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
    except (httpx.HTTPError, RuntimeError, OSError, TypeError, chromadb.errors.ChromaError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"获取状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.get("/{agent_id}/stats")
@rate_limit_api()
async def get_loop_stats(
    request: Request,
    agent_id: str,
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P0-1b: 聚合监控统计数据（对话趋势/响应时间/满意度/最近会话/错误日志/告警）。"""
    # P0-01: 校验 agent 归属
    await _verify_agent_access(db, agent_id, current_user)
    try:
        result = await loop_engine.get_loop_stats(db, agent_id, days)
        return success_response(result)
    except ValueError as e:
        logger.warning(f"获取监控统计参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.LOOP_STATS_INVALID) from e
    except SQLAlchemyError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"获取监控统计数据库异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
    except (httpx.HTTPError, RuntimeError, OSError, TypeError, chromadb.errors.ChromaError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"获取监控统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.get("/{agent_id}/insights")
@rate_limit_api()
async def get_loop_insights(
    request: Request,
    agent_id: str,
    days: int = Query(default=30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P1-LOOP: 获取 LoopDashboard 优化历史页所需综合洞察。

    包含：
    - 优化历史（feedback/gap/optimization）
    - Agent 版本快照（按 created_at 升序，便于 v1→v2→v3 展示）
    - RAG 自动评估趋势（faithfulness/answer_relevancy/context_precision/context_recall）
    - 反馈分析与知识缺口（R5: 原 /feedback、/knowledge-gaps 聚合至此）
    """
    await _verify_agent_access(db, agent_id, current_user)
    try:
        # 1. 优化历史
        opt_result = await db.execute(
            select(OptimizationHistory)
            .where(OptimizationHistory.agent_id == agent_id)
            .order_by(OptimizationHistory.created_at.desc())
            .limit(50)
        )
        optimizations = [o.to_dict() for o in opt_result.scalars().all()]

        # 2. 版本快照
        ver_result = await db.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.created_at.asc())
            .limit(20)
        )
        versions = [v.to_dict() for v in ver_result.scalars().all()]

        # 3. RAG 评估趋势
        rag_trend = await rag_evaluator.get_agent_trend(db, agent_id, days)

        # 4. 最新 RAG 评分
        latest_result = await db.execute(
            select(RAGEvaluation)
            .where(RAGEvaluation.agent_id == agent_id)
            .order_by(RAGEvaluation.evaluated_at.desc())
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()

        # 5. 反馈分析与知识缺口（R5: 聚合原废弃端点数据）
        feedback_analysis = await loop_engine.analyze_feedback(db, agent_id, days)
        knowledge_gaps = await loop_engine.analyze_knowledge_gaps(db, agent_id, days)

        return success_response({
            "agent_id": agent_id,
            "days": days,
            "optimizations": optimizations,
            "versions": versions,
            "rag_trend": rag_trend,
            "rag_latest": latest.to_dict() if latest else None,
            "feedback_analysis": feedback_analysis,
            "knowledge_gaps": knowledge_gaps,
        })
    except ValueError as e:
        logger.warning(f"获取 Loop 洞察参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.LOOP_INSIGHTS_INVALID) from e
    except SQLAlchemyError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"获取 Loop 洞察数据库异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
    except (httpx.HTTPError, RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"获取 Loop 洞察失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.post("/{agent_id}/apply/{optimization_id}")
@rate_limit_admin()
async def apply_optimization(
    request: Request,
    agent_id: str,
    optimization_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """O-08: 应用优化记录到 Agent 配置，打通 Loop 闭环。

    仅管理员可执行：应用前会创建 Agent 版本快照（可回滚），
    随后按优化类型（feedback/gap/optimization）应用具体变更并标记 applied=True。

    返回：{applied, optimization_id, version_snapshot_id, agent_version, summary}
    """
    # P0-01: 校验 agent 归属（管理员跨企业放行）
    await _verify_agent_access(db, agent_id, current_user)
    try:
        result = await loop_engine.apply_optimization(
            db, agent_id, optimization_id, user_id=current_user.id
        )
        # 记录审计日志（apply_optimization 内部已 commit 业务数据，此处提交审计记录）
        await log_audit(
            db, current_user, "apply_optimization", "agent", agent_id,
            request=request,
            details={
                "optimization_id": optimization_id,
                "optimization_type": "unknown",
                "version_snapshot_id": result.get("version_snapshot_id"),
                "agent_version": result.get("agent_version"),
            },
        )
        await db.commit()
        return success_response(result)
    except ValueError as e:
        msg = str(e)
        if msg == "optimization_not_found":
            raise HTTPException(status_code=404, detail=ErrorCode.OPTIMIZATION_NOT_FOUND) from e
        elif msg == "optimization_already_applied":
            raise HTTPException(status_code=409, detail=ErrorCode.OPTIMIZATION_ALREADY_APPLIED) from e
        logger.warning(f"应用优化参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.OPTIMIZATION_APPLY_FAILED) from e
    except SQLAlchemyError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"应用优化数据库异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
