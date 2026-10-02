"""AutoTeams 4.0 Local Runner 端侧工具桥接器。

安全边界（AUD-01 / AUD-14）
--------------------------
历史实现把 `runner_read_file` 的 `file_path` 直接交给后端 `open()`，于是任何
已登录用户都能通过 `POST /api/v1/mcp/call` 读取后端进程可访问的任意文件；
`runner_execute_script` 则在完全没有执行器的情况下返回 `simulated_success`；
`runner_system_probe` 报告的其实是**后端**的操作系统信息。

现在的契约：

1. **后端不再打开调用方给出的路径。** 端侧文件操作必须先给出 ``grant_id``
   （用户在本地工具桥接里登记的**授权目录记录**），再由已注册的授权执行器
   （``services.mcp.grant_executor``）转发给该授权对应的本地守护进程。
2. **授权对象是 grant，不是 device。** 授权目录、授权范围与"守护进程是否在线"都
   记录在 ``local_path_grants`` 上；WebSocket 会话按 grant 绑定，端侧进程一次只
   持有一个授权根目录。用 ``device_id`` 去字符串匹配 ``runner_id`` 既不唯一
   （同一 runner 可有多个授权目录），也不是可验证的身份链，因此已从契约移除。
3. **路径只接受授权目录内的相对路径。** 绝对路径、盘符/UNC、**任何 `..` 段**一律
   拒绝，不做任何后端文件系统的兜底；真实边界由端侧在其授权目录内再校验一次。
4. **只接受新契约参数。** ``file_path`` 旧参数不再被静默接受——历史上它正是
   后端任意文件读取的入口，宁可明确失败。
5. **没有执行器就是没有能力。** 未注册执行器时返回 ``success=False`` 与明确的
   ``not configured`` 错误，绝不伪造成功。
6. **错误不外泄。** 执行器抛出的原始异常可能带数据库内容或远端细节，因此只有
   本模块定义的公共错误原样透出，其余一律泛化成结构化失败，并且**不打印文件
   内容**（完整堆栈只进服务端日志）。
7. **探针不冒充。** ``runner_system_probe`` 尚未接入真实端侧探测，明确返回未实现，
   绝不用数据库里的设备档案伪装成端侧健康状况。
8. ``runner_execute_script`` 保持未实现：本桥接器不提供任何宿主命令执行面。
"""
from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.services.mcp.schema import (
    MCPToolDefinition,
    MCPToolParameterSchema,
    MCPToolResult,
)

logger = logging.getLogger(__name__)

#: 单次端侧读取的最大字节数（UTF-8 字节；与历史实现一致，保持调用方契约稳定）。
MAX_READ_BYTES = 4096

#: 未接入真实执行器时统一返回的错误，便于前端与告警识别"能力缺失"。
ERR_NO_EXECUTOR = "端侧执行器未接入：当前部署没有可用的 Local Runner 授权执行器"
ERR_NO_CONTEXT = "缺少调用者身份上下文：请携带已认证用户与企业信息调用该工具"
ERR_GRANT_NOT_ALLOWED = "授权不存在、不属于当前企业/用户，或未处于已连接状态"
ERR_PATH_NOT_ALLOWED = "路径不合法：仅接受授权目录内的相对路径"
ERR_DEVICE_ID_RETIRED = (
    "runner_read_file 不再接受 device_id：请改用 grant_id 指定用户的本地路径授权"
)
ERR_FILE_PATH_RETIRED = (
    "runner_read_file 不再接受 file_path：请改用 relative_path（授权目录内的相对路径）"
)
ERR_UNEXPECTED_FAILURE = "端侧文件读取失败：授权执行器返回了未预期的错误"
ERR_BAD_RESULT_TYPE = "授权执行器返回了非预期的结果类型"
ERR_PROBE_UNAVAILABLE = "未实现：端侧设备探针尚未接入 Local Runner 的真实探测通道"
ERR_SCRIPT_UNAVAILABLE = "未实现：端侧脚本执行尚未接入 Local Runner 授权执行器（AUD-14）"

