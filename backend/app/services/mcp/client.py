"""AutoTeams 4.0 Model Context Protocol (MCP) 客户端引擎。

实现符合 Anthropic MCP 标准的客户端协议栈：
1. stdio 传输：子进程 IPC、标准输入输出流、崩溃隔离与超时防护
2. SSE / HTTP 传输：长连接 Server-Sent Events 与 REST 端点
3. SafeHarness 沙箱与超时拦截结合
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
import httpx

from app.services.mcp.runner_bridge import MCPToolCallContext
from app.services.mcp.schema import (
    MCPServerConfig,
    MCPToolDefinition,
    MCPToolResult,
)
logger = logging.getLogger(__name__)


class MCPClient:
    """标准 MCP 客户端。"""

    def __init__(self, config: MCPServerConfig):

        self.config = config
        self._request_counter = 0

    def _next_request_id(self) -> int:
        self._request_counter += 1
        return self._request_counter

    async def list_tools(self) -> List[MCPToolDefinition]:
        """向 MCP 服务器发现可用工具清单（tools/list）。"""
        if self.config.transport == "stdio":
            return await self._list_tools_stdio()
        elif self.config.transport == "sse":
            return await self._list_tools_sse()
        elif self.config.transport == "runner_bridge":
            from app.services.mcp.runner_bridge import LocalRunnerBridge
            return LocalRunnerBridge.list_tools()
        return []

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: Optional[MCPToolCallContext] = None,
    ) -> MCPToolResult:
        """调用 MCP 服务器的指定工具（tools/call）。

        `context` 是端侧工具的身份上下文（AUD-01）：`runner_bridge` 传输下的
        工具必须在其中携带企业/用户信息，后端据此拒绝无上下文的调用。
        """
        start = time.perf_counter()
        try:
            if self.config.transport == "stdio":
                result = await self._call_tool_stdio(tool_name, arguments)
            elif self.config.transport == "sse":
                result = await self._call_tool_sse(tool_name, arguments)
            elif self.config.transport == "runner_bridge":
                from app.services.mcp.runner_bridge import LocalRunnerBridge
                result = await LocalRunnerBridge.call_tool(tool_name, arguments, context)
            else:
                return MCPToolResult(
                    success=False,
                    error=f"不支持的传输协议: {self.config.transport}",
                    execution_time_ms=(time.perf_counter() - start) * 1000,
                )
            result.execution_time_ms = round((time.perf_counter() - start) * 1000, 2)
            return result
        except asyncio.TimeoutError:
            return MCPToolResult(
                success=False,
                error=f"MCP 工具执行超时（限时 {self.config.timeout_seconds}s）",
                execution_time_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        except Exception as e:
            logger.error(f"MCP 工具调用异常: {e}", exc_info=True)
            return MCPToolResult(
                success=False,
                error=str(e),
                execution_time_ms=round((time.perf_counter() - start) * 1000, 2),
            )

    async def _list_tools_stdio(self) -> List[MCPToolDefinition]:
        """通过 stdio 子进程协议请求 tools/list。"""
        if not self.config.command:
            return []

        req = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/list",
            "params": {},
        }
        cmd = [self.config.command] + self.config.args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**self.config.env},
        )
        try:
            input_bytes = (json.dumps(req) + "\n").encode("utf-8")
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(input_bytes),
                timeout=self.config.timeout_seconds,
            )
            response = json.loads(stdout_bytes.decode("utf-8"))
            tools_raw = response.get("result", {}).get("tools", [])
            return [MCPToolDefinition(**t) for t in tools_raw]
        except Exception as e:
            logger.warning(f"stdio tools/list 失败: {e}")
            return []
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    async def _call_tool_stdio(self, tool_name: str, arguments: Dict[str, Any]) -> MCPToolResult:
        """通过 stdio 子进程协议执行 tools/call。"""
        if not self.config.command:
            return MCPToolResult(success=False, error="未配置可执行命令")

        req = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        cmd = [self.config.command] + self.config.args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**self.config.env},
        )
        try:
            input_bytes = (json.dumps(req) + "\n").encode("utf-8")
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input_bytes),
                timeout=self.config.timeout_seconds,
            )
            raw_out = stdout_bytes.decode("utf-8").strip()
            if not raw_out:
                err_msg = stderr_bytes.decode("utf-8") if stderr_bytes else "无输出"
                return MCPToolResult(success=False, error=f"执行无输出: {err_msg}")

            response = json.loads(raw_out)
            if "error" in response:
                return MCPToolResult(success=False, error=str(response["error"]))

            result_data = response.get("result", {})
            return MCPToolResult(success=True, data=result_data)
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    async def _list_tools_sse(self) -> List[MCPToolDefinition]:
        """通过 HTTP / SSE 端点拉取 tools/list。"""
        if not self.config.url:
            return []
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            resp = await client.post(
                f"{self.config.url.rstrip('/')}/tools/list",
                json={"jsonrpc": "2.0", "id": self._next_request_id(), "method": "tools/list", "params": {}},
            )
            if resp.status_code == 200:
                data = resp.json()
                tools_raw = data.get("result", {}).get("tools", [])
                return [MCPToolDefinition(**t) for t in tools_raw]
            return []

    async def _call_tool_sse(self, tool_name: str, arguments: Dict[str, Any]) -> MCPToolResult:
        """通过 HTTP / SSE 端点调用 tools/call。"""
        if not self.config.url:
            return MCPToolResult(success=False, error="未配置服务器端点 URL")
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            resp = await client.post(
                f"{self.config.url.rstrip('/')}/tools/call",
                json={
                    "jsonrpc": "2.0",
                    "id": self._next_request_id(),
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    return MCPToolResult(success=False, error=str(data["error"]))
                return MCPToolResult(success=True, data=data.get("result"))
            return MCPToolResult(success=False, error=f"HTTP 响应异常状态: {resp.status_code}")
