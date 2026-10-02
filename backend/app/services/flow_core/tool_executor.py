"""Flow 工具节点的真实执行器注册表（AUD-14）。

原实现里 ``action_tool`` 节点只往 ``last_output`` 写一句"已执行动作"然后自动推进，
既没有调用 ``bound_tools`` 里的任何工具，也没有任何产物可以核对。本模块把这件事
变成一件只有注册了真实执行器才可能发生的事：

- 节点未绑定任何工具  → :class:`FlowToolError`（显式失败，绝不记为已执行）
- 绑定了未注册的工具  → :class:`FlowToolNotConfigured`（能力未实现，unsupported）
- 注册了真实执行器    → 真正 await 执行器，结果写入 ``state.tool_results``
- 演示模式            → 只写 ``state.simulations``，并在文案里标注 DEMO 模拟

演示模式（``settings.DEMO_MODE_ENABLED``）永远不能把模拟结果写进 ``tool_results``，
也不能产生任何"已执行/已完成"语义。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List

from app.config import settings
from app.services.flow_core.schema import FlowExecutionState, FlowNode

logger = logging.getLogger(__name__)


class FlowToolError(RuntimeError):
    """工具节点执行失败（显式失败，不做任何模拟回退）。"""


class FlowToolNotConfigured(FlowToolError):
    """绑定的工具没有任何已注册的真实执行器 —— 能力未实现。"""


# 执行器签名：拿到节点与执行状态，返回该次执行的真实产物（必须是可核对的字典）。
FlowToolHandler = Callable[[FlowNode, FlowExecutionState], Awaitable[Dict[str, Any]]]

_TOOL_REGISTRY: Dict[str, FlowToolHandler] = {}


def register_flow_tool(name: str, handler: FlowToolHandler) -> None:
    """注册一个真实工具执行器。同名重复注册会覆盖，便于测试与热插拔。"""
    if not name or not callable(handler):
        raise ValueError("工具名不能为空，且执行器必须可调用")
    _TOOL_REGISTRY[name] = handler


def unregister_flow_tool(name: str) -> None:
    _TOOL_REGISTRY.pop(name, None)


def registered_tool_names() -> List[str]:
    return sorted(_TOOL_REGISTRY)


def demo_mode_enabled() -> bool:
    """演示模式开关（config.DEMO_MODE_ENABLED，默认关闭）。"""
    return bool(getattr(settings, "DEMO_MODE_ENABLED", False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def execute_bound_tools(
    node: FlowNode,
    state: FlowExecutionState,
) -> Dict[str, Any]:
    """执行 ``action_tool`` 节点绑定的全部工具。

    :returns: 真实执行产物（节点 ID → 产物字典）。演示模式下模拟产物不进入该返回值。
    :raises FlowToolError: 未绑定工具 / 工具未注册 / 执行器报错。
    """
    if not node.bound_tools:
        raise FlowToolError(
            f"节点 [{node.node_id} {node.name}] 未绑定任何工具，拒绝在无真实执行的情况下推进"
            "（系统不允许把模拟记为已执行动作）"
        )

    if demo_mode_enabled():
        missing = [t for t in node.bound_tools if t not in _TOOL_REGISTRY]
        if missing:
            # 演示模式：明确标记为模拟，不产生任何"已执行"语义。
            record = {
                "node_id": node.node_id,
                "node_name": node.name,
                "mode": "demo",
                "simulated_tools": missing,
                "at": _now(),
            }
            state.simulations.append(record)
            return {}

    results: Dict[str, Any] = {}
    for tool_name in node.bound_tools:
        handler = _TOOL_REGISTRY.get(tool_name)
        if handler is None:
            raise FlowToolNotConfigured(
                f"工具 '{tool_name}'（节点 [{node.node_id}]）没有注册任何真实执行器，"
                "该能力尚未实现，不能按已完成处理"
            )
        try:
            artifact = await handler(node, state)
        except FlowToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - 执行器异常统一转成显式失败
            raise FlowToolError(
                f"工具 '{tool_name}'（节点 [{node.node_id}]）执行失败: {exc}"
            ) from exc
        results[tool_name] = artifact
    return results


__all__ = [
    "FlowToolError",
    "FlowToolHandler",
    "FlowToolNotConfigured",
    "demo_mode_enabled",
    "execute_bound_tools",
    "register_flow_tool",
    "registered_tool_names",
    "unregister_flow_tool",
]
