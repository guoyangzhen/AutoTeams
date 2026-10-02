"""O-07: Loop 闭环后台自动优化定时任务（APScheduler）。

设计要点：
- 使用 AsyncIOScheduler，在 FastAPI lifespan 的事件循环内启动/关闭。
- 后台任务写 DB 必须用独立 session（async_session_factory()），
  与请求主 session 隔离，避免 SQLite WAL 下的 "database is locked"。
- 每个 Agent 独立 try/except + 独立 session，单点失败不影响其他 Agent。
- 自动优化（03:00 UTC）：对 status="ready" 的 Agent 调 loop_engine.optimize_retrieval，
  产出 OptimizationHistory(type="optimization", applied=False) 供后续一键应用。
- 知识缺口扫描（04:00 UTC）：调 loop_engine.analyze_knowledge_gaps，
  产出 OptimizationHistory(type="gap", applied=False)。
"""
import logging
from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, func, and_, text

from app.config import settings
from app.utils.branding import LEGACY_STATE_NAMESPACE
from app.database import async_session_factory, engine

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.loop_engine import loop_engine

logger = logging.getLogger(__name__)

# 模块级调度器实例：构造不需要事件循环，start() 才需要（在 lifespan 内调用）
scheduler = AsyncIOScheduler()

_AUTO_OPTIMIZE_JOB_ID = "auto_optimize_all_agents"
_AUTO_SCAN_GAPS_JOB_ID = "auto_scan_knowledge_gaps"


@asynccontextmanager
async def _scheduler_leader_lock(lock_name: str):
    """跨实例排他执行定时任务。

    PostgreSQL 使用 session-level advisory lock，并在整个任务期间持有独立连接；
    SQLite 开发模式不支持 advisory lock，保留单实例运行语义。锁不可用时调用方安全跳过，
    不会重复触发 LLM/写库副作用。
    """
    if not settings.SCHEDULER_LEADER_LOCK_ENABLED or engine.dialect.name != "postgresql":
        yield True
        return

    async with engine.connect() as conn:
        acquired = bool(
            (await conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:lock_name))"),
                {"lock_name": f"{LEGACY_STATE_NAMESPACE}:scheduler:{lock_name}"},
            )).scalar()
        )
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:lock_name))"),
                {"lock_name": f"{LEGACY_STATE_NAMESPACE}:scheduler:{lock_name}"},
            )
            await conn.commit()


async def _run_locked_schedule(
    lock_name: str,
    callback: Callable[[], Awaitable[None]],
) -> None:
    async with _scheduler_leader_lock(lock_name) as acquired:
        if not acquired:
            logger.info("APScheduler: 其他实例持有任务锁，跳过 %s", lock_name)
            return
        await callback()


async def _run_auto_optimize_locked() -> None:
    await _run_locked_schedule(_AUTO_OPTIMIZE_JOB_ID, auto_optimize_all_agents)


async def _run_auto_gap_scan_locked() -> None:
    await _run_locked_schedule(_AUTO_SCAN_GAPS_JOB_ID, auto_scan_knowledge_gaps)


