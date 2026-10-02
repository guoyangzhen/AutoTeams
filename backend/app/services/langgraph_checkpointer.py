"""LangGraph checkpointer 解析（AUD-16）。

历史问题
--------
1. `agent_graph._get_or_create_checkpointer()` 优先导入 `langgraph.checkpoint.sqlite`，
   但依赖清单与生产锁文件里**没有** `langgraph-checkpoint-sqlite`，导入必然失败，
   于是静默回退 `MemorySaver` —— 等待审批（HITL）的检查点随进程重启全部丢失。
2. 补上依赖后又暴露第二个、更严重的问题：图是 `await app.ainvoke(...)` 调用的，
   而代码装的是**同步** `SqliteSaver`。LangGraph 的异步调用会走
   `aget_tuple` / `aput`，同步实现直接抛
   ``NotImplementedError: The SqliteSaver does not support async methods``。
   也就是说"装上持久化依赖"反而让整条构建链路在运行时崩溃。

本模块的契约
------------
* 使用 **AsyncSqliteSaver**（与 ``ainvoke`` 匹配），在应用启动时异步初始化、
  关闭时异步释放；``build_agent_graph`` 是同步工厂，只做读取；
* 依赖缺失或初始化失败时，**生产环境直接启动失败**（fail-closed），
  不再无声降级到内存；
* 开发/测试环境仍可回退，但必须打 WARNING 并计入错误指标。

``aiosqlite`` 已在 ``requirements.lock`` 中锁定（SQLAlchemy async 驱动本就依赖它），
本模块不引入新的传递依赖。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.config import settings
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


class CheckpointerUnavailableError(RuntimeError):
    """生产环境缺少可用的持久化 checkpointer。"""


#: 已进入上下文的 saver（供业务使用）
_instance: Optional[Any] = None
#: 进入它所用的 async context manager（关闭时只能通过它退出）
_context_manager: Optional[Any] = None
#: 开发/测试环境未初始化时的降级实例
_memory_fallback: Optional[Any] = None


async def init_checkpointer() -> Any:
    """创建并进入持久化 checkpointer。应用 lifespan 启动时调用一次。"""
    global _instance, _context_manager
    if _instance is not None:
        return _instance

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    ckpt_path = settings.LANGGRAPH_CHECKPOINT_PATH
    os.makedirs(os.path.dirname(ckpt_path) or ".", exist_ok=True)
    manager = AsyncSqliteSaver.from_conn_string(ckpt_path)
    # __aenter__ 返回 saver 本身（不是 manager）；退出必须用 manager。
    saver = await manager.__aenter__()
    _context_manager = manager
    _instance = saver
    logger.info("LangGraph AsyncSqliteSaver 已初始化，checkpoint 路径: %s", ckpt_path)
    return _instance


async def shutdown_checkpointer() -> None:
    """关闭 checkpointer 连接。应用 lifespan 关闭时调用。"""
    global _instance, _context_manager
    if _context_manager is not None:
        try:
            await _context_manager.__aexit__(None, None, None)
        except Exception as exc:  # noqa: BLE001 - 关闭失败不应阻断关停
            logger.warning("关闭 LangGraph checkpointer 失败: %s", exc)
    _instance = None
    _context_manager = None


def get_or_create_checkpointer() -> Any:
    """同步访问器，供同步的 `build_agent_graph` 工厂使用。

    正常路径：lifespan 已通过 :func:`init_checkpointer` 建好，直接返回。
    脚本/测试等未走 lifespan 的场景：开发环境回退 MemorySaver（带告警），
    生产环境直接拒绝。
    """
    global _memory_fallback
    if _instance is not None:
        return _instance
    if _memory_fallback is not None:
        return _memory_fallback

    if not settings.DEBUG:
        raise CheckpointerUnavailableError(
            "生产环境必须使用持久化 LangGraph checkpointer，但它尚未初始化。"
            "请确认应用 lifespan 调用了 init_checkpointer()，并检查 "
            f"{settings.LANGGRAPH_CHECKPOINT_PATH} 所在卷是否可写。"
            "生产环境不允许回退 MemorySaver —— 审批检查点会随进程重启丢失。"
        )

    from langgraph.checkpoint.memory import MemorySaver

    logger.warning(
        "LangGraph checkpointer 未初始化，回退到 MemorySaver（仅开发环境）。"
        "HITL 审批状态在进程重启后会丢失。"
    )
    errors_total.labels(module=__name__, exception_type="CheckpointerFallback").inc()
    _memory_fallback = MemorySaver()
    return _memory_fallback


def reset_checkpointer_cache() -> None:
    """清空缓存引用（仅供测试使用）。"""
    global _instance, _context_manager, _memory_fallback
    _instance = None
    _context_manager = None
    _memory_fallback = None


__all__ = [
    "CheckpointerUnavailableError",
    "get_or_create_checkpointer",
    "init_checkpointer",
    "reset_checkpointer_cache",
    "shutdown_checkpointer",
]
