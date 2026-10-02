"""WT4 持续优化器（PRD §5.4 进化循环 + 重构方案 §7.6 阶段3）。

复用既有 loop_engine（反馈分析）+ rag_evaluator（RAG 评估），
形成"度量 → 分析 → 优化建议 → 应用 → 回滚监控"的闭环：

    ┌───────────────────────────────────────────────────────────┐
    │  Loop Engine（反馈分析）  +  RAG Evaluator（质量评估）     │
    │                            ↓                               │
    │            ContinuousOptimizer.analyze_enterprise          │
    │                            ↓                               │
    │           生成优化项（OptimizationItem）                   │
    │                            ↓                               │
    │        apply_optimization → Agent/RAG 配置变更             │
    │                            ↓                               │
    │      auto_rollback_if_degraded（D4 M14）监控质量           │
    └───────────────────────────────────────────────────────────┘

工程约束（spec §2.2/§2.3）：
- service 层写操作显式 await db.commit()
- 后台任务用 async_session_factory() 独立 session
- LLM 调用前用户输入经 prompt_security.wrap_untrusted 包裹
- 渐进替换：loop_engine 保留为兼容层，本模块仅追加组合能力
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.agent import Agent
from app.models.optimization_history import OptimizationHistory
from app.models.rag_evaluation import RAGEvaluation
from app.services.loop_engine import loop_engine
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


# 优化项类型（与 loop_engine OptimizationHistory.type 对齐）
OPT_TYPE_FEEDBACK = "feedback"
OPT_TYPE_GAP = "gap"
OPT_TYPE_OPTIMIZATION = "optimization"
OPT_TYPE_AUTO_ROLLBACK = "auto_rollback"

# RAG 质量阈值（沿用 loop_engine.AUTO_ROLLBACK_SCORE_THRESHOLD 语义）
RAG_SCORE_THRESHOLD = 0.6


@dataclass
class OptimizationItem:
    """企业级优化项（聚合自 loop_engine + rag_evaluator）。

    type:
        feedback   —— 反馈分析产出的不满意原因与改进
        gap        —— 知识缺口分析产出的补充建议
        optimization —— 检索结果优化建议
        auto_rollback —— 触发自动回滚的质量告警
    """

    agent_id: str
    type: str
    title: str
    detail: dict[str, Any]
    priority: str = "medium"  # high/medium/low
    source: str = "loop_engine"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "type": self.type,
            "title": self.title,
            "detail": self.detail,
            "priority": self.priority,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }


class ContinuousOptimizer:
    """持续优化器。

    Usage::

        optimizer = ContinuousOptimizer()
        report = await optimizer.analyze_enterprise(db, enterprise_id)
        items = report["optimization_items"]
        applied = await optimizer.apply_optimization(db, optimization_id)
    """

    # ========================================================
    # analyze_enterprise：聚合 loop_engine + rag_evaluator
    # ========================================================

    async def analyze_enterprise(
        self,
        db: AsyncSession,
        enterprise_id: str,
        feedback_days: int = 7,
        gap_days: int = 30,
    ) -> dict[str, Any]:
        """分析企业所有 Agent，生成优化项集合。

        Args:
            db: 数据库会话（读为主；写 OptimizationHistory 在独立 session）
            enterprise_id: 企业 ID
            feedback_days: 反馈分析回溯天数
            gap_days: 知识缺口分析回溯天数

        Returns:
            {
                "enterprise_id": ...,
                "analyzed_agents": int,
                "rag_quality": {"avg_composite": float, "degraded_agents": [...]},
                "optimization_items": list[OptimizationItem.to_dict()],
                "generated_at": iso8601,
            }
        """
        agents = await self._list_enterprise_agents(db, enterprise_id)

        items: list[OptimizationItem] = []
        degraded_agents: list[dict[str, Any]] = []
        rag_scores: list[float] = []

        for agent in agents:
            agent_id = agent.id

            # 1. loop_engine 反馈分析（不满意原因）
            feedback_item = await self._safe_analyze_feedback(
                db, agent_id, feedback_days
            )
            if feedback_item is not None:
                items.append(feedback_item)

            # 2. loop_engine 知识缺口分析
            gap_item = await self._safe_analyze_knowledge_gaps(
                db, agent_id, gap_days
            )
            if gap_item is not None:
                items.append(gap_item)

            # 3. rag_evaluator 质量评估
            rag_composite = await self._compute_agent_rag_score(db, agent_id)
            if rag_composite is not None:
                rag_scores.append(rag_composite)
                if rag_composite < RAG_SCORE_THRESHOLD:
                    degraded_agents.append({
                        "agent_id": agent_id,
                        "agent_name": agent.name,
                        "composite": round(rag_composite, 3),
                    })
                    items.append(OptimizationItem(
                        agent_id=agent_id,
                        type=OPT_TYPE_AUTO_ROLLBACK,
                        title=f"Agent {agent.name} RAG 质量退化",
                        detail={
                            "composite": round(rag_composite, 3),
                            "threshold": RAG_SCORE_THRESHOLD,
                            "suggestion": "触发自动回滚检查或补充知识库",
                        },
                        priority="high",
                        source="rag_evaluator",
                    ))

        avg_rag = (sum(rag_scores) / len(rag_scores)) if rag_scores else 0.0

        # 按优先级排序：high > medium > low
        priority_order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda x: priority_order.get(x.priority, 99))

        return {
            "enterprise_id": enterprise_id,
            "analyzed_agents": len(agents),
            "rag_quality": {
                "avg_composite": round(avg_rag, 3),
                "degraded_agents": degraded_agents,
                "threshold": RAG_SCORE_THRESHOLD,
            },
            "optimization_items": [item.to_dict() for item in items],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ========================================================
    # apply_optimization：应用优化项（委托 loop_engine）
    # ========================================================

    async def apply_optimization(
        self,
        db: AsyncSession,
        agent_id: str,
        optimization_id: str,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """应用一条 OptimizationHistory 记录到 Agent 配置。

        委托给 loop_engine.apply_optimization（已实现版本快照 + 按 type 分发应用 + commit）。

        Args:
            db: 数据库会话
            agent_id: Agent ID
            optimization_id: OptimizationHistory.id
            user_id: 操作者（用于版本快照归属）

        Returns:
            loop_engine.apply_optimization 的返回值
        """
        return await loop_engine.apply_optimization(
            db, agent_id, optimization_id, user_id
        )

    # ========================================================
    # check_and_auto_rollback：触发自动回滚检查
    # ========================================================

    async def check_and_auto_rollback(
        self,
        db: AsyncSession,
        agent_id: str,
    ) -> dict[str, Any]:
        """检查 RAG 质量退化并触发自动回滚（委托 loop_engine.auto_rollback_if_degraded）。

        loop_engine 已使用独立 session 执行回滚写操作（spec §2.3）。

        Returns:
            loop_engine.auto_rollback_if_degraded 的返回值
        """
        return await loop_engine.auto_rollback_if_degraded(db, agent_id)

    # ========================================================
    # get_enterprise_optimization_history：企业级优化历史
    # ========================================================

    async def get_enterprise_optimization_history(
        self,
        db: AsyncSession,
        enterprise_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """企业级优化历史（分页，spec §10.7 列表端点契约）。

        Returns:
            {"items": [...], "total": int, "limit": int, "offset": int}
        """
        # 联表 Agent 过滤企业
        count_stmt = (
            select(func.count(OptimizationHistory.id))
            .join(Agent, OptimizationHistory.agent_id == Agent.id)
            .where(Agent.enterprise_id == enterprise_id)
        )
        total = int((await db.execute(count_stmt)).scalar() or 0)

        data_stmt = (
            select(OptimizationHistory)
            .join(Agent, OptimizationHistory.agent_id == Agent.id)
            .where(Agent.enterprise_id == enterprise_id)
            .order_by(OptimizationHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(data_stmt)
        records = result.scalars().all()

        return {
            "items": [
                {
                    "id": r.id,
                    "agent_id": r.agent_id,
                    "type": r.type,
                    "applied": r.applied,
                    "applied_at": r.applied_at.isoformat() if r.applied_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "output_data": r.output_data,
                }
                for r in records
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ========================================================
    # run_enterprise_optimization_background：后台批量分析
    # ========================================================

    async def run_enterprise_optimization_background(
        self,
        enterprise_id: str,
        feedback_days: int = 7,
        gap_days: int = 30,
    ) -> dict[str, Any]:
        """后台执行企业级持续优化分析（spec §2.3 独立 session）。

        供 scheduler_service 定时任务调用，避免阻塞请求。

        Returns:
            analyze_enterprise 的结果
        """
        async with async_session_factory() as db:
            return await self.analyze_enterprise(
                db, enterprise_id, feedback_days, gap_days
            )

    # ========================================================
    # 内部：loop_engine 包装（异常隔离 + 转换为 OptimizationItem）
    # ========================================================

    async def _safe_analyze_feedback(
        self,
        db: AsyncSession,
        agent_id: str,
        days: int,
    ) -> Optional[OptimizationItem]:
        """包装 loop_engine.analyze_feedback，转换为 OptimizationItem。"""
        try:
            result = await loop_engine.analyze_feedback(db, agent_id, days=days)
        except Exception as e:
            errors_total.labels(
                module=__name__, exception_type=type(e).__name__
            ).inc()
            logger.warning(
                "loop_engine.analyze_feedback 失败 agent=%s: %s",
                agent_id, e, exc_info=True,
            )
            return None

        if not result or result.get("total_feedback", 0) == 0:
            return None

        issues = result.get("issues") or []
        if not issues:
            return None

        # 取最高优先级
        priority = self._highest_priority(issues)

        return OptimizationItem(
            agent_id=agent_id,
            type=OPT_TYPE_FEEDBACK,
            title=f"反馈分析：不满意 {result.get('dissatisfied_count', 0)} 条",
            detail={
                "satisfaction_rate": result.get("satisfaction_rate"),
                "dissatisfied_count": result.get("dissatisfied_count"),
                "issues": issues[:5],  # 截断防止过大
            },
            priority=priority,
            source="loop_engine",
        )

    async def _safe_analyze_knowledge_gaps(
        self,
        db: AsyncSession,
        agent_id: str,
        days: int,
    ) -> Optional[OptimizationItem]:
        """包装 loop_engine.analyze_knowledge_gaps，转换为 OptimizationItem。"""
        try:
            result = await loop_engine.analyze_knowledge_gaps(db, agent_id, days=days)
        except Exception as e:
            errors_total.labels(
                module=__name__, exception_type=type(e).__name__
            ).inc()
            logger.warning(
                "loop_engine.analyze_knowledge_gaps 失败 agent=%s: %s",
                agent_id, e, exc_info=True,
            )
            return None

        if not result or result.get("total_questions", 0) == 0:
            return None

        gaps = result.get("gaps") or []
        suggestions = result.get("suggestions") or []
        if not gaps and not suggestions:
            return None

        return OptimizationItem(
            agent_id=agent_id,
            type=OPT_TYPE_GAP,
            title=f"知识缺口：{len(gaps)} 项 / 建议 {len(suggestions)} 项",
            detail={
                "gaps": gaps[:10],
                "suggestions": suggestions[:5],
                "priority_questions": (result.get("priority_questions") or [])[:5],
            },
            priority="medium",
            source="loop_engine",
        )

    # ========================================================
    # 内部：RAG 质量评分（复用 rag_evaluator 落库的数据）
    # ========================================================

    async def _compute_agent_rag_score(
        self,
        db: AsyncSession,
        agent_id: str,
    ) -> Optional[float]:
        """计算 Agent 最近 RAG 评估综合分（avg of 4 dimensions）。

        数据来源：rag_evaluator.evaluate_message 已写入的 RAGEvaluation 记录。
        取最近 7 天的均值作为质量信号。
        """
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        try:
            result = await db.execute(
                select(
                    func.avg(RAGEvaluation.faithfulness),
                    func.avg(RAGEvaluation.answer_relevancy),
                    func.avg(RAGEvaluation.context_precision),
                    func.avg(RAGEvaluation.context_recall),
                ).where(
                    RAGEvaluation.agent_id == agent_id,
                    RAGEvaluation.evaluated_at >= cutoff,
                )
            )
            row = result.fetchone()
            if not row or row[0] is None:
                return None

            composite = (
                float(row[0] or 0)
                + float(row[1] or 0)
                + float(row[2] or 0)
                + float(row[3] or 0)
            ) / 4.0
            return composite
        except Exception as e:
            errors_total.labels(
                module=__name__, exception_type=type(e).__name__
            ).inc()
            logger.warning(
                "计算 RAG 质量分失败 agent=%s: %s", agent_id, e, exc_info=True
            )
            return None

    # ========================================================
    # 内部：辅助
    # ========================================================

    async def _list_enterprise_agents(
        self, db: AsyncSession, enterprise_id: str
    ) -> list[Agent]:
        """列出企业所有 Agent（生产态优先）。"""
        result = await db.execute(
            select(Agent)
            .where(Agent.enterprise_id == enterprise_id)
            .order_by(Agent.lifecycle_stage.asc(), Agent.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    def _highest_priority(issues: list[Any]) -> str:
        """从 issues 列表中提取最高优先级。"""
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        best = "medium"
        best_rank = 99
        for issue in issues:
            if isinstance(issue, dict):
                p = str(issue.get("priority", "medium")).lower()
            else:
                p = "medium"
            r = priority_rank.get(p, 99)
            if r < best_rank:
                best_rank = r
                best = p
        return best


# 模块级单例（无状态，可安全共享）
continuous_optimizer = ContinuousOptimizer()
