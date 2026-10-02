"""外部本地 Agent 统一接入与任务委托 API。

AUD-14 修复：原实现在 ``dispatch`` 里直接拼装 ``completed`` 回执并伪造 120ms 耗时，
任何调用都会"成功"。现在：

- 没有注册真实执行器的 Agent → 明确返回 ``501 unsupported / not configured``，
  永远不会出现 ``completed``。
- ``completed`` 只能来自 :class:`ExternalExecutionResult`，且必须携带执行器身份
  ``executor_id`` 与关联 ID ``correlation_id``，并写入审计流水。
- 执行器不可用（断连/报错）→ 明确失败，不产生成功回执。
- 演示模式（``settings.DEMO_MODE_ENABLED``，默认关闭；生产需显式确认串）只回
  ``demo_simulated``，并在结果与清单元数据上标注 ``demo: true``。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.utils.audit import log_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external-agents", tags=["外部Agent接入"])


class ExternalAgentItem(BaseModel):
    id: str
    name: str
    type: str = Field(description="codex | claude | mcp | custom")
    status: str = Field(description="offline | detected | connected | busy")
    command_path: Optional[str] = None
    description: str
    capabilities: List[str] = Field(default_factory=list)
    # AUD-14：UI 可见的执行能力事实 —— 是否真的存在已注册的执行器
    has_executor: bool = False
    # 演示模式开启时，UI 必须能据此标注 DEMO
    demo: bool = False


class ConnectAgentRequest(BaseModel):
    options: Optional[Dict[str, Any]] = None


class DispatchTaskRequest(BaseModel):
    prompt: str
    context: Optional[Dict[str, Any]] = None
    timeout_seconds: Optional[float] = 60.0


@dataclass(frozen=True)
class ExternalExecutionResult:
    """真实外部执行器返回的结果。

    ``executor_id`` 与 ``correlation_id`` 为空时不得视为完成 —— API 会拒绝把
    这种结果标记为 ``completed``。
    """

    output: str
    executor_id: str
    correlation_id: str
    artifacts: List[str] = field(default_factory=list)
    demo: bool = False


class ExternalAgentDisconnected(RuntimeError):
    """执行器当前不可用（进程未启动/连接断开）。"""


class ExternalExecutionError(RuntimeError):
    """执行器运行失败。"""


# 执行器签名：接收分派请求，返回真实执行结果。
ExternalAgentExecutor = Callable[[DispatchTaskRequest], Awaitable[ExternalExecutionResult]]

_EXECUTORS: Dict[str, ExternalAgentExecutor] = {}


def register_executor(agent_id: str, executor: ExternalAgentExecutor) -> None:
    """为某个外部 Agent 注册真实执行器（由本地桥接在连接建立时注册）。"""
    if not agent_id or not callable(executor):
        raise ValueError("agent_id 不能为空，且执行器必须可调用")
    _EXECUTORS[agent_id] = executor


def unregister_executor(agent_id: str) -> None:
    _EXECUTORS.pop(agent_id, None)


def has_executor(agent_id: str) -> bool:
    return agent_id in _EXECUTORS


def _demo_mode_enabled() -> bool:
    return bool(getattr(settings, "DEMO_MODE_ENABLED", False))


# 外部 Agent 能力声明表：这里只描述"侦测到的能力"，不代表任何执行已经发生。
# 初始状态一律 detected；只有真实执行器注册且连接成功才会变成 connected。
_AGENT_CATALOG: Dict[str, Dict[str, Any]] = {
    "codex-cli": {
        "id": "codex-cli",
        "name": "OpenAI Codex 本地代码专员",
        "type": "codex",
        "command_path": "codex",
        "description": "负责本地代码编写、AST 语法树重构与自动化单元测试补全",
        "capabilities": ["code_generation", "ast_refactoring", "unit_test_authoring"],
    },
    "claude-code": {
        "id": "claude-code",
        "name": "Claude Code 架构分析师",
        "type": "claude",
        "command_path": "claude",
        "description": "负责复杂跨文件逻辑分析、依赖链排查与技术方案编制",
        "capabilities": ["architecture_audit", "cross_file_reasoning", "dependency_check"],
    },
    "mcp-local": {
        "id": "mcp-local",
        "name": "标准 MCP (Model Context Protocol) 运行时",
        "type": "mcp",
        "command_path": None,
        "description": "接入本地标准 MCP Tools 与企业私有文件资源通道",
        "capabilities": ["tool_execution", "resource_reading", "prompt_templates"],
    },
}

# agent_id -> 连接状态（由 connect/disconnect 端点驱动，不代表执行能力）
_CONNECTION_STATE: Dict[str, str] = {}


def _item_for(agent_id: str) -> ExternalAgentItem:
    spec = _AGENT_CATALOG[agent_id]
    demo = _demo_mode_enabled()
    return ExternalAgentItem(
        **spec,
        status=_CONNECTION_STATE.get(agent_id, "detected"),
        has_executor=has_executor(agent_id),
        demo=demo,
    )


def _unsupported(agent_id: str, reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "success": False,
            "status": "unsupported",
            "agent_id": agent_id,
            "detail": reason,
        },
    )


@router.get("", response_model=List[ExternalAgentItem])
async def list_external_agents(current_user=Depends(get_current_user)):
    """获取已侦测的本地外部 Agent 清单（含是否真的配置了执行器）。"""
    return [_item_for(agent_id) for agent_id in _AGENT_CATALOG]


@router.post("/{agent_id}/connect")
async def connect_external_agent(
    agent_id: str,
    req: ConnectAgentRequest,
    current_user=Depends(get_current_user),
):
    """连接外部 Agent。没有真实执行器时明确拒绝，不伪造连接成功。"""
    if agent_id not in _AGENT_CATALOG:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "detail": f"未找到指定的外部 Agent: {agent_id}"},
        )
    if not has_executor(agent_id):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "success": False,
                "status": "not_configured",
                "agent_id": agent_id,
                "detail": "该外部 Agent 没有注册任何真实执行器，无法连接（能力未实现）",
            },
        )
    _CONNECTION_STATE[agent_id] = "connected"
    logger.info("已连接外部 Agent: %s (用户: %s)", _AGENT_CATALOG[agent_id]["name"], current_user.email)
    return {"success": True, "message": f"已连接 {_AGENT_CATALOG[agent_id]['name']}", "agent": _item_for(agent_id)}


@router.post("/{agent_id}/dispatch", response_model=None)
async def dispatch_task_to_agent(
    agent_id: str,
    req: DispatchTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """向外部 Agent 分派子任务。

    只有真实执行器返回带 ``executor_id`` + ``correlation_id`` 的结果时才会出现
    ``completed``；否则是明确的 unsupported / failed，并写入审计流水。
    """
    if agent_id not in _AGENT_CATALOG:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "detail": f"未找到指定的外部 Agent: {agent_id}"},
        )

    task_id = f"task-ext-{uuid.uuid4().hex[:8]}"
    executor = _EXECUTORS.get(agent_id)

    if executor is None:
        if _demo_mode_enabled():
            # 演示模式：明确标注为模拟，绝不返回 completed
            body = {
                "success": False,
                "demo": True,
                "status": "demo_simulated",
                "task_id": task_id,
                "agent_id": agent_id,
                "result": (
                    f"[DEMO 模拟 · 非真实执行] 针对「{req.prompt}」的演示回执。"
                    "本次没有启动任何外部进程，不构成履约结果。"
                ),
                "executor_id": None,
                "correlation_id": f"demo-{task_id}",
                "detail": "演示模式已开启但没有注册真实执行器，仅返回标记为 DEMO 的模拟回执",
            }
            await _audit_dispatch(db, current_user, agent_id, task_id, "demo_simulated", None, None)
            await db.commit()
            return JSONResponse(status_code=status.HTTP_200_OK, content=body)
        await _audit_dispatch(db, current_user, agent_id, task_id, "unsupported", None, None)
        await db.commit()
        return _unsupported(
            agent_id,
            "该外部 Agent 未配置真实执行器，分派能力未实现（不返回任何完成回执）",
        )

    start_time = time.perf_counter()
    try:
        result = await executor(req)
    except ExternalAgentDisconnected as exc:
        await _audit_dispatch(db, current_user, agent_id, task_id, "executor_disconnected", None, None)
        await db.commit()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "success": False,
                "status": "executor_disconnected",
                "task_id": task_id,
                "agent_id": agent_id,
                "detail": f"执行器当前不可用: {exc}",
            },
        )
    except Exception as exc:  # noqa: BLE001 - 任何执行异常都必须显式失败
        logger.exception("外部 Agent 执行失败 agent=%s task=%s", agent_id, task_id)
        await _audit_dispatch(db, current_user, agent_id, task_id, "executor_failed", None, None)
        await db.commit()
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "success": False,
                "status": "executor_failed",
                "task_id": task_id,
                "agent_id": agent_id,
                "detail": f"执行器执行失败: {exc}",
            },
        )

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    if not result.executor_id or not result.correlation_id:
        # 没有执行器身份与关联 ID 的结果不允许被当作"完成"
        await _audit_dispatch(db, current_user, agent_id, task_id, "invalid_result", None, None)
        await db.commit()
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "success": False,
                "status": "invalid_execution_result",
                "task_id": task_id,
                "agent_id": agent_id,
                "detail": "执行结果缺少执行器身份(executor_id)或关联 ID(correlation_id)，不予采信",
            },
        )

    if result.demo:
        await _audit_dispatch(
            db, current_user, agent_id, task_id, "demo_simulated", result.executor_id, result.correlation_id
        )
        await db.commit()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": False,
                "demo": True,
                "status": "demo_simulated",
                "task_id": task_id,
                "agent_id": agent_id,
                "result": result.output,
                "executor_id": result.executor_id,
                "correlation_id": result.correlation_id,
                "artifacts": result.artifacts,
                "duration_ms": duration_ms,
            },
        )

    await _audit_dispatch(
        db, current_user, agent_id, task_id, "completed", result.executor_id, result.correlation_id
    )
    await db.commit()
    return {
        "success": True,
        "demo": False,
        "task_id": task_id,
        "agent_id": agent_id,
        "status": "completed",
        "result": result.output,
        "executor_id": result.executor_id,
        "correlation_id": result.correlation_id,
        "artifacts": result.artifacts,
        "duration_ms": duration_ms,
    }


async def _audit_dispatch(
    db: AsyncSession,
    user: User,
    agent_id: str,
    task_id: str,
    outcome: str,
    executor_id: Optional[str],
    correlation_id: Optional[str],
) -> None:
    """分派结果必须进审计流水（含执行器身份与关联 ID）。"""
    await log_audit(
        db,
        user,
        action="external_agent_dispatch",
        resource_type="external_agent",
        resource_id=agent_id,
        details={
            "task_id": task_id,
            "outcome": outcome,
            "executor_id": executor_id,
            "correlation_id": correlation_id,
        },
    )


@router.post("/{agent_id}/disconnect")
async def disconnect_external_agent(
    agent_id: str,
    current_user=Depends(get_current_user),
):
    """断开外部 Agent（同时注销其执行器，断开后不得再分派成功）。"""
    if agent_id not in _AGENT_CATALOG:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"success": False, "detail": f"未找到指定的外部 Agent: {agent_id}"},
        )
    _CONNECTION_STATE[agent_id] = "offline"
    unregister_executor(agent_id)
    return {"success": True, "message": f"已断开 {_AGENT_CATALOG[agent_id]['name']}"}