async def _list_ready_agents() -> list[tuple[str, str]]:
    """用一个独立 session 取出所有 status='ready' 的 Agent (id, name)。"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(Agent.id, Agent.name).where(Agent.status == "ready")
        )
        return [(str(row[0]), str(row[1])) for row in result.fetchall()]


async def _derive_query_feedback(db, agent_id: str) -> tuple[str | None, str]:
    """从近 30 天对话中派生优化所需的 query / feedback。

    - query：最近一条用户提问（截断 500 字符）。
    - feedback：基于不满意反馈数量生成中文反馈文案。
    无近期提问时返回 (None, None)，调用方据此跳过该 Agent。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    user_msg_result = await db.execute(
        select(Message.content)
        .join(Conversation)
        .where(
            and_(
                Conversation.agent_id == agent_id,
                Message.role == "user",
                Message.created_at >= cutoff,
            )
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    row = user_msg_result.first()
    query = row[0] if row else None
    if not query:
        return None, None

    dissatisfied_result = await db.execute(
        select(func.count(Message.id))
        .join(Conversation)
        .where(
            and_(
                Conversation.agent_id == agent_id,
                Message.role == "assistant",
                Message.satisfaction.in_(["unsatisfied", "dissatisfied"]),
                Message.created_at >= cutoff,
            )
        )
    )
    dissatisfied_count = int(dissatisfied_result.scalar() or 0)
    if dissatisfied_count > 0:
        feedback = (
            f"近 30 天有 {dissatisfied_count} 条不满意反馈，"
            f"请优化检索结果的相关性与完整性。"
        )
    else:
        feedback = "请基于近期高频问题优化检索结果的相关性。"
    return query[:500], feedback


async def auto_optimize_all_agents() -> None:
    """定时任务：对所有 ready Agent 执行检索优化分析。

    每个 Agent 独立 session + 独立 try/except；optimize_retrieval 内部
    会持久化 OptimizationHistory(type="optimization", applied=False)。
    """
    logger.info("APScheduler: 开始自动检索优化（全部 ready Agent）")
    try:
        agents = await _list_ready_agents()
    except Exception as e:
        logger.error(f"APScheduler: 获取 ready Agent 列表失败: {e}", exc_info=True)
        return

    if not agents:
        logger.info("APScheduler: 无 ready 状态 Agent，跳过自动优化")
        return

    success = 0
    skipped = 0
    failed = 0
    for agent_id, agent_name in agents:
        try:
            async with async_session_factory() as db:
                query, feedback = await _derive_query_feedback(db, agent_id)
                if not query:
                    skipped += 1
                    logger.info(
                        f"APScheduler: Agent {agent_name}({agent_id}) 无近期提问，跳过"
                    )
                    continue
                await loop_engine.optimize_retrieval(db, agent_id, query, feedback)
                # D4 M14: 优化后检测 RAG 质量退化，必要时自动回滚
                rollback_result = await loop_engine.auto_rollback_if_degraded(
                    db, agent_id
                )
                if rollback_result.get("triggered"):
                    logger.warning(
                        f"APScheduler: Agent {agent_name}({agent_id}) 触发自动回滚，"
                        f"目标版本 {rollback_result.get('rollback_version_id')}"
                    )
                success += 1
                logger.info(
                    f"APScheduler: Agent {agent_name}({agent_id}) 自动优化完成"
                )
        except Exception as e:
            failed += 1
            logger.error(
                f"APScheduler: Agent {agent_name}({agent_id}) 自动优化失败: {e}",
                exc_info=True,
            )

    logger.info(
        f"APScheduler: 自动优化结束（成功 {success}，跳过 {skipped}，失败 {failed}）"
    )


async def auto_scan_knowledge_gaps() -> None:
    """定时任务：对所有 ready Agent 扫描知识缺口。

    analyze_knowledge_gaps 内部会持久化
    OptimizationHistory(type="gap", applied=False)。
    """
    logger.info("APScheduler: 开始自动知识缺口扫描（全部 ready Agent）")
    try:
        agents = await _list_ready_agents()
    except Exception as e:
        logger.error(f"APScheduler: 获取 ready Agent 列表失败: {e}", exc_info=True)
        return

    if not agents:
        logger.info("APScheduler: 无 ready 状态 Agent，跳过知识缺口扫描")
        return

    success = 0
    failed = 0
    for agent_id, agent_name in agents:
        try:
            async with async_session_factory() as db:
                # analyze_knowledge_gaps 内部写 OptimizationHistory(type="gap", applied=False)
                await loop_engine.analyze_knowledge_gaps(db, agent_id, days=30)
                success += 1
                logger.info(
                    f"APScheduler: Agent {agent_name}({agent_id}) 知识缺口扫描完成"
                )
        except Exception as e:
            failed += 1
            logger.error(
                f"APScheduler: Agent {agent_name}({agent_id}) 知识缺口扫描失败: {e}",
                exc_info=True,
            )

    logger.info(
        f"APScheduler: 知识缺口扫描结束（成功 {success}，失败 {failed}）"
    )


def start_scheduler() -> None:
    """启动 APScheduler 并注册两个 cron 任务。

    幂等：未启用 / 已运行时安全跳过。启动失败仅记录日志，不阻断应用启动。
    """
    if not settings.LOOP_SCHEDULER_ENABLED:
        logger.info("APScheduler: LOOP_SCHEDULER_ENABLED=false，跳过调度器启动")
        return
    if scheduler.running:
        logger.warning("APScheduler: 调度器已在运行，跳过重复启动")
        return

    try:
        scheduler.add_job(
                        _run_auto_optimize_locked,

            CronTrigger(
                hour=settings.LOOP_AUTO_OPTIMIZE_HOUR,
                minute=settings.LOOP_AUTO_OPTIMIZE_MINUTE,
                timezone="UTC",
            ),
            id=_AUTO_OPTIMIZE_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
                        _run_auto_gap_scan_locked,

            CronTrigger(
                hour=settings.LOOP_AUTO_SCAN_GAPS_HOUR,
                minute=settings.LOOP_AUTO_SCAN_GAPS_MINUTE,
                timezone="UTC",
            ),
            id=_AUTO_SCAN_GAPS_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        logger.info(
            "APScheduler: 调度器已启动"
            f"（自动优化 {settings.LOOP_AUTO_OPTIMIZE_HOUR:02d}:{settings.LOOP_AUTO_OPTIMIZE_MINUTE:02d} UTC / "
            f"知识缺口扫描 {settings.LOOP_AUTO_SCAN_GAPS_HOUR:02d}:{settings.LOOP_AUTO_SCAN_GAPS_MINUTE:02d} UTC）"
        )
        # WT4: 进化层定时任务注册（加性追加，与既有 loop_engine 任务共存）
        _register_evolution_jobs()
    except Exception as e:
        # 调度器是非关键依赖，启动失败不应阻断主服务
        logger.error(f"APScheduler: 调度器启动失败（非致命）: {e}", exc_info=True)


def shutdown_scheduler() -> None:
    """优雅关闭调度器。wait=False 避免阻塞关闭流程。"""
    if not scheduler.running:
        return
    try:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler: 调度器已关闭")
    except Exception as e:
        logger.error(f"APScheduler: 调度器关闭失败: {e}", exc_info=True)


# ============================================================
# WT4 进化层定时任务（加性追加，不修改以上既有函数）
# ============================================================
# 三个进化层定时任务：
# 1. 企业级持续优化分析（05:00 UTC）：continuous_optimizer.run_enterprise_optimization_background
# 2. AI 组织分析指标快照（06:00 UTC）：org_analytics.get_metrics（持久化 5 类指标）
# 3. Advisor 优化建议生成（07:00 UTC）：advisor.generate_suggestions（生成 4 类建议）

from app.models.enterprise import Enterprise  # noqa: E402

_EVOLUTION_OPTIMIZE_JOB_ID = "evolution_continuous_optimize"
_EVOLUTION_METRICS_JOB_ID = "evolution_org_metrics_snapshot"
_EVOLUTION_ADVISOR_JOB_ID = "evolution_advisor_generate"

# 进化层定时任务配置由 Settings 显式声明，环境变量可可靠覆盖。
_EVOLUTION_SCHEDULER_ENABLED = settings.EVOLUTION_SCHEDULER_ENABLED
_EVOLUTION_OPTIMIZE_HOUR = settings.EVOLUTION_OPTIMIZE_HOUR
_EVOLUTION_OPTIMIZE_MINUTE = settings.EVOLUTION_OPTIMIZE_MINUTE
_EVOLUTION_METRICS_HOUR = settings.EVOLUTION_METRICS_HOUR
_EVOLUTION_METRICS_MINUTE = settings.EVOLUTION_METRICS_MINUTE
_EVOLUTION_ADVISOR_HOUR = settings.EVOLUTION_ADVISOR_HOUR
_EVOLUTION_ADVISOR_MINUTE = settings.EVOLUTION_ADVISOR_MINUTE


async def _list_active_enterprises() -> list[tuple[str, str]]:
    """用一个独立 session 取出所有 is_active=True 的企业 (id, name)。

    供进化层批量任务遍历，避免在请求主 session 中累积大量读操作。
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(Enterprise.id, Enterprise.name).where(
                Enterprise.is_active.is_(True)
            )
        )
        return [(str(row[0]), str(row[1])) for row in result.fetchall()]


async def _run_evolution_optimize_locked() -> None:
    await _run_locked_schedule(_EVOLUTION_OPTIMIZE_JOB_ID, evolution_continuous_optimize)


async def _run_evolution_metrics_locked() -> None:
    await _run_locked_schedule(_EVOLUTION_METRICS_JOB_ID, evolution_org_metrics_snapshot)


async def _run_evolution_advisor_locked() -> None:
    await _run_locked_schedule(_EVOLUTION_ADVISOR_JOB_ID, evolution_advisor_generate)


async def evolution_continuous_optimize() -> None:

    """WT4 定时任务：对所有企业执行持续优化分析。

    委托 continuous_optimizer.run_enterprise_optimization_background（内部使用独立 session），
    聚合 loop_engine 反馈分析 + rag_evaluator 质量评估，生成优化项。
    """
    logger.info("APScheduler[WT4]: 开始企业级持续优化分析")
    try:
        enterprises = await _list_active_enterprises()
    except Exception as e:
        logger.error(
            f"APScheduler[WT4]: 获取企业列表失败: {e}", exc_info=True
        )
        return

    if not enterprises:
        logger.info("APScheduler[WT4]: 无活跃企业，跳过持续优化分析")
        return

    # 延迟导入避免循环依赖（evolution 模块导入 scheduler_service 的场景）
    from app.services.evolution.continuous_optimizer import continuous_optimizer

    success = 0
    failed = 0
    for enterprise_id, enterprise_name in enterprises:
        try:
            # run_enterprise_optimization_background 内部使用 async_session_factory()
            report = await continuous_optimizer.run_enterprise_optimization_background(
                enterprise_id
            )
            item_count = len(report.get("optimization_items", []))
            logger.info(
                f"APScheduler[WT4]: 企业 {enterprise_name}({enterprise_id}) "
                f"持续优化分析完成，优化项 {item_count} 条"
            )
            success += 1
        except Exception as e:
            failed += 1
            logger.error(
                f"APScheduler[WT4]: 企业 {enterprise_name}({enterprise_id}) "
                f"持续优化分析失败: {e}",
                exc_info=True,
            )

    logger.info(
        f"APScheduler[WT4]: 持续优化分析结束（成功 {success}，失败 {failed}）"
    )


async def evolution_org_metrics_snapshot() -> None:
    """WT4 定时任务：对所有企业生成 AI 组织分析指标快照。

    调用 org_analytics.get_metrics（内部持久化 5 类指标到 org_metrics 表），
    每次计算追加一行，保留历史轨迹便于趋势分析。
    """
    logger.info("APScheduler[WT4]: 开始 AI 组织分析指标快照")
    try:
        enterprises = await _list_active_enterprises()
    except Exception as e:
        logger.error(
            f"APScheduler[WT4]: 获取企业列表失败: {e}", exc_info=True
        )
        return

    if not enterprises:
        logger.info("APScheduler[WT4]: 无活跃企业，跳过指标快照")
        return

    from app.services.evolution.org_analytics import org_analytics

    success = 0
    failed = 0
    for enterprise_id, enterprise_name in enterprises:
        # 每个企业独立 session + 独立 try/except（spec §2.3）
        try:
            async with async_session_factory() as db:
                # period 使用当前年月（YYYY-MM）
                period = datetime.now(timezone.utc).strftime("%Y-%m")
                metrics = await org_analytics.get_metrics(
                    db, enterprise_id, period=period
                )
                logger.info(
                    f"APScheduler[WT4]: 企业 {enterprise_name}({enterprise_id}) "
                    f"指标快照完成，成熟度 {metrics.get('maturity_level')}"
                )
                success += 1
        except Exception as e:
            failed += 1
            logger.error(
                f"APScheduler[WT4]: 企业 {enterprise_name}({enterprise_id}) "
                f"指标快照失败: {e}",
                exc_info=True,
            )

    logger.info(
        f"APScheduler[WT4]: 指标快照结束（成功 {success}，失败 {failed}）"
    )


async def evolution_advisor_generate() -> None:
    """WT4 定时任务：对所有企业生成 AI Advisor 优化建议。

    委托 advisor.generate_suggestions（内部 LLM 调用，产出 4 类建议并持久化）。
    LLM 调用前的输入已由 advisor 内部 wrap_untrusted 包裹（spec §2.7）。
    """
    logger.info("APScheduler[WT4]: 开始 AI Advisor 建议生成")
    try:
        enterprises = await _list_active_enterprises()
    except Exception as e:
        logger.error(
            f"APScheduler[WT4]: 获取企业列表失败: {e}", exc_info=True
        )
        return

    if not enterprises:
        logger.info("APScheduler[WT4]: 无活跃企业，跳过建议生成")
        return

    from app.services.evolution.advisor import advisor_service

    success = 0
    failed = 0
    for enterprise_id, enterprise_name in enterprises:
        try:
            async with async_session_factory() as db:
                suggestions = await advisor_service.generate_suggestions(
                    db, enterprise_id
                )
                logger.info(
                    f"APScheduler[WT4]: 企业 {enterprise_name}({enterprise_id}) "
                    f"建议生成完成，{len(suggestions)} 条建议"
                )
                success += 1
        except Exception as e:
            failed += 1
            logger.error(
                f"APScheduler[WT4]: 企业 {enterprise_name}({enterprise_id}) "
                f"建议生成失败: {e}",
                exc_info=True,
            )

    logger.info(
        f"APScheduler[WT4]: 建议生成结束（成功 {success}，失败 {failed}）"
    )


def _register_evolution_jobs() -> None:
    """注册 WT4 进化层定时任务（加性追加，幂等）。

    在 start_scheduler() 末尾调用，与既有 loop_engine 任务共存。
    若进化层调度未启用则跳过。
    """
    if not _EVOLUTION_SCHEDULER_ENABLED:
        logger.info("APScheduler[WT4]: EVOLUTION_SCHEDULER_ENABLED=false，跳过进化层任务注册")
        return

    try:
        scheduler.add_job(
                        _run_evolution_optimize_locked,

            CronTrigger(
                hour=_EVOLUTION_OPTIMIZE_HOUR,
                minute=_EVOLUTION_OPTIMIZE_MINUTE,
                timezone="UTC",
            ),
            id=_EVOLUTION_OPTIMIZE_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
                        _run_evolution_metrics_locked,

            CronTrigger(
                hour=_EVOLUTION_METRICS_HOUR,
                minute=_EVOLUTION_METRICS_MINUTE,
                timezone="UTC",
            ),
            id=_EVOLUTION_METRICS_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
                        _run_evolution_advisor_locked,

            CronTrigger(
                hour=_EVOLUTION_ADVISOR_HOUR,
                minute=_EVOLUTION_ADVISOR_MINUTE,
                timezone="UTC",
            ),
            id=_EVOLUTION_ADVISOR_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info(
            "APScheduler[WT4]: 进化层任务已注册"
            f"（持续优化 {_EVOLUTION_OPTIMIZE_HOUR:02d}:{_EVOLUTION_OPTIMIZE_MINUTE:02d} UTC / "
            f"指标快照 {_EVOLUTION_METRICS_HOUR:02d}:{_EVOLUTION_METRICS_MINUTE:02d} UTC / "
            f"建议生成 {_EVOLUTION_ADVISOR_HOUR:02d}:{_EVOLUTION_ADVISOR_MINUTE:02d} UTC）"
        )
    except Exception as e:
        logger.error(
            f"APScheduler[WT4]: 进化层任务注册失败（非致命）: {e}",
            exc_info=True,
        )
