"""P3-1: request_id 上下文管理。

提供线程/协程安全的 request_id 透传机制：
- FastAPI 中间件在每个请求开始时生成或继承客户端传入的 request_id
- 业务代码通过 get_request_id() 获取当前请求的 request_id
- 日志通过 extra={"request_id": ...} 自动输出到 JSON 结构化日志
- 异常响应通过 HTTP 头 X-Request-Id 返回给前端，便于问题定位
"""
import uuid
from contextvars import ContextVar
from typing import Optional

_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


REQUEST_ID_HEADER = "X-Request-Id"


def get_request_id() -> str:
    """获取当前上下文的 request_id。

    若尚未设置（例如在后台任务或 CLI 脚本中），自动生成一个新的 UUID4，
    保证日志和追踪始终有值可用。
    """
    value = _request_id_ctx.get()
    if value is None:
        value = _generate_request_id()
        _request_id_ctx.set(value)
    return value


def set_request_id(request_id: str) -> None:
    """显式设置当前上下文的 request_id。"""
    _request_id_ctx.set(request_id)


def _generate_request_id() -> str:
    """生成标准 request_id：小写 UUID4。"""
    return uuid.uuid4().hex
