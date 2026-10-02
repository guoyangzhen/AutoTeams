"""定时汇总与平台健康度检查任务。

由 celery_app.beat_schedule 驱动：
- ``generate_daily_report``  每日 02:00 汇总前一日经营日报
- ``check_platform_health``  每 15 分钟探测依赖健康度

设计原则：
- **可观测**：返回结构化 dict 而非只打日志，beat 与外部监控都能读到结果；
- **不吞异常**：健康检查返回 unhealthy 是**结论**（要上报），不是错误；
  但探测本身抛异常必须记录，不能让 worker 静默退出；
- **无外部依赖可测**：任务签名与结构组装逻辑与 DB/Redis 解耦，单测可 monkeypatch。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from celery import shared_task

from app.tasks.celery_app import celery_app  # noqa: F401  —— 保证任务注册到同一 app

logger = logging.getLogger(__name__)


def _summarize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """从 get_business_metrics 的原始结果里挑出日报需要的字段。

    单独抽出是为了让「字段挑选」这一纯函数可被单测直接覆盖，
    不必连数据库。
    """
    summary = metrics.get("summary", {}) or {}
    efficiency = metrics.get("efficiency", {}) or {}
    quality = metrics.get("quality", {}) or {}
    period = metrics.get("period", {}) or {}
    return {
        "period": {"start": period.get("start"), "end": period.get("end")},
        "conversations": summary.get("total_conversations", 0),
        "questions": summary.get("total_questions", 0),
        "resolved": summary.get("resolved_count", 0),
        "resolution_rate": round(float(efficiency.get("resolution_rate", 0.0)), 4),
        "hours_saved": efficiency.get("hours_saved", 0),
        "satisfaction": round(float(quality.get("satisfaction_score", 0.0)), 2),
        "rated_count": quality.get("rated_count", 0),
    }


@shared_task(bind=False, name="app.tasks.scheduled_metrics.generate_daily_report")
def generate_daily_report(enterprise_id: Optional[str] = None, range_days: int = 1) -> Dict[str, Any]:
    """汇总指定企业的日报指标。

    Args:
        enterprise_id: 企业 ID；为 None 时跨企业汇总
        range_days: 统计窗口，默认前 1 天

    Returns:
        含 report_id / window / 核心指标的 dict，便于下游落库或告警消费。
    """
    from app.database import async_session_factory
    from app.services.metrics_service import MetricsService

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=range_days)

    async def _collect() -> Dict[str, Any]:
        async with async_session_factory() as session:
            return await MetricsService().get_business_metrics(
                db=session,
                enterprise_id=enterprise_id,
                range_days=range_days,
            )

    try:
        metrics = asyncio.run(_collect())
    except Exception as exc:  # noqa: BLE001 —— 日报失败不应拖垮 beat
        logger.exception("日报汇总失败 enterprise_id=%s", enterprise_id)
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "enterprise_id": enterprise_id,
            "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        }

    report = {
        "status": "ok",
        "enterprise_id": enterprise_id,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "metrics": _summarize_metrics(metrics),
        "generated_at": window_end.isoformat(),
    }
    logger.info("[tasks] 日报已生成 enterprise_id=%s", enterprise_id)
    return report


@shared_task(bind=False, name="app.tasks.scheduled_metrics.check_platform_health")
def check_platform_health() -> Dict[str, Any]:
    """探测数据库 / Redis / ChromaDB 健康度。

    返回 unhealthy 是**探测结论**而非任务异常：只有探测本身崩溃才记 error。
    规则沿用 ``app/utils/health.py``：database down → unhealthy；
    redis/chromadb down → degraded；全 up → healthy。
    """
    from app.utils.health import run_health_checks, serialize_statuses

    try:
        status, dependencies = asyncio.run(run_health_checks())
        return {
            "status": status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "dependencies": serialize_statuses(dependencies),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("健康度探测本身失败")
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "dependencies": [],
        }


@shared_task(bind=False, name="app.tasks.scheduled_metrics.summarize_batch")
def summarize_batch(enterprise_ids: List[str], range_days: int = 1) -> Dict[str, Any]:
    """批量为多家企业生成日报摘要。

    单企业失败只记录该项，不影响其余企业——批量任务里最常见的诉求是
    「别让一家的问题拖垮全部」。
    """
    reports: List[Dict[str, Any]] = []
    failed = 0
    for eid in enterprise_ids or []:
        report = generate_daily_report(enterprise_id=eid, range_days=range_days)
        reports.append(report)
        if report.get("status") != "ok":
            failed += 1
    return {
        "total": len(reports),
        "ok": len(reports) - failed,
        "failed": failed,
        "reports": reports,
    }
