"""企业生命体征聚合（UI v4 §七 P1-6）。

驾驶舱「今日 AI 公司」需要一眼看清企业当前的运转状态。
此前前端需并发打 6 个接口再自行拼装，且大量指标被丢弃。
本服务一次聚合出全部生命体征，供 VitalPulse 波形与顶部体征带消费。

设计原则：所有数值均为真实统计，无数据时返回 0 / None，
绝不编造 —— 一家没在运转的 AI 公司就应该显示平直的心电线。
"""
import logging
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.collaboration import ApprovalGate, CollaborationEvent
from app.models.compiler import CompilationJob
from app.models.shadow import ShadowTask
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


async def collect_vitals(db: AsyncSession, enterprise_id: str) -> dict[str, Any]:
    """采集企业生命体征。

    Returns:
        {
          agents: {total, production, training, recruit, active},
          events: {last_hour, last_24h, total, failed, per_hour},
          approvals: {pending},
          shadow: {autonomous, total, trust_score},
          compile: {running, job_id, progress, last_completed_at},
          health: {score, tone},
          collected_at
        }
    """
    now = utcnow()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(hours=24)

    # —— AI 员工编制与在岗情况 ——
    agent_rows = await db.execute(
        select(Agent.lifecycle_stage, func.count(Agent.id))
        .where(Agent.enterprise_id == enterprise_id)
        .group_by(Agent.lifecycle_stage)
    )
    stage_dist: dict[str, int] = {}
    for stage, count in agent_rows:
        stage_dist[stage or "unknown"] = int(count or 0)
    total_agents = sum(stage_dist.values())
    production = stage_dist.get("production", 0)

    # —— 协作事件流速率（驱动心跳频率）——
    async def _count_events(since=None, status: Optional[str] = None) -> int:
        conds = [CollaborationEvent.enterprise_id == enterprise_id]
        if since is not None:
            conds.append(CollaborationEvent.created_at >= since)
        if status is not None:
            conds.append(CollaborationEvent.status == status)
        r = await db.execute(select(func.count(CollaborationEvent.id)).where(*conds))
        return int(r.scalar() or 0)

    events_hour = await _count_events(hour_ago)
    events_day = await _count_events(day_ago)
    events_total = await _count_events()
    events_failed = await _count_events(day_ago, "failed")

    # 每小时事件速率：优先用近 1h 实测，样本不足时用 24h 均摊
    per_hour = events_hour if events_hour > 0 else round(events_day / 24, 1)

    # —— 待审批（琥珀信号源）——
    pending_r = await db.execute(
        select(func.count(ApprovalGate.id)).where(
            ApprovalGate.enterprise_id == enterprise_id,
            ApprovalGate.status == "pending",
        )
    )
    pending_approvals = int(pending_r.scalar() or 0)

    # —— 影子模式信任度（真实计算，替代前端写死的 30/60/100）——
    shadow_total_r = await db.execute(
        select(func.count(ShadowTask.id)).where(ShadowTask.enterprise_id == enterprise_id)
    )
    shadow_total = int(shadow_total_r.scalar() or 0)
    shadow_auto_r = await db.execute(
        select(func.count(ShadowTask.id)).where(
            ShadowTask.enterprise_id == enterprise_id,
            ShadowTask.status == "autonomous",
        )
    )
    shadow_auto = int(shadow_auto_r.scalar() or 0)
    match_r = await db.execute(
        select(func.count(ShadowTask.id)).where(
            ShadowTask.enterprise_id == enterprise_id,
            ShadowTask.eval_result == "match",
        )
    )
    match_count = int(match_r.scalar() or 0)
    evaluated_r = await db.execute(
        select(func.count(ShadowTask.id)).where(
            ShadowTask.enterprise_id == enterprise_id,
            ShadowTask.eval_result.in_(["match", "mismatch"]),
        )
    )
    evaluated = int(evaluated_r.scalar() or 0)
    trust_score = round(match_count / evaluated, 4) if evaluated > 0 else None

    # —— 编译状态（决定波形是否切换为扫描线）——
    running_r = await db.execute(
        select(CompilationJob)
        .where(
            CompilationJob.enterprise_id == enterprise_id,
            CompilationJob.status == "running",
        )
        .order_by(CompilationJob.created_at.desc())
        .limit(1)
    )
    running_job = running_r.scalar_one_or_none()

    last_done_r = await db.execute(
        select(CompilationJob)
        .where(
            CompilationJob.enterprise_id == enterprise_id,
            CompilationJob.status == "completed",
        )
        .order_by(CompilationJob.created_at.desc())
        .limit(1)
    )
    last_done = last_done_r.scalar_one_or_none()

    # —— 健康度综合评分 ——
    # 三个维度：在岗率（员工是否上岗）、处理率（事件是否被消化）、无故障
    staffing = production / total_agents if total_agents > 0 else 0.0
    processed_day = events_day - events_failed
    throughput = processed_day / events_day if events_day > 0 else 1.0
    fault_penalty = min(0.3, events_failed * 0.1)
    health = max(0.0, min(1.0, staffing * 0.4 + throughput * 0.6 - fault_penalty))

    if events_failed > 0:
        tone = "fault"
    elif pending_approvals > 0:
        tone = "alert"
    elif total_agents == 0:
        tone = "idle"
    else:
        tone = "alive"

    return {
        "agents": {
            "total": total_agents,
            "production": production,
            "training": stage_dist.get("training", 0),
            "recruit": stage_dist.get("recruit", 0),
            "stage_distribution": stage_dist,
        },
        "events": {
            "last_hour": events_hour,
            "last_24h": events_day,
            "total": events_total,
            "failed": events_failed,
            "per_hour": per_hour,
        },
        "approvals": {"pending": pending_approvals},
        "shadow": {
            "autonomous": shadow_auto,
            "total": shadow_total,
            "evaluated": evaluated,
            "match": match_count,
            # 真实信任度：一致判定数 / 已评估数；无样本时为 None（前端显示「样本不足」）
            "trust_score": trust_score,
        },
        "compile": {
            "running": running_job is not None,
            "job_id": running_job.id if running_job else None,
            "stage": running_job.stage if running_job else None,
            "progress": (running_job.progress or 0.0) if running_job else None,
            "last_completed_at": (
                last_done.completed_at.isoformat()
                if last_done and last_done.completed_at
                else None
            ),
            "last_completeness": last_done.completeness if last_done else None,
        },
        "health": {"score": round(health, 4), "tone": tone},
        "collected_at": now.isoformat(),
    }
