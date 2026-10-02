"""全局异常处理器。

从 main.py 提取（原 main.py:505-569），统一对外错误响应格式::

    {"success": false, "message": <可展示文案>, "data": null, "errors": [...]}

所有响应均带 ``X-Request-Id`` 响应头，便于前后端/网关链路串联排障。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.utils.metrics import errors_total
from app.utils.request_context import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger(__name__)


def _json_response_with_request_id(status_code: int, content: dict) -> JSONResponse:
    """P3-1: 构造带 X-Request-Id 响应头的 JSONResponse。"""
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={REQUEST_ID_HEADER: get_request_id()},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return _json_response_with_request_id(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": str(exc.detail),
            "data": None,
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    error_messages = []
    for error in errors:
        loc = " -> ".join(str(part) for part in error["loc"])
        error_messages.append(f"{loc}: {error['msg']}")

    return _json_response_with_request_id(
        status_code=422,
        content={
            "success": False,
            "message": "请求参数验证失败",
            "data": None,
            "errors": error_messages,
        },
    )


async def global_exception_handler(request: Request, exc: Exception):
    request_id = get_request_id()
    errors_total.labels(module=__name__, exception_type=type(exc).__name__).inc()
    logger.error(f"未处理的异常: {exc}", extra={"request_id": request_id}, exc_info=True)
    # P3-2: 将未处理异常上报 Sentry，并附加 request_id 便于链路追踪
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk

            with sentry_sdk.isolation_scope() as scope:
                scope.set_tag("request_id", request_id)
                scope.set_extra("path", request.url.path)
                scope.set_extra("method", request.method)
                sentry_sdk.capture_exception(exc)
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning("Sentry 上报失败", exc_info=True)
    return _json_response_with_request_id(
        status_code=500,
        content={
            "success": False,
            "message": "服务器内部错误，请稍后重试",
            "data": None,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
