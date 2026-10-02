"""业务效果指标聚合服务。

聚合 LoopStats（对话量/满意度/响应时间）、RAGEvaluation（准确率）、
对话记录（解决率=无人工介入比）、LLM token（成本估算）等数据源，
输出效率 / 成本 / 覆盖 / 质量 / 趋势五类业务指标，供「业务效果仪表盘」
向企业决策者呈现 ROI。

数据口径（务必保持一致并在前端 tooltip 中说明）：
- 对话量：conversations 表按日聚合
- 解决率（无人工介入比）：会话末位 assistant 满意度非「不满意」视为已解决；
  末位满意度为 unsatisfied/dissatisfied 视为转人工。无满意度信号视为已解决。
- 响应时间：assistant 消息 token_count 代理指标（无真实计时字段），
  按 token 量分段映射到毫秒区间中位数，与 LoopDashboard 口径一致。
- 准确率：RAGEvaluation 四项指标（faithfulness/answer_relevancy/
  context_precision/context_recall）均值的平均值。
- 知识覆盖率：已获得 assistant 回复的 user 问题数 / user 问题总数。
- 单次协作成本：输出 token 实测 + 推理/思考 token + 工具调用 + 知识检索四类成本之和
  除以会话数。其中推理/工具/检索为「深度 Agent 协作」的建模构成（见下方常量），
  避免仅按输出 token 计费导致的严重低估。
- 月成本估算：单次协作成本 × 单个 AI 员工典型月工作量（建模，非实测会话数）。

实现策略：当前为实时聚合接口（range_days≤90，数据量可控）；
后台定时聚合落库 AgentKPI 作为后续优化项，二者口径保持一致。
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, func, and_, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.rag_evaluation import RAGEvaluation
from app.models.skill import Skill
from app.models.skill_execution import SkillExecution

logger = logging.getLogger(__name__)


# ============================================================
# 成本 / ROI 估算常量（口径公开，可在前端 tooltip 说明）
# ============================================================
# 国产模型（Doubao/通义/智谱）输入+输出 blended 均价，元/千 token
COST_PER_1K_TOKENS_YUAN = 0.008
# 人工查找/回复单个问题的平均耗时（分钟），用于 ROI 节省人力估算
HUMAN_LOOKUP_MINUTES = 5.0
# 人工客服单位成本（元/分钟），按 60 元/小时估算
HUMAN_COST_PER_MINUTE_YUAN = 1.0

# --- 深度 Agent 协作成本构成（原模型仅按输出 token 计费，低估 3 个数量级） ---
# 单次协作中 Agent 多轮推理/思考/上下文重放的 token 量（近似；深度任务常含
# 多轮工具调用并回传不断增长的上下文，单次可达数万 token）
REASONING_TOKENS_PER_CONVERSATION = 80000
# 单次工具调用（Skill/工具执行）成本，元/次
COST_PER_TOOL_CALL_YUAN = 0.08
# 单次知识检索（RAG 召回 + 重排）成本，元/次
COST_PER_RETRIEVAL_YUAN = 0.06
# 单次协作平均知识检索次数（Agent 深度检索）
AVG_RETRIEVALS_PER_CONVERSATION = 8
# 单个 AI 员工典型月工作量（协作/任务数），用于月成本估算（建模参考值）
MONTHLY_CONVERSATIONS_PER_EMPLOYEE = 200

# 满意度枚举 → 0-5 评分映射（与 LoopDashboard avgRating 口径一致）
_SATISFACTION_SCORE = {"satisfied": 5.0, "neutral": 4.0, "unsatisfied": 2.0, "dissatisfied": 2.0}
# 视为转人工 / 未解决的满意度信号
_NEGATIVE_SATISFACTION = {"unsatisfied", "dissatisfied"}


def _token_count_to_ms(tokens: int) -> int:
    """token_count → 估算响应毫秒（与 LoopDashboard 响应时间分桶中位数一致）。"""
    if tokens < 50:
        return 150
    if tokens < 150:
        return 350
    if tokens < 300:
        return 750
    if tokens < 600:
        return 1500
    return 2500


class MetricsService:
    """业务效果指标聚合器。"""

    async def _resolve_agent_ids(
        self,
        db: AsyncSession,
        agent_id: Optional[str],
        enterprise_id: Optional[str],
    ) -> list[str]:
        """解析要聚合的智能体 ID 列表。

        - 指定 agent_id：仅该智能体；
        - 未指定但指定企业：该企业下全部智能体；
        - 都未指定：返回空列表（调用方应保证至少传入其一）。
        """
        if agent_id:
            return [agent_id]
        if enterprise_id:
            result = await db.execute(
                select(Agent.id).where(Agent.enterprise_id == enterprise_id)
            )
            return [str(r) for r in result.scalars().all()]
        return []

    async def get_business_metrics(
        self,
        db: AsyncSession,
        agent_id: Optional[str] = None,
        enterprise_id: Optional[str] = None,
        range_days: int = 30,
    ) -> dict:
        """聚合业务效果指标。

        Args:
            db: 异步会话
            agent_id: 指定智能体（与 enterprise_id 二选一，优先 agent_id）
            enterprise_id: 企业 ID（agent_id 为空时聚合该企业全部智能体）
            range_days: 聚合时间窗口（天），上限 90

        Returns:
            含 summary/efficiency/cost/coverage/quality/trends 六组的结构化指标。
        """
        range_days = max(1, min(range_days, 90))
        agent_ids = await self._resolve_agent_ids(db, agent_id, enterprise_id)

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=range_days)
        period = {
            "start": cutoff.isoformat(),
            "end": now.isoformat(),
            "range_days": range_days,
        }

        # 无数据时返回零值骨架，避免前端除零与空指针
        if not agent_ids:
            return self._empty_result(agent_id, enterprise_id, period)

        # 1. 对话量与每日趋势
        total_conversations, daily_counts = await self._conversation_trend(
            db, agent_ids, cutoff, now, range_days
        )

        # 2. 消息层聚合：token、满意度、问题数、已回答数、响应时间
        msg_agg = await self._message_aggregation(db, agent_ids, cutoff)

        # 3. 末位满意度 → 解决率 / 转人工率
        unresolved_count = await self._count_unresolved_conversations(db, agent_ids, cutoff)

        # 4. RAG 准确率
        accuracy = await self._rag_accuracy(db, agent_ids, cutoff)

        # 5. 技能使用 Top5
        skill_top5 = await self._skill_top5(db, agent_ids, cutoff)

        # 6. 满意度每日趋势
        satisfaction_trend = await self._satisfaction_trend(db, agent_ids, cutoff, now, range_days)

        # ---------- 派生指标 ----------
        resolved_count = max(0, total_conversations - unresolved_count)
        resolution_rate = resolved_count / total_conversations if total_conversations else 0.0
        escalation_rate = unresolved_count / total_conversations if total_conversations else 0.0

        total_tokens = msg_agg["total_tokens"]
        # 工具调用实测次数（已成功的 SkillExecution）
        tool_call_count = await self._count_tool_calls(db, agent_ids, cutoff)

        # ---- 成本模型：输出 token 实测 + 推理/思考 + 工具调用 + 知识检索 ----
        # 输出 token 实测成本（仅 assistant 消息 token_count）
        output_token_cost = total_tokens / 1000.0 * COST_PER_1K_TOKENS_YUAN
        # Agent 多轮推理/思考/上下文重放的 token 成本（建模，随会话量线性）
        reasoning_cost = (
            total_conversations * REASONING_TOKENS_PER_CONVERSATION
            / 1000.0 * COST_PER_1K_TOKENS_YUAN
        )
        # 工具调用成本（实测成功次数 × 单价）
        tool_cost = tool_call_count * COST_PER_TOOL_CALL_YUAN
        # 知识检索成本（建模，随会话量线性）
        retrieval_cost = (
            total_conversations * AVG_RETRIEVALS_PER_CONVERSATION
            * COST_PER_RETRIEVAL_YUAN
        )

        total_cost_yuan = output_token_cost + reasoning_cost + tool_cost + retrieval_cost
        per_conversation_cost = total_cost_yuan / total_conversations if total_conversations else 0.0
        # 启用 AI 前基线：同等问题量由人工处理的人力成本
        pre_ai_cost_yuan = resolved_count * HUMAN_LOOKUP_MINUTES * HUMAN_COST_PER_MINUTE_YUAN
        cost_reduction_pct = (
            (pre_ai_cost_yuan - total_cost_yuan) / pre_ai_cost_yuan
            if pre_ai_cost_yuan > 0 else 0.0
        )
        # 月成本估算：单次协作成本 × 单个 AI 员工典型月工作量（建模参考值）
        estimated_monthly_cost = (
            per_conversation_cost * MONTHLY_CONVERSATIONS_PER_EMPLOYEE
            if total_conversations else 0.0
        )

        # 节省人力小时数：每个已解决问题节省 (人工耗时 - AI 耗时)
        ai_minutes_per_conv = msg_agg["avg_response_ms"] / 60000.0
        saved_per_conv = max(0.0, HUMAN_LOOKUP_MINUTES - ai_minutes_per_conv)
        hours_saved = resolved_count * saved_per_conv / 60.0

        total_questions = msg_agg["total_questions"]
        answered_questions = msg_agg["answered_questions"]
        knowledge_coverage = answered_questions / total_questions if total_questions else 0.0

        return {
            "agent_id": agent_id,
            "enterprise_id": enterprise_id,
            "period": period,
            "summary": {
                "total_conversations": total_conversations,
                "total_questions": total_questions,
                "total_tokens": total_tokens,
                "resolved_count": resolved_count,
            },
            "efficiency": {
                "resolution_rate": round(resolution_rate, 4),
                "escalation_rate": round(escalation_rate, 4),
                "avg_response_time_ms": msg_agg["avg_response_ms"],
                "human_compare_minutes": HUMAN_LOOKUP_MINUTES,
                "hours_saved": round(hours_saved, 2),
            },
            "cost": {
                "total_tokens": total_tokens,
                "tool_call_count": tool_call_count,
                "total_cost_yuan": round(total_cost_yuan, 4),
                "per_conversation_cost_yuan": round(per_conversation_cost, 4),
                "estimated_monthly_cost_yuan": round(estimated_monthly_cost, 2),
                "pre_ai_cost_yuan": round(pre_ai_cost_yuan, 2),
                "cost_reduction_pct": round(cost_reduction_pct, 4),
                "cost_per_1k_tokens_yuan": COST_PER_1K_TOKENS_YUAN,
            },
            "coverage": {
                "knowledge_coverage": round(knowledge_coverage, 4),
                "total_questions": total_questions,
                "answered_questions": answered_questions,
                "skill_top5": skill_top5,
            },
            "quality": {
                "accuracy": round(accuracy, 4),
                "satisfaction_score": round(msg_agg["satisfaction_score"], 2),
                "satisfaction_rate": round(msg_agg["satisfaction_rate"], 4),
                "rated_count": msg_agg["rated_count"],
            },
            "trends": {
                "daily_counts": daily_counts,
                "satisfaction_trend": satisfaction_trend,
            },
        }

    # -------------------- 子查询 --------------------

    async def _conversation_trend(
        self, db: AsyncSession, agent_ids: list[str],
        cutoff: datetime, now: datetime, days: int,
    ) -> tuple[int, list[dict]]:
        """对话量按日聚合（补齐空缺日期），返回 (总数, 趋势列表)。"""
        result = await db.execute(
            select(
                func.date(Conversation.created_at).label("d"),
                func.count(Conversation.id).label("c"),
            )
            .where(
                and_(
                    Conversation.agent_id.in_(agent_ids),
                    Conversation.created_at >= cutoff,
                )
            )
            .group_by(func.date(Conversation.created_at))
            .order_by("d")
        )
        rows = {str(r.d): int(r.c) for r in result.fetchall()}

        trend: list[dict] = []
        total = 0
        for i in range(days):
            d = (now - timedelta(days=days - 1 - i)).date()
            key = d.isoformat()
            count = rows.get(key, 0)
            total += count
            trend.append({"date": d.strftime("%m-%d"), "count": count})
        return total, trend

    async def _message_aggregation(
        self, db: AsyncSession, agent_ids: list[str], cutoff: datetime,
    ) -> dict:
        """聚合消息层指标：token 总量、平均响应时间、满意度、问题数与已回答数。"""
        # assistant 消息 token 统计（用于响应时间代理 + 成本）
        asst_result = await db.execute(
            select(Message.token_count)
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id.in_(agent_ids),
                    Message.role == "assistant",
                    Message.is_deleted.is_(False),
                    Message.created_at >= cutoff,
                )
            )
        )
        asst_tokens = [int(r[0] or 0) for r in asst_result.fetchall()]
        total_tokens = sum(asst_tokens)
        avg_response_ms = (
            sum(_token_count_to_ms(t) for t in asst_tokens) // len(asst_tokens)
            if asst_tokens else 0
        )

        # 满意度分布（assistant 消息）
        sat_result = await db.execute(
            select(Message.satisfaction, func.count(Message.id))
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id.in_(agent_ids),
                    Message.role == "assistant",
                    Message.is_deleted.is_(False),
                    Message.satisfaction.isnot(None),
                    Message.created_at >= cutoff,
                )
            )
            .group_by(Message.satisfaction)
        )
        sat_dist = {str(r[0]): int(r[1]) for r in sat_result.fetchall()}
        satisfied = sat_dist.get("satisfied", 0)
        neutral = sat_dist.get("neutral", 0)
        negative = sat_dist.get("unsatisfied", 0) + sat_dist.get("dissatisfied", 0)
        rated_count = satisfied + neutral + negative
        satisfaction_rate = satisfied / rated_count if rated_count else 0.0
        satisfaction_score = (
            (satisfied * 5.0 + neutral * 4.0 + negative * 2.0) / rated_count
            if rated_count else 0.0
        )

        # 问题数：user 消息总数
        total_q_result = await db.execute(
            select(func.count(Message.id))
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id.in_(agent_ids),
                    Message.role == "user",
                    Message.is_deleted.is_(False),
                    Message.created_at >= cutoff,
                )
            )
        )
        total_questions = int(total_q_result.scalar() or 0)

        # 已回答问题数：存在同会话中更晚的 assistant 回复的 user 消息数
        answered_questions = 0
        if total_questions > 0:
            asst_alias = aliased(Message)
            # 选取真实列而非 literal_column，避免部分方言（如旧版 SQLite）不识别
            answered_exists = (
                select(asst_alias.id)
                .where(
                    and_(
                        asst_alias.conversation_id == Message.conversation_id,
                        asst_alias.role == "assistant",
                        asst_alias.is_deleted.is_(False),
                        asst_alias.created_at >= Message.created_at,
                    )
                )
                .exists()
            )
            answered_result = await db.execute(
                select(func.count(Message.id))
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    and_(
                        Conversation.agent_id.in_(agent_ids),
                        Message.role == "user",
                        Message.is_deleted.is_(False),
                        Message.created_at >= cutoff,
                        answered_exists,
                    )
                )
            )
            answered_questions = int(answered_result.scalar() or 0)

        return {
            "total_tokens": total_tokens,
            "avg_response_ms": avg_response_ms,
            "satisfaction_score": satisfaction_score,
            "satisfaction_rate": satisfaction_rate,
            "rated_count": rated_count,
            "total_questions": total_questions,
            "answered_questions": answered_questions,
        }

    async def _count_unresolved_conversations(
        self, db: AsyncSession, agent_ids: list[str], cutoff: datetime,
    ) -> int:
        """统计末位满意度为负（转人工）的会话数。

        对每个会话取最新带满意度 assistant 消息的时刻，再回查该时刻的满意度，
        计数为负向（unsatisfied/dissatisfied）的会话数。
        """
        # 子查询：每个会话最新带满意度 assistant 消息的 created_at
        latest_time = (
            select(
                Message.conversation_id.label("cid"),
                func.max(Message.created_at).label("max_t"),
            )
            .where(
                and_(
                    Message.role == "assistant",
                    Message.is_deleted.is_(False),
                    Message.satisfaction.isnot(None),
                )
            )
            .group_by(Message.conversation_id)
            .subquery()
        )
        # JOIN 回 Message 取该时刻的满意度，计数负向满意度
        result = await db.execute(
            select(func.count(Message.id))
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(
                latest_time,
                and_(
                    latest_time.c.cid == Message.conversation_id,
                    latest_time.c.max_t == Message.created_at,
                ),
            )
            .where(
                and_(
                    Conversation.agent_id.in_(agent_ids),
                    Conversation.created_at >= cutoff,
                    Message.role == "assistant",
                    Message.satisfaction.in_(list(_NEGATIVE_SATISFACTION)),
                )
            )
        )
        return int(result.scalar() or 0)

    async def _rag_accuracy(
        self, db: AsyncSession, agent_ids: list[str], cutoff: datetime,
    ) -> float:
        """RAG 准确率：四项指标均值的平均。"""
        result = await db.execute(
            select(
                func.avg(RAGEvaluation.faithfulness),
                func.avg(RAGEvaluation.answer_relevancy),
                func.avg(RAGEvaluation.context_precision),
                func.avg(RAGEvaluation.context_recall),
            )
            .where(
                and_(
                    RAGEvaluation.agent_id.in_(agent_ids),
                    RAGEvaluation.evaluated_at >= cutoff,
                )
            )
        )
        row = result.one()
        values = [float(v or 0.0) for v in row]
        if not any(values):
            return 0.0
        # 四项均值的平均；若全部为 0 视为无数据
        avg_of_means = sum(values) / len(values)
        return avg_of_means

    async def _skill_top5(
        self, db: AsyncSession, agent_ids: list[str], cutoff: datetime,
    ) -> list[dict]:
        """技能使用频次 Top5。"""
        result = await db.execute(
            select(
                Skill.id,
                Skill.name,
                func.count(SkillExecution.id).label("cnt"),
            )
            .join(SkillExecution, SkillExecution.skill_id == Skill.id)
            .where(
                and_(
                    SkillExecution.agent_id.in_(agent_ids),
                    SkillExecution.created_at >= cutoff,
                    SkillExecution.status == "success",
                )
            )
            .group_by(Skill.id, Skill.name)
            .order_by(func.count(SkillExecution.id).desc())
            .limit(5)
        )
        return [
            {"skill_id": str(r.id), "name": r.name, "count": int(r.cnt)}
            for r in result.fetchall()
        ]

    async def _count_tool_calls(
        self, db: AsyncSession, agent_ids: list[str], cutoff: datetime,
    ) -> int:
        """统计时间窗内已成功执行的工具调用次数（SkillExecution status=success）。"""
        result = await db.execute(
            select(func.count(SkillExecution.id))
            .where(
                and_(
                    SkillExecution.agent_id.in_(agent_ids),
                    SkillExecution.created_at >= cutoff,
                    SkillExecution.status == "success",
                )
            )
        )
        return int(result.scalar() or 0)

    async def _satisfaction_trend(
        self, db: AsyncSession, agent_ids: list[str],
        cutoff: datetime, now: datetime, days: int,
    ) -> list[dict]:
        """满意度评分按日趋势（补齐空缺日期）。"""
        # 用 CASE 将满意度枚举映射为评分
        sat_score_expr = case(
            (Message.satisfaction == "satisfied", 5.0),
            (Message.satisfaction == "neutral", 4.0),
            (Message.satisfaction == "unsatisfied", 2.0),
            (Message.satisfaction == "dissatisfied", 2.0),
            else_=None,
        )
        result = await db.execute(
            select(
                func.date(Message.created_at).label("d"),
                func.avg(sat_score_expr).label("score"),
            )
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id.in_(agent_ids),
                    Message.role == "assistant",
                    Message.is_deleted.is_(False),
                    Message.satisfaction.isnot(None),
                    Message.created_at >= cutoff,
                )
            )
            .group_by(func.date(Message.created_at))
            .order_by("d")
        )
        rows = {str(r.d): float(r.score) if r.score is not None else 0.0 for r in result.fetchall()}

        trend: list[dict] = []
        for i in range(days):
            d = (now - timedelta(days=days - 1 - i)).date()
            key = d.isoformat()
            trend.append({"date": d.strftime("%m-%d"), "score": round(rows.get(key, 0.0), 2)})
        return trend

    # -------------------- 空结果骨架 --------------------

    def _empty_result(
        self, agent_id: Optional[str], enterprise_id: Optional[str], period: dict,
    ) -> dict:
        return {
            "agent_id": agent_id,
            "enterprise_id": enterprise_id,
            "period": period,
            "summary": {
                "total_conversations": 0,
                "total_questions": 0,
                "total_tokens": 0,
                "resolved_count": 0,
            },
            "efficiency": {
                "resolution_rate": 0.0,
                "escalation_rate": 0.0,
                "avg_response_time_ms": 0,
                "human_compare_minutes": HUMAN_LOOKUP_MINUTES,
                "hours_saved": 0.0,
            },
            "cost": {
                "total_tokens": 0,
                "tool_call_count": 0,
                "total_cost_yuan": 0.0,
                "per_conversation_cost_yuan": 0.0,
                "estimated_monthly_cost_yuan": 0.0,
                "pre_ai_cost_yuan": 0.0,
                "cost_reduction_pct": 0.0,
                "cost_per_1k_tokens_yuan": COST_PER_1K_TOKENS_YUAN,
            },
            "coverage": {
                "knowledge_coverage": 0.0,
                "total_questions": 0,
                "answered_questions": 0,
                "skill_top5": [],
            },
            "quality": {
                "accuracy": 0.0,
                "satisfaction_score": 0.0,
                "satisfaction_rate": 0.0,
                "rated_count": 0,
            },
            "trends": {
                "daily_counts": [],
                "satisfaction_trend": [],
            },
        }


# 模块级单例，与 loop_engine 用法一致
metrics_service = MetricsService()
