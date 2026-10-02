"""Loop RAG 质量评估与自动回滚（evaluation）。

从 ``app/services/loop_engine.py`` 拆分（原 93-97、601-852 行）。D4 M14 能力：
按日聚合 RAGEvaluation 综合分，连续 N 天低于阈值即判定质量退化并回滚到
最近一个可用的知识库快照。

.. warning::
   ``async_session_factory`` 必须保持**函数内延迟导入**。后台任务写 DB 必须使用
   独立 session（项目硬约束），而 ``tests/test_d4_version_management.py`` 通过
   ``monkeypatch.setattr("app.database.async_session_factory", ...)`` 注入测试
   session；一旦提升为模块级导入，该测试的 patch 将失效。
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.optimization_history import OptimizationHistory
# D4 M14: RAG 评估模型用于自动回滚检测
from app.models.rag_evaluation import RAGEvaluation
from app.services.agent_version_service import rollback_knowledge
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

# D4 M14: 自动回滚配置常量
# 连续 N 天 RAG 评分均低于阈值时触发自动回滚
AUTO_ROLLBACK_WINDOW_DAYS = 3
# RAG 评分退化阈值（日均综合分低于此值视为退化）
AUTO_ROLLBACK_SCORE_THRESHOLD = 0.6


class LoopEvaluationMixin:
    """RAG 质量评估与自动回滚（mixin，由 :class:`app.services.loop.engine.LoopEngine` 组装）。"""

    # ============================================================
    # D4 M14: Agent 版本自动回滚
    # ============================================================

    async def auto_rollback_if_degraded(
        self,
        db: AsyncSession,
        agent_id: str,
    ) -> dict:
        """D4 M14: 检测 RAG 质量退化并自动回滚到上一稳定版本。

        触发条件：最近 {AUTO_ROLLBACK_WINDOW_DAYS} 天该 Agent 的 RAG 评估日均综合分
        均低于 {AUTO_ROLLBACK_SCORE_THRESHOLD}（连续退化）。

        流程：
        1. 查询最近 N 天的 RAGEvaluation 记录，按日聚合计算综合分
           综合分 = avg(faithfulness, answer_relevancy, context_precision, context_recall)
        2. 若连续 N 天日均分均低于阈值，判定为质量退化
        3. 找到最近一条 knowledge_snapshot（is_active=False 且 knowledge_snapshot 非空）
        4. 调用 rollback_knowledge 回滚
        5. 创建 OptimizationHistory 记录（type="auto_rollback", applied=True）

        项目硬约束：后台任务写 DB 必须用独立 session（async_session_factory）
        本方法接收的 db 用于读查询；回滚写操作在独立 session 中执行。

        Args:
            db: 用于读查询的 session（可以是请求级 session）
            agent_id: Agent ID

        Returns:
            {
                "triggered": bool,           # 是否触发了回滚
                "reason": str | None,         # 触发原因（未触发时为 None）
                "avg_scores": list[dict],     # 最近 N 天日均分（debug 用）
                "rollback_version_id": str | None,  # 回滚到的版本 ID
            }
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=AUTO_ROLLBACK_WINDOW_DAYS)

        # 1. 查询最近 N 天的 RAG 评估记录，按日聚合
        #    综合分 = avg(faithfulness, answer_relevancy, context_precision, context_recall)
        result = await db.execute(
            select(
                func.date(RAGEvaluation.evaluated_at).label("d"),
                func.avg(RAGEvaluation.faithfulness).label("avg_faith"),
                func.avg(RAGEvaluation.answer_relevancy).label("avg_relevancy"),
                func.avg(RAGEvaluation.context_precision).label("avg_precision"),
                func.avg(RAGEvaluation.context_recall).label("avg_recall"),
                func.count(RAGEvaluation.id).label("cnt"),
            )
            .where(
                and_(
                    RAGEvaluation.agent_id == agent_id,
                    RAGEvaluation.evaluated_at >= cutoff,
                )
            )
            .group_by(func.date(RAGEvaluation.evaluated_at))
            .order_by("d")
        )
        rows = result.fetchall()

        # 构建日均分列表
        avg_scores: list[dict] = []
        for r in rows:
            # 综合分 = 四项指标的算术平均
            composite = (
                float(r.avg_faith or 0)
                + float(r.avg_relevancy or 0)
                + float(r.avg_precision or 0)
                + float(r.avg_recall or 0)
            ) / 4.0
            avg_scores.append({
                "date": str(r.d),
                "composite": round(composite, 4),
                "count": int(r.cnt or 0),
            })

        # 2. 判定是否退化：连续 N 天日均分均低于阈值
        #    要求至少有 N 天的数据，且每一天都低于阈值
        if len(avg_scores) < AUTO_ROLLBACK_WINDOW_DAYS:
            return {
                "triggered": False,
                "reason": f"评估数据不足（{len(avg_scores)}/{AUTO_ROLLBACK_WINDOW_DAYS} 天）",
                "avg_scores": avg_scores,
                "rollback_version_id": None,
            }

        # 取最近 N 天（可能数据超过 N 天，取最近的）
        recent_scores = avg_scores[-AUTO_ROLLBACK_WINDOW_DAYS:]
        all_degraded = all(
            s["composite"] < AUTO_ROLLBACK_SCORE_THRESHOLD for s in recent_scores
        )

        if not all_degraded:
            return {
                "triggered": False,
                "reason": "RAG 评分未持续低于阈值",
                "avg_scores": avg_scores,
                "rollback_version_id": None,
            }

        # 3. 找到最近一条 knowledge_snapshot（用于回滚目标）
        snapshot_result = await db.execute(
            select(AgentVersion)
            .where(
                and_(
                    AgentVersion.agent_id == agent_id,
                    AgentVersion.knowledge_snapshot.isnot(None),
                )
            )
            .order_by(AgentVersion.created_at.desc())
            .limit(1)
        )
        target_snapshot = snapshot_result.scalar_one_or_none()

        if not target_snapshot:
            logger.warning(
                f"auto_rollback_if_degraded: Agent {agent_id} 检测到质量退化，"
                f"但无可回滚的知识库快照"
            )
            # 仍记录一条 OptimizationHistory 标记检测到退化但无快照
            await self._record_auto_rollback_history(
                agent_id,
                triggered=False,
                rollback_version_id=None,
                reason=f"RAG 评分连续 {AUTO_ROLLBACK_WINDOW_DAYS} 天低于 {AUTO_ROLLBACK_SCORE_THRESHOLD}，但无可回滚快照",
                avg_scores=avg_scores,
            )
            return {
                "triggered": False,
                "reason": "无可回滚的知识库快照",
                "avg_scores": avg_scores,
                "rollback_version_id": None,
            }

        # 4. 执行回滚（使用独立 session，项目硬约束）
        #    先查出 Agent 对象（在独立 session 中），再调用 rollback_knowledge
        from app.database import async_session_factory

        rollback_version_id = target_snapshot.id
        try:
            async with async_session_factory() as rollback_db:
                # 在独立 session 中重新加载 Agent（避免跨 session 实例）
                agent_result = await rollback_db.execute(
                    select(Agent).where(Agent.id == agent_id)
                )
                agent = agent_result.scalar_one_or_none()
                if not agent:
                    logger.error(
                        f"auto_rollback_if_degraded: Agent {agent_id} 不存在，无法回滚"
                    )
                    return {
                        "triggered": False,
                        "reason": "Agent 不存在",
                        "avg_scores": avg_scores,
                        "rollback_version_id": None,
                    }

                # 调用 D4 的 rollback_knowledge（内部会 commit）
                await rollback_knowledge(
                    rollback_db,
                    agent,
                    target_snapshot.id,
                    user_id=None,  # 自动回滚无操作者
                )
                # rollback_knowledge 已 commit；OptimizationHistory 写在同一个独立 session
                await self._record_auto_rollback_history_in_session(
                    rollback_db,
                    agent_id,
                    triggered=True,
                    rollback_version_id=rollback_version_id,
                    reason=f"RAG 评分连续 {AUTO_ROLLBACK_WINDOW_DAYS} 天低于 {AUTO_ROLLBACK_SCORE_THRESHOLD}",
                    avg_scores=avg_scores,
                )
        except (SQLAlchemyError, ValueError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(
                f"auto_rollback_if_degraded: 回滚失败（agent={agent_id}）: {e}",
                exc_info=True,
            )
            return {
                "triggered": False,
                "reason": f"回滚失败: {type(e).__name__}",
                "avg_scores": avg_scores,
                "rollback_version_id": None,
            }

        logger.info(
            f"auto_rollback_if_degraded: 已触发自动回滚（agent={agent_id}, "
            f"target_version={target_snapshot.version}）"
        )
        return {
            "triggered": True,
            "reason": f"RAG 评分连续 {AUTO_ROLLBACK_WINDOW_DAYS} 天低于 {AUTO_ROLLBACK_SCORE_THRESHOLD}",
            "avg_scores": avg_scores,
            "rollback_version_id": rollback_version_id,
        }

    async def _record_auto_rollback_history(
        self,
        agent_id: str,
        triggered: bool,
        rollback_version_id: Optional[str],
        reason: str,
        avg_scores: list[dict],
    ) -> None:
        """在独立 session 中记录自动回滚检测的 OptimizationHistory（回滚未触发场景）。

        当检测到退化但无法回滚（无快照/Agent 不存在）时调用此方法。
        回滚已触发的场景使用 _record_auto_rollback_history_in_session（复用回滚 session）。
        """
        from app.database import async_session_factory
        async with async_session_factory() as hist_db:
            await self._record_auto_rollback_history_in_session(
                hist_db,
                agent_id,
                triggered=triggered,
                rollback_version_id=rollback_version_id,
                reason=reason,
                avg_scores=avg_scores,
            )

    async def _record_auto_rollback_history_in_session(
        self,
        db: AsyncSession,
        agent_id: str,
        triggered: bool,
        rollback_version_id: Optional[str],
        reason: str,
        avg_scores: list[dict],
    ) -> None:
        """在给定 session 中写入 OptimizationHistory 记录。"""
        history = OptimizationHistory(
            agent_id=agent_id,
            user_id=None,
            type="auto_rollback",
            input_data={
                "window_days": AUTO_ROLLBACK_WINDOW_DAYS,
                "score_threshold": AUTO_ROLLBACK_SCORE_THRESHOLD,
                "avg_scores": avg_scores,
            },
            output_data={
                "triggered": triggered,
                "rollback_version_id": rollback_version_id,
                "reason": reason,
            },
            applied=triggered,
            applied_at=datetime.now(timezone.utc) if triggered else None,
        )
        db.add(history)
        await db.commit()
