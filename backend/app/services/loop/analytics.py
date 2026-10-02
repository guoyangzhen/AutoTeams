"""Loop 监控统计与趋势（analytics）。

从 ``app/services/loop_engine.py`` 拆分（原 900-1269 行）。只读侧聚合，
不触发任何写操作，可安全地在看板 / 报表接口中高频调用。

包含：
- ``get_loop_stats``    监控总览聚合（趋势 + 响应时间 + 满意度 + 会话 + 错误 + 告警）
- ``get_loop_status``   优化历史查询
- ``_build_*``          上述聚合的各数据块构造器
- ``_to_utc``           naive/aware datetime 规范化工具（供 alerts 模块复用）

错误日志与派生告警见 :mod:`app.services.loop.alerts`。
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.optimization_history import OptimizationHistory

logger = logging.getLogger(__name__)


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """安全将 naive/aware datetime 规范化为带 UTC 时区的时间对象。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class LoopAnalyticsMixin:
    """Loop 监控统计与趋势（mixin，由 :class:`app.services.loop.engine.LoopEngine` 组装）。"""

    async def get_loop_stats(
        self,
        db: AsyncSession,
        agent_id: str,
        days: int = 7,
    ) -> dict:
        """P0-1b: 聚合监控统计数据。

        数据来源：
        - 对话量趋势：conversations 按日聚合
        - 响应时间分布：assistant 消息 token_count 代理指标（无真实计时时的合理近似）
        - 满意度饼图：assistant 消息 satisfaction 分布
        - 最近会话：conversations 倒序 + 消息数/满意度
        - 错误日志：ProcessingTask.error_log + AuditLog 异常动作
        - 告警：基于当前状态派生（满意度低/Agent 异常/任务失败）
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)

        # 1. 对话量趋势（按日聚合，补齐空缺日期）
        trend = await self._build_trend(db, agent_id, cutoff, now, days)

        # 2. 响应时间分布（token_count 代理）
        response_time = await self._build_response_time(db, agent_id, cutoff)

        # 3. 满意度饼图
        satisfaction_pie = await self._build_satisfaction_pie(db, agent_id, cutoff)

        # 4. 最近会话
        recent_sessions = await self._build_recent_sessions(db, agent_id, limit=8)

        # 5. 错误日志
        error_logs = await self._build_error_logs(db, agent_id, limit=10)

        # 6. 告警
        alerts = await self._build_alerts(db, agent_id, satisfaction_pie, trend)

        return {
            "trend": trend,
            "response_time": response_time,
            "satisfaction_pie": satisfaction_pie,
            "recent_sessions": recent_sessions,
            "error_logs": error_logs,
            "alerts": alerts,
        }

    async def _build_trend(
        self, db: AsyncSession, agent_id: str,
        cutoff: datetime, now: datetime, days: int,
    ) -> list[dict]:
        """对话量按日聚合，补齐空缺日期。"""
        result = await db.execute(
            select(
                func.date(Conversation.created_at).label("d"),
                func.count(Conversation.id).label("c"),
            )
            .where(
                and_(
                    Conversation.agent_id == agent_id,
                    Conversation.created_at >= cutoff,
                )
            )
            .group_by(func.date(Conversation.created_at))
            .order_by("d")
        )
        rows = {str(r.d): int(r.c) for r in result.fetchall()}

        trend: list[dict] = []
        for i in range(days):
            d = (now - timedelta(days=days - 1 - i)).date()
            key = d.isoformat()
            label = d.strftime("%m-%d")
            trend.append({"time": label, "count": rows.get(key, 0)})
        return trend

    async def _build_response_time(
        self, db: AsyncSession, agent_id: str, cutoff: datetime,
    ) -> list[dict]:
        """响应时间分布（token_count 代理指标）。

        无真实计时字段，用 assistant 消息的 token_count 近似：
        - <50 tokens ≈ <200ms
        - 50-150 ≈ 200-500ms
        - 150-300 ≈ 500-1000ms
        - 300-600 ≈ 1-2s
        - >600 ≈ >2s
        """
        result = await db.execute(
            select(Message.token_count)
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id == agent_id,
                    Message.role == "assistant",
                    Message.is_deleted == False,  # noqa: E712
                    Message.created_at >= cutoff,
                )
            )
        )
        counts = [int(r[0] or 0) for r in result.fetchall()]
        buckets = [
            {"range": "<200ms", "count": 0},
            {"range": "200-500ms", "count": 0},
            {"range": "500-1000ms", "count": 0},
            {"range": "1-2s", "count": 0},
            {"range": ">2s", "count": 0},
        ]
        for c in counts:
            if c < 50:
                buckets[0]["count"] += 1
            elif c < 150:
                buckets[1]["count"] += 1
            elif c < 300:
                buckets[2]["count"] += 1
            elif c < 600:
                buckets[3]["count"] += 1
            else:
                buckets[4]["count"] += 1
        return buckets

    async def _build_satisfaction_pie(
        self, db: AsyncSession, agent_id: str, cutoff: datetime,
    ) -> list[dict]:
        """满意度饼图：satisfied / neutral / unsatisfied。"""
        result = await db.execute(
            select(Message.satisfaction, func.count(Message.id))
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id == agent_id,
                    Message.role == "assistant",
                    Message.is_deleted == False,  # noqa: E712
                    Message.satisfaction.isnot(None),
                    Message.created_at >= cutoff,
                )
            )
            .group_by(Message.satisfaction)
        )
        raw = {str(r[0]): int(r[1]) for r in result.fetchall()}

        satisfied = raw.get("satisfied", 0)
        neutral = raw.get("neutral", 0)
        unsatisfied = raw.get("unsatisfied", 0) + raw.get("dissatisfied", 0)

        return [
            {"name": "满意", "value": satisfied, "color": "#1E3A5F"},
            {"name": "一般", "value": neutral, "color": "#6B8CB1"},
            {"name": "不满意", "value": unsatisfied, "color": "#D97706"},
        ]

    async def _build_recent_sessions(
        self, db: AsyncSession, agent_id: str, limit: int = 8,
    ) -> list[dict]:
        """最近会话列表：含消息数、响应时间、满意度、状态。"""
        result = await db.execute(
            select(Conversation)
            .where(Conversation.agent_id == agent_id)
            .order_by(Conversation.created_at.desc())
            .limit(limit)
        )
        conversations = result.scalars().all()
        if not conversations:
            return []

        sessions: list[dict] = []
        for conv in conversations:
            # 统计消息数与最后一条 assistant 消息的满意度/token
            msg_result = await db.execute(
                select(
                    func.count(Message.id),
                    func.max(Message.token_count),
                )
                .where(
                    and_(
                        Message.conversation_id == conv.id,
                        Message.is_deleted == False,  # noqa: E712
                    )
                )
            )
            msg_count, max_tokens = msg_result.fetchone()

            sat_result = await db.execute(
                select(Message.satisfaction)
                .where(
                    and_(
                        Message.conversation_id == conv.id,
                        Message.role == "assistant",
                        Message.is_deleted == False,  # noqa: E712
                        Message.satisfaction.isnot(None),
                    )
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            sat_row = sat_result.fetchone()
            satisfaction = sat_row[0] if sat_row else None

            # 状态：基于更新时间推断
            now = datetime.now(timezone.utc)
            updated_utc = _to_utc(conv.updated_at)
            if updated_utc and (now - updated_utc).total_seconds() < 300:
                status = "active"
            elif satisfaction in ("unsatisfied", "dissatisfied"):
                status = "interrupted"
            else:
                status = "completed"

            # 满意度数值化
            sat_value = None
            if satisfaction == "satisfied":
                sat_value = 5
            elif satisfaction == "neutral":
                sat_value = 4
            elif satisfaction in ("unsatisfied", "dissatisfied"):
                sat_value = 2

            # 响应时间（token 代理显示）
            rt_label = "—"
            if max_tokens:
                if max_tokens < 50:
                    rt_label = "<200ms"
                elif max_tokens < 150:
                    rt_label = "200-500ms"
                elif max_tokens < 300:
                    rt_label = "500-1000ms"
                elif max_tokens < 600:
                    rt_label = "1-2s"
                else:
                    rt_label = ">2s"

            # 用户标识
            user_label = f"用户 {conv.user_id[:6]}" if conv.user_id else "匿名"

            sessions.append({
                "id": conv.id[:8],
                "user": user_label,
                "startTime": conv.created_at.strftime("%H:%M") if conv.created_at else "—",
                "messages": int(msg_count or 0),
                "responseTime": rt_label,
                "satisfaction": sat_value,
                "status": status,
            })
        return sessions

    async def get_loop_status(self, agent_id: str, db: AsyncSession) -> dict:
        """从数据库查询优化历史。"""
        stmt = (
            select(OptimizationHistory)
            .where(OptimizationHistory.agent_id == agent_id)
            .order_by(OptimizationHistory.created_at.desc())
            .limit(100)
        )
        result = await db.execute(stmt)
        optimizations = result.scalars().all()

        return {
            "agent_id": agent_id,
            "total_optimizations": len(optimizations),
            "recent_optimizations": [o.to_dict() for o in optimizations[:5]],
            "status": "active" if optimizations else "inactive",
        }