#: 可以原样透传给调用方的公共错误文本。其余异常一律泛化，避免泄露内部细节。
_PUBLIC_ERRORS = frozenset(
    {
        ERR_NO_EXECUTOR,
        ERR_NO_CONTEXT,
        ERR_GRANT_NOT_ALLOWED,
        ERR_PATH_NOT_ALLOWED,
        ERR_DEVICE_ID_RETIRED,
        ERR_FILE_PATH_RETIRED,
        ERR_UNEXPECTED_FAILURE,
        ERR_BAD_RESULT_TYPE,
        ERR_PROBE_UNAVAILABLE,
        ERR_SCRIPT_UNAVAILABLE,
    }
)


@dataclass(frozen=True)
class MCPToolCallContext:
    """端侧工具调用上下文（由 API 层从已认证用户构造）。"""

    enterprise_id: str
    user_id: str


@dataclass(frozen=True)
class GrantFileRead:
    """端侧授权目录内返回的文件内容。"""

    content: str
    truncated: bool = False


#: 授权执行器签名。实现方必须自行完成授权归属校验（企业/用户/状态/范围）。
GrantExecutor = Callable[..., Awaitable[Any]]


class _ExecutorRegistry:
    """进程内授权执行器注册表。

    由应用启动时注册（见 `app.core.lifespan`），未注册时所有端侧文件工具一律返回
    `not configured`，而不是退化成后端本地操作。
    """

    def __init__(self) -> None:
        self._executor: Optional[GrantExecutor] = None

    def register(self, executor: Optional[GrantExecutor]) -> None:
        self._executor = executor

    def get(self) -> Optional[GrantExecutor]:
        return self._executor

    @property
    def configured(self) -> bool:
        return self._executor is not None


_REGISTRY = _ExecutorRegistry()


def register_executor(executor: Optional[GrantExecutor]) -> None:
    """注册/注销端侧授权执行器（应用启动/关闭时调用）。"""
    _REGISTRY.register(executor)


def executor_configured() -> bool:
    """当前部署是否具备真实端侧执行能力。"""
    return _REGISTRY.configured


def _public_error(exc: BaseException) -> str:
    """把执行器异常收敛成可安全展示的文案。

    公共错误（由本模块或授权执行器定义）原样透出；其它异常只进服务端日志，
    对外统一返回泛化文案——异常原文可能带数据库内容、远端响应或本地路径。
    """
    text = str(exc)
    if text in _PUBLIC_ERRORS:
        return text
    return ERR_UNEXPECTED_FAILURE


def normalize_device_relative_path(raw_path: Any) -> Optional[str]:
    """把调用方给出的路径校验并归一化为授权目录内的相对路径。

    返回 ``None`` 表示路径不合法。**这里只做字符串层面的校验与规范化，不访问
    后端文件系统**：真实路径校验必须由端侧在其授权目录内完成。

    与工具说明保持一致：**任何** ``..`` 段都直接拒绝，而不是先 ``normpath`` 再看
    结果。否则 ``a/../b`` 会被静默改写成 ``b``，既与"不接受 .."的契约矛盾，也让
    "调用方写了什么"与"端侧实际读了什么"对不上。
    """
    if not isinstance(raw_path, str):
        return None
    candidate = raw_path.strip().replace("\\", "/")
    if not candidate or candidate in {".", "/"}:
        return None
    # 绝对路径、盘符、UNC、协议前缀一律拒绝。
    if candidate.startswith("/") or candidate.startswith("~"):
        return None
    if len(candidate) > 1 and candidate[1] == ":":
        return None
    if "://" in candidate:
        return None
    if "\x00" in candidate:
        return None
    # 任何向上跳转段一律拒绝（不做"先归一化再看是否越界"）。
    if any(segment == ".." for segment in candidate.split("/")):
        return None
    normalized = posixpath.normpath(candidate)
    if normalized.startswith("..") or normalized == "." or posixpath.isabs(normalized):
        return None
    return normalized


