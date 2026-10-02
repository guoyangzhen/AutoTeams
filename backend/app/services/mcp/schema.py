"""AutoTeams 4.0 Model Context Protocol (MCP) 数据结构与契约。

支持标准 JSON-RPC 2.0 规范的 MCP 协议交互。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class MCPToolParameterSchema(BaseModel):
    type: str = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)


class MCPToolDefinition(BaseModel):
    name: str = Field(..., description="工具名称")
    description: Optional[str] = Field(None, description="工具功能与用法描述")
    inputSchema: MCPToolParameterSchema = Field(
        default_factory=MCPToolParameterSchema,
        description="参数 JSON Schema 定义",
    )


class MCPToolCall(BaseModel):
    server_id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class MCPToolResult(BaseModel):
    success: bool
    data: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0


class MCPServerConfig(BaseModel):
    server_id: str
    name: str
    transport: Literal["stdio", "sse", "runner_bridge"] = "stdio"
    command: Optional[str] = None          # stdio 启动命令，如 npx, python
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None              # sse 服务的端点 URL
    is_active: bool = True
    timeout_seconds: int = 30
