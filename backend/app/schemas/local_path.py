"""本地路径授权的请求/响应 Schema。

安全约定：
- 响应永不返回 setup_token 明文；token 仅在「注册成功」一次性返回，并内嵌于 setup 命令。
- setup_command 由后端生成，用户复制到本机终端即可拉起本地守护进程并完成桥接。
- 本地桥接仅支持目录内文件操作；不接受任何通用命令或 Agentic CLI 执行请求。
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ScopeLiteral = Literal["read", "read_write"]


class RegisterLocalPathRequest(BaseModel):
    """用户提交的本地路径授权请求。"""

    local_path: str = Field(..., min_length=1, max_length=2000)
    label: Optional[str] = Field(default=None, max_length=128)
    scope: ScopeLiteral = "read"


class LocalPathGrantView(BaseModel):
    """返回给前端的授权记录视图（不含任何 token）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    enterprise_id: str
    user_id: str
    label: Optional[str] = None
    local_path: str
    scope: str
    status: str
    claimed: bool = False
    runner_id: Optional[str] = None
    tool_manifest: Optional[dict] = None
    resolved_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RegisterLocalPathResponse(BaseModel):
    """注册成功响应：返回授权记录 + 一次性 setup token + 可直接粘贴的命令。"""

    grant: LocalPathGrantView
    setup_token: str
    setup_command: str
    setup_token_expires_at: datetime


class ClaimGrantRequest(BaseModel):
    """本地守护进程（经 collaboration-service 转发）认领授权的请求体。

    认证不依赖 Cookie（Runner 无浏览器会话），而是携带注册时下发的一次性
    setup_token 进行校验。
    """

    setup_token: str = Field(..., min_length=1)


class RunnerStatusUpdate(BaseModel):
    """Runner 本地校验通过后上报的连接状态与工具清单。"""

    runner_id: str = Field(..., min_length=1, max_length=200)
    resolved_path: Optional[str] = None
    tool_manifest: Optional[dict] = None


class RunLocalTaskRequest(BaseModel):
    """驱动本地守护进程执行受限文件操作。

    支持 list/read/write/delete。写入和删除须具有 read_write 授权。通用命令、
    包管理器、解释器及 Agentic CLI 能突破工作目录限制，因此一律不是受支持工具。
    """

    tool: Literal["list", "read", "write", "delete"] = "list"
    # 相对授权目录的路径（list 可为目录；read/write/delete 为文件路径）
    path: str = Field("", max_length=2000)
    # write 的内容（tool=write 时必填；Runner 会进一步限制最大字节数）
    content: Optional[str] = None