class LocalRunnerBridge:
    """本地 Runner 工具桥接器。"""

    _AVAILABLE_RUNNER_TOOLS = [
        MCPToolDefinition(
            name="runner_read_file",
            description=(
                "读取**用户本地授权目录**内的文件内容。"
                "必须给出该目录的 grant_id（本地工具桥接里登记的授权记录）与"
                "relative_path（不含 .. 的相对路径）；后端不会读取自身文件系统，"
                "真实读取发生在用户本机的守护进程里。"
            ),
            inputSchema=MCPToolParameterSchema(
                type="object",
                properties={
                    "grant_id": {
                        "type": "string",
                        "description": "当前用户自己的本地路径授权 ID（已连接状态）",
                    },
                    "relative_path": {
                        "type": "string",
                        "description": "授权目录内的相对路径（不接受绝对路径或 ..）",
                    },
                },
                required=["grant_id", "relative_path"],
            ),
        ),
        MCPToolDefinition(
            name="runner_execute_script",
            description="在端侧执行受控脚本（**当前未实现**，任何调用都会失败）",
            inputSchema=MCPToolParameterSchema(
                type="object",
                properties={
                    "command": {"type": "string", "description": "待执行指令"},
                    "timeout_seconds": {"type": "integer", "description": "超时时长（秒）"},
                },
                required=["command"],
            ),
        ),
        MCPToolDefinition(
            name="runner_system_probe",
            description="获取端侧守护进程的健康状况（**当前未实现**，不会用后端档案冒充）",
            inputSchema=MCPToolParameterSchema(
                type="object",
                properties={},
                required=[],
            ),
        ),
    ]

    @classmethod
    def list_tools(cls) -> List[MCPToolDefinition]:
        """返回已桥接的端侧 Local Runner 工具列表。"""
        return list(cls._AVAILABLE_RUNNER_TOOLS)

    @classmethod
    async def call_tool(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        context: Optional[MCPToolCallContext] = None,
    ) -> MCPToolResult:
        """调用端侧 Local Runner 工具。

        没有身份上下文、没有已注册执行器或授权校验失败时，一律返回
        ``success=False``；不存在任何后端本地文件读取或命令执行兜底。
        """
        arguments = arguments or {}

        if tool_name not in {t.name for t in cls._AVAILABLE_RUNNER_TOOLS}:
            return MCPToolResult(success=False, error=f"未知端侧工具: {tool_name}")

        if context is None or not context.enterprise_id or not context.user_id:
            return MCPToolResult(success=False, error=ERR_NO_CONTEXT)

        # 探针与脚本执行都没有真实端侧通道：明确拒绝，不用后端档案或模拟结果冒充。
        if tool_name == "runner_system_probe":
            return MCPToolResult(success=False, error=ERR_PROBE_UNAVAILABLE)
        if tool_name == "runner_execute_script":
            return MCPToolResult(success=False, error=ERR_SCRIPT_UNAVAILABLE)

        # 旧参数明确拒绝，不做静默兼容：file_path 正是历史后端任意读取的入口。
        if "file_path" in arguments and "relative_path" not in arguments:
            return MCPToolResult(success=False, error=ERR_FILE_PATH_RETIRED)

        # 旧契约：只给 device_id 不再被"就近映射"到某个授权，必须显式失败。
        if arguments.get("device_id") and not arguments.get("grant_id"):
            return MCPToolResult(success=False, error=ERR_DEVICE_ID_RETIRED)

        grant_id = arguments.get("grant_id")
        if not isinstance(grant_id, str) or not grant_id.strip():
            return MCPToolResult(
                success=False, error="缺少 grant_id：端侧文件读取必须绑定具体授权"
            )

        executor = _REGISTRY.get()
        if executor is None:
            return MCPToolResult(success=False, error=ERR_NO_EXECUTOR)

        return await cls._read_file(executor, arguments, context, grant_id.strip())

    @classmethod
    async def _read_file(
        cls,
        executor: GrantExecutor,
        arguments: Dict[str, Any],
        context: MCPToolCallContext,
        grant_id: str,
    ) -> MCPToolResult:
        relative_path = normalize_device_relative_path(arguments.get("relative_path"))
        if relative_path is None:
            return MCPToolResult(success=False, error=ERR_PATH_NOT_ALLOWED)

        try:
            result = await executor(
                operation="read_file",
                grant_id=grant_id,
                enterprise_id=context.enterprise_id,
                user_id=context.user_id,
                relative_path=relative_path,
                max_bytes=MAX_READ_BYTES,
            )
        except Exception as exc:  # noqa: BLE001 - 端侧失败必须转成结构化错误
            # 完整堆栈只进服务端日志；对外只给公共错误或泛化文案。
            logger.warning("端侧文件读取失败: %s", exc, exc_info=True)
            return MCPToolResult(success=False, error=_public_error(exc))

        if not isinstance(result, GrantFileRead):
            return MCPToolResult(success=False, error=ERR_BAD_RESULT_TYPE)

        return MCPToolResult(
            success=True,
            data={"content": result.content, "truncated": result.truncated},
        )
