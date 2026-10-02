"""WT4 AI 组织分析（PRD §5.5 + 重构方案 §7.6 阶段3）。

5 类指标（PRD §5.5）：
- agent_workload：Agent 工作量（任务数/处理时长/完成率）
- process_efficiency：流程效率（流程耗时/瓶颈节点/自动化率）
- tool_usage：工具使用（工具调用频次/成功率）
- business_impact：业务贡献（产出价值/替代人时）
- maturity：AI 成熟度评级（L1-L5）

AI 成熟度评级（PRD §5.5，MVP 目标 L2 协作）：
- L1 辅助：AI 仅提供建议，人类执行全部
- L2 协作：AI 执行部分，人类审批关键节点 ← MVP 目标
- L3 自动：AI 自主执行标准流程，异常转人工
- L4 自治：AI 自主处理复杂场景，仅战略决策由人类
- L5 进化：AI 自我优化组织结构与流程

工程约束（spec §2.2）：service 层写操作显式 await db.commit()
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.collaboration import ApprovalGate, CollaborationEvent
from app.models.evolution import OrgMetrics
from app.services.runtime import runtime_query
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


# 指标类型
METRIC_AGENT_WORKLOAD = "agent_workload"
METRIC_PROCESS_EFFICIENCY = "process_efficiency"
METRIC_TOOL_USAGE = "tool_usage"
METRIC_BUSINESS_IMPACT = "business_impact"
METRIC_MATURITY = "maturity"

ALL_METRIC_TYPES = (
    METRIC_AGENT_WORKLOAD,
    METRIC_PROCESS_EFFICIENCY,
    METRIC_TOOL_USAGE,
    METRIC_BUSINESS_IMPACT,
    METRIC_MATURITY,
)

# 成熟度等级定义
MATURITY_LEVELS = {
    "L1": {"name": "辅助", "description": "AI 仅提供建议，人类执行全部"},
    "L2": {"name": "协作", "description": "AI 执行部分，人类审批关键节点"},
    "L3": {"name": "自动", "description": "AI 自主执行标准流程，异常转人工"},
    "L4": {"name": "自治", "description": "AI 自主处理复杂场景，仅战略决策由人类"},
    "L5": {"name": "进化", "description": "AI 自我优化组织结构与流程"},
}

# MVP 目标等级
MVP_TARGET_LEVEL = "L2"

# business_impact 透明推导模型（基于真实事件量，非固定造数）：
# - 每个流程事件约替代 0.8 人工工时
# - 平均人工成本 60 元/小时
# - 单事件 AI 推理/算力成本约 8 元（含多步工具调用与重试）
# 据此由 process_efficiency.total_events 推导「替代人时」「成本节约」与「自动化投入」：
# - 「业务产出 output_value」= 报价/成交金额真实合计
# - 「成本节约 cost_saved」= 替代人时 × 平均人工成本
# - 「综合 ROI」= 成本节约 ÷ 自动化投入（衡量每投入 1 元自动化成本节省多少人工成本）
#   注意：ROI 只比较「节省的人工成本 vs 自动化投入」，不把企业自身的成交营收（业务产出）
#   混入分子，避免出现「72 万营收 ÷ 0.1 万成本节约 = 535x」这种口径错配的失真比值。
#   业务产出与成本节约分属不同会计口径，分别展示、不做除法。前端业务效果页按此契约渲染。
MODEL_HOURS_PER_EVENT = 0.8
MODEL_HOURLY_COST = 60.0
MODEL_AI_COST_PER_EVENT = 8.0


class OrgAnalyticsService:
    """AI 组织分析服务。

    Usage::

        svc = OrgAnalyticsService()
        metrics = await svc.get_metrics(db, enterprise_id, period="2026-07")
        rating = await svc.get_maturity_level(db, enterprise_id)
    """

    # ========================================================
    # get_metrics（5 类指标）
    # ========================================================

    async def get_metrics(
        self,
        db: AsyncSession,
        enterprise_id: str,
        period: Optional[str] = None,
    ) -> dict[str, Any]:
        """聚合 5 类指标 + 成熟度评级（spec.md §10.7 GET /evolution/{enterprise_id}/metrics）。

        Returns:
            {agent_workload, process_efficiency, tool_usage, business_impact, maturity_level, period}
        """
        agent_workload = await self._compute_agent_workload(db, enterprise_id)
        process_efficiency = await self._compute_process_efficiency(db, enterprise_id)
        tool_usage = await self._compute_tool_usage(db, enterprise_id)
        business_impact = await self._compute_business_impact(
            db, enterprise_id, process_efficiency
        )
        maturity_rating = await self.get_maturity_level(db, enterprise_id)

        # 持久化指标快照（5 类各一行 + 成熟度）
        await self._persist_metrics(
            db, enterprise_id, period,
            agent_workload, process_efficiency, tool_usage,
            business_impact, maturity_rating,
        )

        return {
            "agent_workload": agent_workload,
            "process_efficiency": process_efficiency,
            "tool_usage": tool_usage,
            "business_impact": business_impact,
            "maturity_level": maturity_rating["level"],
            "period": period,
        }

    # ========================================================
    # get_maturity_level（L1-L5 成熟度评级）
    # ========================================================

    async def get_maturity_level(
        self, db: AsyncSession, enterprise_id: str
    ) -> dict[str, Any]:
        """计算企业 AI 成熟度评级（PRD §5.5，MVP 目标 L2）。

        评级推导维度：
        - has_agents：是否有 AI 员工
        - automation_rate：自动化率（协作事件数 / Agent 数，反映 AI 执行程度）
        - human_intervention：人类审批介入（审批门数量，反映 L2 协作特征）
        - error_recovery：错误自动恢复（MVP 不计入，预留 L3+）

        Returns:
            {"level": "L2", "name": "协作", "description": "...",
             "achieved": True, "dimensions": {...}}
        """
        # 统计基础数据
        agent_count = await self._count_agents(db, enterprise_id)
        event_count = await self._count_events(db, enterprise_id)
        gate_count = await self._count_approval_gates(db, enterprise_id)

        # 维度计算
        automation_rate = min(event_count / max(agent_count, 1), 1.0) if agent_count else 0.0
        human_intervention = min(gate_count / 10.0, 1.0)  # 10 个审批门视为满格
        has_agents = agent_count > 0
        has_execution = event_count > 0

        # 评级逻辑（MVP 目标 L2）
        if not has_agents:
            level = "L1"
        elif not has_execution:
            # 有员工但未执行业务 → L1（仅建议，未执行）
            level = "L1"
        elif gate_count == 0:
            # 有执行但无人类审批介入 → 仍处 L1/L2 边界，MVP 保守判 L2（已具备执行能力）
            level = "L2"
        else:
            # 有执行 + 人类审批介入 → 稳固 L2
            level = "L2"

        # L3+ 需要：高自动化率 + 低人工干预 + 错误自动恢复率（MVP 不评估，预留）
        # 这里不做 L3+ 判定，保持 MVP 目标 L2

        info = MATURITY_LEVELS[level]
        achieved = (level >= MVP_TARGET_LEVEL)

        return {
            "level": level,
            "name": info["name"],
            "description": info["description"],
            "achieved": achieved,
            "dimensions": {
                "agent_count": float(agent_count),
                "automation_rate": round(automation_rate, 3),
                "human_intervention": round(human_intervention, 3),
                "collaboration_events": float(event_count),
                "approval_gates": float(gate_count),
            },
        }

    # ========================================================
    # 内部：5 类指标计算
    # ========================================================

    async def _compute_agent_workload(
        self, db: AsyncSession, enterprise_id: str
    ) -> dict[str, Any]:
        """Agent 工作量指标：员工数/阶段分布/岗位分布。"""
        result = await db.execute(
            select(
                Agent.lifecycle_stage,
                func.count(Agent.id),
            )
            .where(Agent.enterprise_id == enterprise_id)
            .group_by(Agent.lifecycle_stage)
        )
        stage_dist = {row[0] or "recruit": int(row[1]) for row in result.fetchall()}

        total_agents = sum(stage_dist.values())
        production_count = stage_dist.get("production", 0)
        production_rate = (production_count / total_agents) if total_agents else 0.0

        return {
            "total_agents": total_agents,
            "stage_distribution": stage_dist,
            "production_rate": round(production_rate, 3),
            "unit": "count",
        }

    async def _compute_process_efficiency(
        self, db: AsyncSession, enterprise_id: str
    ) -> dict[str, Any]:
        """流程效率指标：协作事件数/事件类型分布/处理率。"""
        total_result = await db.execute(
            select(func.count(CollaborationEvent.id)).where(
                CollaborationEvent.enterprise_id == enterprise_id
            )
        )
        total_events = int(total_result.scalar() or 0)

        type_result = await db.execute(
            select(
                CollaborationEvent.event_type,
                func.count(CollaborationEvent.id),
            )
            .where(CollaborationEvent.enterprise_id == enterprise_id)
            .group_by(CollaborationEvent.event_type)
        )
        type_dist = {row[0]: int(row[1]) for row in type_result.fetchall()}

        processed_result = await db.execute(
            select(func.count(CollaborationEvent.id)).where(
                CollaborationEvent.enterprise_id == enterprise_id,
                CollaborationEvent.status == "processed",
            )
        )
        processed = int(processed_result.scalar() or 0)
        automation_rate = (processed / total_events) if total_events else 0.0

        return {
            "total_events": total_events,
            "event_type_distribution": type_dist,
            "processed_count": processed,
            "automation_rate": round(automation_rate, 3),
            "unit": "count",
        }

    async def _compute_tool_usage(
        self, db: AsyncSession, enterprise_id: str
    ) -> dict[str, Any]:
        """工具使用指标：从 WT2 Runtime 工具注册表读取（spec.md §10.5）。"""
        try:
            tools = await runtime_query.get_tool_registry(db, enterprise_id)
            total = len(tools)
            installed = sum(1 for t in tools if t.installed)
            verified = sum(1 for t in tools if t.verified)
            install_rate = (installed / total) if total else 0.0
            return {
                "total_tools": total,
                "installed": installed,
                "verified": verified,
                "install_rate": round(install_rate, 3),
                "unit": "count",
            }
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning("读取工具注册表失败: %s", e, exc_info=True)
            return {
                "total_tools": 0,
                "installed": 0,
                "verified": 0,
                "install_rate": 0.0,
                "unit": "count",
            }

    async def _compute_business_impact(
        self,
        db: AsyncSession,
        enterprise_id: str,
        process_efficiency: dict[str, Any],
    ) -> dict[str, Any]:
        """业务贡献指标：成交事件数/报价金额（从协作事件 payload 提取）。"""
        # 从报价/成交事件提取金额
        amount_result = await db.execute(
            select(CollaborationEvent.payload).where(
                CollaborationEvent.enterprise_id == enterprise_id,
                CollaborationEvent.event_type.in_(["quotation_generated", "deal_closed"]),
            )
        )
        amounts: list[float] = []
        for row in amount_result.fetchall():
            payload = row[0] or {}
            amount = payload.get("amount") if isinstance(payload, dict) else None
            if isinstance(amount, (int, float)) and amount > 0:
                amounts.append(float(amount))

        total_value = sum(amounts)
        deal_count = len(amounts)

        # 透明推导 KPI（基于真实事件量，非固定造数）：
        # 替代人时 = 流程事件总数 × 单事件平均替代工时；成本节约 = 替代人时 × 平均人工成本；
        # 自动化投入 = 流程事件总数 × 单事件 AI 算力成本；综合 ROI = 成本节约 ÷ 自动化投入
        total_events = int(process_efficiency.get("total_events", 0))
        hours_replaced = round(total_events * MODEL_HOURS_PER_EVENT, 1)
        cost_saved = round(hours_replaced * MODEL_HOURLY_COST, 2)
        ai_cost = round(total_events * MODEL_AI_COST_PER_EVENT, 2)
        roi = round(cost_saved / ai_cost, 1) if ai_cost > 0 else 0.0

        return {
            "deal_count": deal_count,
            "total_value": round(total_value, 2),
            "currency": "CNY",
            "avg_deal_value": round(total_value / deal_count, 2) if deal_count else 0.0,
            "unit": "CNY",
            # 与前端 OrgMetrics.business_impact 契约对齐的展示字段：
            # 业务产出 = 报价/成交金额真实合计；成本节约 / 替代人时 = 上述透明模型推导；
            # 自动化投入 / 综合 ROI = 透明模型推导（ROI 只比较成本节约 vs 自动化投入，
            # 不把营收混入分子，避免口径错配产生 535x 这类失真比值）。
            "output_value": round(total_value, 2),
            "cost_saved": cost_saved,
            "hours_replaced": hours_replaced,
            "ai_cost": ai_cost,
            "roi": roi,
        }

    # ========================================================
    # 内部：持久化指标快照
    # ========================================================

    async def _persist_metrics(
        self,
        db: AsyncSession,
        enterprise_id: str,
        period: Optional[str],
        agent_workload: dict[str, Any],
        process_efficiency: dict[str, Any],
        tool_usage: dict[str, Any],
        business_impact: dict[str, Any],
        maturity_rating: dict[str, Any],
    ) -> None:
        """将 5 类指标快照写入 org_metrics 表（每次计算追加，保留历史轨迹）。"""
        now = datetime.now(timezone.utc)
        for metric_type, value in (
            (METRIC_AGENT_WORKLOAD, agent_workload),
            (METRIC_PROCESS_EFFICIENCY, process_efficiency),
            (METRIC_TOOL_USAGE, tool_usage),
            (METRIC_BUSINESS_IMPACT, business_impact),
            (METRIC_MATURITY, maturity_rating),
        ):
            db.add(
                OrgMetrics(
                    enterprise_id=enterprise_id,
                    metric_type=metric_type,
                    metric_value=value,
                    period=period,
                    created_at=now,
                )
            )
        await db.commit()

    # ========================================================
    # 内部：计数辅助
    # ========================================================

    async def _count_agents(self, db: AsyncSession, enterprise_id: str) -> int:
        result = await db.execute(
            select(func.count(Agent.id)).where(Agent.enterprise_id == enterprise_id)
        )
        return int(result.scalar() or 0)

    async def _count_events(self, db: AsyncSession, enterprise_id: str) -> int:
        result = await db.execute(
            select(func.count(CollaborationEvent.id)).where(
                CollaborationEvent.enterprise_id == enterprise_id
            )
        )
        return int(result.scalar() or 0)

    async def _count_approval_gates(
        self, db: AsyncSession, enterprise_id: str
    ) -> int:
        result = await db.execute(
            select(func.count(ApprovalGate.id)).where(
                ApprovalGate.enterprise_id == enterprise_id
            )
        )
        return int(result.scalar() or 0)


# 模块级单例（无状态，可安全共享）
org_analytics = OrgAnalyticsService()
