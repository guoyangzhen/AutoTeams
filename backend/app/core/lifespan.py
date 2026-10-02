"""应用生命周期（lifespan）上下文管理。

从 main.py 提取（原 main.py:139-305）。保持行为完全一致，仅做职责拆分：

- ``_verify_database_connection``  启动前自检数据库连通性
- ``_warn_if_metrics_unprotected``  生产环境未配置 METRICS_AUTH_TOKEN 时告警
- ``_reset_interrupted_processing_tasks``  重置上次进程崩溃残留的 processing 任务
- ``_recover_stale_compilation_jobs``  重新入队无有效租约的编译 Job
- ``_start_background_services`` / ``_stop_background_services``  APScheduler 调度器
- ``_resume_file_watchers`` / ``_stop_file_watchers``  知识库增量更新的文件监听

重要：数据库 schema 初始化必须通过 Alembic 迁移完成：
    cd backend && alembic upgrade head
启动时仅做连接校验，不再用 create_all 自动建表。
这避免了 schema 变更只能 drop & recreate 丢数据的问题。
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import engine
from app.utils.metrics import errors_total
from app.services.langgraph_checkpointer import init_checkpointer, shutdown_checkpointer

logger = logging.getLogger(__name__)


async def _verify_database_connection() -> None:
    """启动自检：确认数据库可连接。"""
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    logger.info("数据库连接正常")


def _warn_if_metrics_unprotected() -> None:
    """BE-SEC-05: 生产环境 /metrics 未设置访问控制时告警。"""
    if not settings.DEBUG and not settings.METRICS_AUTH_TOKEN:
        logger.warning(
            "安全警告：生产环境 METRICS_AUTH_TOKEN 未设置，"
            "/metrics 端点可被任意访问。请在 .env 中配置随机 token。"
        )

async def _reset_interrupted_processing_tasks() -> None:
    """把**租约已过期**的 processing 任务放回队列（AUD-17）。

    历史实现把 DB 里所有 `processing` 任务一律标成 failed：多实例部署下，
    任何一个实例滚动更新都会误伤仍由其他实例执行中的任务，数据库状态与真实
    副作用就此分离。现在只回收租约过期或从未持有租约的任务，其他 worker 持有的
    有效租约原样保留。
    """
    if not settings.DEBUG and engine.dialect.name == "postgresql":
        logger.info("生产处理队列恢复由独立 processing-worker 执行")
        return

    from app.database import async_session_factory
    from app.services.processing_queue import recover_stale_processing_tasks

    async with async_session_factory() as session:
        recovered = await recover_stale_processing_tasks(session)
    if recovered:
        logger.warning("已将 %s 个租约过期的 processing 任务放回队列", recovered)


async def _recover_stale_compilation_jobs() -> None:
    """耐久编译任务：将无有效租约的历史 running Job 重新入队。

    无法恢复的旧 Job 明确标记失败，避免 UI 永久显示 running。
    独立 compilation-worker 随后领取任务。
    """
    if not settings.DEBUG and engine.dialect.name == "postgresql":
        logger.info("生产编译队列恢复由独立 compilation-worker 执行")
        return

    from app.services.compiler.job_queue import recover_stale_compilation_jobs

    recovery = await recover_stale_compilation_jobs()
    if recovery["requeued"] or recovery["failed"]:
        logger.warning(
            "编译任务启动恢复完成: requeued=%s failed=%s",
            recovery["requeued"], recovery["failed"],
        )


def _start_background_services() -> None:
    """启动全部 APScheduler 后台调度器（任一失败均不阻断启动）。"""
    # O-07: Loop 闭环自动优化 / 知识缺口扫描
    # 调度器内部会判断 LOOP_SCHEDULER_ENABLED，测试环境已通过环境变量关闭
    try:
        from app.services.scheduler_service import start_scheduler

        start_scheduler()
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"启动 APScheduler 失败（非致命）: {e}", exc_info=True)

    # D3-M15: 处理任务监控调度器（每小时取消超时僵尸任务）
    # 复用 LOOP_SCHEDULER_ENABLED 开关；内部使用独立 AsyncIOScheduler 实例
    try:
        from app.services.process_monitor import start_process_monitor

        start_process_monitor()
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"启动 ProcessMonitor 失败（非致命）: {e}", exc_info=True)

    # 本地工具桥接 P2：启动本地授权清理调度器（每小时清理 token 过期/授权到期的授权）
    # 复用 LOOP_SCHEDULER_ENABLED 开关；内部使用独立 AsyncIOScheduler 实例
    try:
        from app.services.runner_cleanup import start_runner_cleanup

        start_runner_cleanup()
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"启动 RunnerCleanup 失败（非致命）: {e}", exc_info=True)


def _stop_background_services() -> None:
    """关闭全部 APScheduler 后台调度器。"""
    # 先关闭调度器，避免后台任务在数据库连接释放后仍尝试写入
    try:
        from app.services.scheduler_service import shutdown_scheduler

        shutdown_scheduler()
    except Exception as e:
        logger.warning(f"关闭 APScheduler 失败（非致命）: {e}", exc_info=True)

    try:
        from app.services.process_monitor import stop_process_monitor

        stop_process_monitor()
    except Exception as e:
        logger.warning(f"关闭 ProcessMonitor 失败（非致命）: {e}", exc_info=True)

    try:
        from app.services.runner_cleanup import stop_runner_cleanup

        stop_runner_cleanup()
    except Exception as e:
        logger.warning(f"关闭 RunnerCleanup 失败（非致命）: {e}", exc_info=True)


async def _resume_file_watchers() -> None:
    """D3-6.7: 为所有 ready 状态且有 folder_path 的 Agent 恢复文件监听。

    服务重启后，此前由 tester_node 自动启动的 watchdog 会随进程退出而失效，
    需在 lifespan 中重新拉起，保证知识库增量更新的持续闭环。
    """
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models.agent import Agent
    from app.services.file_watcher import file_watcher_service

    if not file_watcher_service.is_available():
        logger.warning("watchdog 未安装，跳过启动时文件监听恢复")
        return

    async with async_session_factory() as session:
        result = await session.execute(
            select(Agent.id, Agent.folder_path).where(
                Agent.status == "ready",
                Agent.folder_path.isnot(None),
            )
        )
        ready_agents = result.fetchall()

    resumed = 0
    for agent_id, folder_path in ready_agents:
        if not folder_path:
            continue
        try:
            started = await file_watcher_service.start_watching(
                str(agent_id), folder_path
            )
            if started:
                resumed += 1
        except (OSError, ValueError, RuntimeError) as e:
            logger.warning(f"恢复 Agent {agent_id} 文件监听失败: {e}", exc_info=True)
    if ready_agents:
        logger.info(
            f"D3-6.7: 已恢复 {resumed}/{len(ready_agents)} 个 ready Agent 的文件监听"
        )


async def _stop_file_watchers() -> None:
    """D3-6.7: 关闭所有文件监听，释放 watchdog observer 线程。"""
    from app.services.file_watcher import file_watcher_service

    await file_watcher_service.stop_all()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动自检 → 后台服务 → yield → 优雅关闭。"""
    logger.info("正在启动 AutoTeams 后端服务...")
    await _verify_database_connection()
    _warn_if_metrics_unprotected()

    try:
        await _reset_interrupted_processing_tasks()
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"重置中断任务时出错（非致命）: {e}")

    try:
        await _recover_stale_compilation_jobs()
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"恢复耐久编译任务失败（非致命）: {e}", exc_info=True)
    _start_background_services()

    # AUD-16: LangGraph 持久化 checkpointer 必须在启动时建立。
    # 失败在生产环境是致命的（审批检查点会丢），开发环境降级继续。
    try:
        await init_checkpointer()
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        if not settings.DEBUG:
            logger.critical(f"初始化 LangGraph 持久化 checkpointer 失败，拒绝启动: {e}", exc_info=True)
            raise
        logger.warning(f"初始化 LangGraph checkpointer 失败（开发环境降级）: {e}", exc_info=True)

    try:
        await _resume_file_watchers()
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"启动时恢复文件监听失败（非致命）: {e}", exc_info=True)

    # AUD-01：注册端侧**授权**执行器（只读）。放在启动全部成功之后、`yield` 之前：
    # 前面任何一步失败（生产环境 checkpointer 初始化失败会直接 raise）都不会留下
    # 一个"已注册但应用其实没起来"的执行器。
    from app.services.mcp.grant_executor import grant_executor
    from app.services.mcp.runner_bridge import register_executor

    register_executor(grant_executor)

    try:
        yield
    finally:
        # 无论正常退出还是上下文抛异常，都必须注销：否则同一进程里（例如测试、
        # 热重载）会残留上一份执行器，端侧读取会打到已经不成立的授权上。
        register_executor(None)

    logger.info("正在关闭 AutoTeams 后端服务...")
    _stop_background_services()

    try:
        await _stop_file_watchers()
    except Exception as e:
        logger.warning(f"关闭文件监听失败（非致命）: {e}", exc_info=True)

    # 技术审计 R2 H5: shutdown_scheduler(wait=False) 立即返回，但调度器/进程监控的
    # 异步任务可能仍在事件循环中执行。直接 engine.dispose() 会关闭连接池，导致
    # 运行中任务的 DB 写入失败（如 OptimizationHistory 记录不完整）。
    # 这里 yield 给事件循环 3 秒宽限期，让即将完成的异步任务收尾。
    # shutdown(wait=True) 在 AsyncIOScheduler 上会死锁（同步等待自身事件循环），
    # 故采用宽限期方案。
    await asyncio.sleep(3)

    try:
        await shutdown_checkpointer()
    except Exception as e:
        logger.warning(f"关闭 LangGraph checkpointer 失败（非致命）: {e}", exc_info=True)

    try:
        await engine.dispose()
    finally:
        # 渠道引导/后台连接与 API 连接角色不同，不能遗留到下一次 lifespan。
        from app import database

        disposed = {id(engine)}
        for name in ("worker_engine", "bootstrap_engine"):
            extra_engine = getattr(database, name, None)
            if extra_engine is None or id(extra_engine) in disposed:
                continue
            disposed.add(id(extra_engine))
            try:
                await extra_engine.dispose()
            except Exception:
                logger.warning("关闭独立数据库连接池失败: %s", name, exc_info=True)
