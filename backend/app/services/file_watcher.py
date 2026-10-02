"""文件监控服务（基于 watchdog）。

监控指定文件夹，文件变化时触发增量更新：
- 新增文件：处理并向量化
- 修改文件：删除旧向量，重新处理
- 删除文件：清理向量

watchdog 未安装时优雅降级，提供日志提示。
"""
import asyncio
import concurrent.futures
import logging
import os

from sqlalchemy.exc import SQLAlchemyError
from app.utils.metrics import errors_total

import httpx
import chromadb.errors

logger = logging.getLogger(__name__)

# 懒加载 watchdog
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    Observer = None
    FileSystemEventHandler = object
    FileSystemEvent = object
    WATCHDOG_AVAILABLE = False
    logger.warning("watchdog 未安装，文件监控功能不可用。请运行 pip install watchdog 启用。")


class FileChangeHandler(FileSystemEventHandler):
    """文件变化事件处理器。"""

    def __init__(self, agent_id: str, folder_path: str, loop: asyncio.AbstractEventLoop):
        self.agent_id = agent_id
        self.folder_path = folder_path
        self.loop = loop
        # 防抖：避免短时间内多次触发
        self._pending_tasks: dict[str, "concurrent.futures.Future"] = {}
        self._debounce_seconds = 1.0

    def _on_file_changed(self, file_path: str, event_type: str):
        # 在事件循环中调度异步处理
        # 防抖：取消之前的 pending 任务，重新计时
        previous = self._pending_tasks.get(file_path)
        if previous is not None:
            previous.cancel()

        # watchdog 事件来自独立线程，需用 run_coroutine_threadsafe 调度到事件循环
        future = asyncio.run_coroutine_threadsafe(
            self._debounced_handle(file_path, event_type), self.loop
        )
        self._pending_tasks[file_path] = future

    async def _debounced_handle(self, file_path: str, event_type: str):
        """防抖处理：等待一小段时间，若未被取消则执行增量更新。"""
        try:
            await asyncio.sleep(self._debounce_seconds)
        except asyncio.CancelledError:
            return
        self._pending_tasks.pop(file_path, None)
        await self._handle_change(file_path, event_type)

    async def _handle_change(self, file_path: str, event_type: str):
        """实际处理文件变化：触发增量更新。"""
        logger.info(
            f"检测到文件变化 ({event_type}): {file_path}，触发增量更新 (agent={self.agent_id})"
        )
        # 使用独立 session 执行后台写操作（SQLite WAL 模式要求）
        from app.database import async_session_factory
        from app.services.incremental_updater import incremental_update

        try:
            async with async_session_factory() as db:
                stats = await incremental_update(
                    db, self.agent_id, self.folder_path
                )
                logger.info(f"增量更新完成 (agent={self.agent_id}): {stats}")
        except SQLAlchemyError as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"增量更新数据库失败 (agent={self.agent_id}): {e}", exc_info=True)
        except (OSError, ValueError) as e:
            logger.error(f"增量更新路径或参数错误 (agent={self.agent_id}): {e}", exc_info=True)
        except (RuntimeError, TypeError, httpx.HTTPError, chromadb.errors.ChromaError, KeyError, AttributeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"增量更新失败（未预期错误） (agent={self.agent_id}): {e}", exc_info=True)

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._on_file_changed(event.src_path, "created")

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._on_file_changed(event.src_path, "modified")

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            self._on_file_changed(event.src_path, "deleted")


class FileWatcherService:
    """文件监控服务单例。"""

    def __init__(self):
        self._observers: dict[str, "Observer"] = {}  # agent_id -> Observer
        self._started = False

    def is_available(self) -> bool:
        return WATCHDOG_AVAILABLE

    async def start_watching(self, agent_id: str, folder_path: str) -> bool:
        """开始监控指定文件夹。返回是否成功启动。"""
        if not WATCHDOG_AVAILABLE:
            logger.warning("watchdog 未安装，无法启动文件监控")
            return False
        if agent_id in self._observers:
            await self.stop_watching(agent_id)

        # P0-04: 路径安全校验，防止递归监控任意目录（如 / 或 C:\）
        from app.services.path_security import validate_path, PathSecurityError
        try:
            folder_path = validate_path(folder_path, must_exist=True)
        except PathSecurityError as e:
            logger.warning(f"文件夹路径校验失败，无法启动监控: {e}")
            return False
        except FileNotFoundError:
            logger.warning(f"文件夹不存在，无法启动监控: {folder_path}")
            return False

        if not os.path.isdir(folder_path):
            logger.warning(f"路径不是目录: {folder_path}")
            return False

        loop = asyncio.get_running_loop()
        handler = FileChangeHandler(agent_id, folder_path, loop)
        observer = Observer()
        observer.schedule(handler, folder_path, recursive=True)
        observer.start()
        self._observers[agent_id] = observer
        logger.info(f"已启动文件监控: agent={agent_id}, folder={folder_path}")
        return True

    async def stop_watching(self, agent_id: str) -> None:
        """停止监控。"""
        observer = self._observers.pop(agent_id, None)
        if observer:
            observer.stop()
            # P0-04: 加超时防止永久阻塞
            observer.join(timeout=5)
            if observer.is_alive():
                logger.warning(f"observer 未能正常停止: agent={agent_id}")
            else:
                logger.info(f"已停止文件监控: agent={agent_id}")

    async def stop_all(self) -> None:
        """停止所有监控。"""
        agent_ids = list(self._observers.keys())
        for agent_id in agent_ids:
            await self.stop_watching(agent_id)


file_watcher_service = FileWatcherService()
