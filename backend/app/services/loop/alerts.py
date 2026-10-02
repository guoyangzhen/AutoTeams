"""Loop 错误日志与派生告警（alerts）。

从 ``app/services/loop_engine.py`` 拆分（原 1098-1251 行）。仅做只读查询：
- ``_build_error_logs``  合并 ProcessingTask.error_log 与 AuditLog 异常动作
- ``_build_alerts``      基于当前状态派生告警（Agent 状态 / 满意度 / 流量 / 失败任务）

监控统计与趋势见 :mod:`app.services.loop.analytics`。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.processing_task import ProcessingTask
from app.services.loop.analytics import _to_utc

logger = logging.getLogger(__name__)


class LoopAlertsMixin:
    """Loop 错误日志与派生告警（mixin，由 :class:`app.services.loop.engine.LoopEngine` 组装）。"""

    async def _build_error_logs(
        self, db: AsyncSession, agent_id: str, limit: int = 10,
    ) -> list[dict]:
        """错误日志：ProcessingTask.error_log + AuditLog 异常动作。"""
        logs: list[dict] = []

        # 来源1：ProcessingTask.error_log
        pt_result = await db.execute(
            select(ProcessingTask)
            .where(ProcessingTask.agent_id == agent_id)
            .order_by(ProcessingTask.created_at.desc())
            .limit(5)
        )
        for task in pt_result.scalars().all():
            err_list = task.error_log or []
            for err in err_list:
                if not isinstance(err, dict):
                    continue
                ts = err.get("timestamp") or (
                    task.created_at.strftime("%H:%M:%S") if task.created_at else "—"
                )
                logs.append({
                    "timestamp": ts if isinstance(ts, str) and ":" in str(ts) else str(ts)[-8:] if ts else "—",
                    "level": "ERROR",
                    "message": f"文件处理失败: {err.get('file', '未知')}",
                    "detail": str(err.get("error", ""))[:200],
                })

        # 来源2：AuditLog 异常动作
        audit_result = await db.execute(
            select(AuditLog)
            .where(
                or_(
                    AuditLog.resource_id == agent_id,
                    and_(
                        AuditLog.resource_type == "agent",
                        AuditLog.action.in_(["delete", "error", "build_failed"]),
                    ),
                )
            )
            .order_by(AuditLog.created_at.desc())
            .limit(5)
        )
        for log in audit_result.scalars().all():
            level = "ERROR" if log.action in ("delete", "error", "build_failed") else "WARN"
            logs.append({
                "timestamp": log.created_at.strftime("%H:%M:%S") if log.created_at else "—",
                "level": level,
                "message": f"{log.action} · {log.resource_type or 'agent'}",
                "detail": str(log.details or "")[:200] if log.details else None,
            })

        # 按时间倒序（字符串时间排序近似）
        logs.sort(key=lambda x: x["timestamp"], reverse=True)
        return logs[:limit]

    async def _build_alerts(
        self, db: AsyncSession, agent_id: str,
        satisfaction_pie: list[dict], trend: list[dict],
    ) -> list[dict]:
        """基于当前状态派生告警。"""
        alerts: list[dict] = []
        now = datetime.now(timezone.utc)

        # 1. Agent 状态告警
        agent_result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent:
            if agent.status == "error":
                alerts.append({
                    "dot": "dot-error",
                    "title": "智能体状态异常",
                    "desc": f"Agent {agent.name} 当前状态为 error",
                    "time": "当前",
                })
            elif agent.status == "processing":
                alerts.append({
                    "dot": "dot-warning",
                    "title": "智能体构建中",
                    "desc": f"Agent {agent.name} 正在处理",
                    "time": "当前",
                })

        # 2. 满意度告警
        total_sat = sum(item["value"] for item in satisfaction_pie)
        if total_sat > 0:
            unsatisfied = next((i["value"] for i in satisfaction_pie if i["name"] == "不满意"), 0)
            rate = unsatisfied / total_sat
            if rate > 0.3:
                alerts.append({
                    "dot": "dot-error",
                    "title": "不满意率过高",
                    "desc": f"不满意占比 {round(rate * 100)}%，建议优化知识库",
                    "time": "当前",
                })
            elif rate > 0.15:
                alerts.append({
                    "dot": "dot-warning",
                    "title": "满意度有所下降",
                    "desc": f"不满意占比 {round(rate * 100)}%",
                    "time": "当前",
                })

        # 3. 对话量异常波动
        if len(trend) >= 2:
            recent = trend[-1]["count"]
            prev = trend[-2]["count"]
            if prev > 0 and recent > prev * 2:
                alerts.append({
                    "dot": "dot-warning",
                    "title": "对话量异常增长",
                    "desc": f"较前一日增长 {round((recent / prev - 1) * 100)}%",
                    "time": "今日",
                })

        # 4. 最近失败任务
        fail_result = await db.execute(
            select(ProcessingTask)
            .where(
                and_(
                    ProcessingTask.agent_id == agent_id,
                    ProcessingTask.status == "failed",
                )
            )
            .order_by(ProcessingTask.created_at.desc())
            .limit(1)
        )
        failed = fail_result.scalar_one_or_none()
        if failed:
            ts = failed.created_at
            delta = "近期"
            if ts:
                ts_utc = _to_utc(ts)
                days_ago = (now - ts_utc).days if ts_utc else 0
                delta = f"{days_ago}天前" if days_ago > 0 else "今日"
            alerts.append({
                "dot": "dot-error",
                "title": "存在失败的处理任务",
                "desc": f"任务 {failed.id[:8]} 失败，{failed.failed_files} 个文件未处理",
                "time": delta,
            })

        # 5. 无告警时的正常状态
        if not alerts:
            alerts.append({
                "dot": "dot-success",
                "title": "系统运行正常",
                "desc": "所有指标在正常范围内",
                "time": "当前",
            })

        return alerts[:6]
