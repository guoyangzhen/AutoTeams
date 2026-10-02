"""中间件注册。

从 main.py 提取（原 main.py:315-458），由 :func:`register_middleware` 统一装配。

**注册顺序即执行顺序，勿随意调整。** FastAPI/Starlette 的
``add_middleware`` 会把中间件插入到栈顶，因此最终的「由外到内」执行链为::

    metrics → remove_server_header → cache_control → request_id → csrf → rate_limit → CORS

- ``metrics``/``request_id``/``cache_control``/``remove_server_header`` 处于最外层，
  才能覆盖到内层短路返回的响应（例如 CSRF 403）。
- ``csrf`` 必须早于业务路由执行，因此位于 ``rate_limit``/``CORS`` 之前。
- ``CORS`` 最内层，保证预检（OPTIONS）不会被 CSRF 规则拦截。
"""
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.utils.agent_api_auth import AGENT_API_KEY_HEADER, extract_agent_api_key
from app.utils.metrics import (
    errors_total,
    http_request_duration_seconds,
    http_requests_total,
)
from app.utils.rate_limit import setup_limiter
from app.utils.request_context import REQUEST_ID_HEADER, get_request_id, set_request_id
from app.utils.security import validate_csrf_token

logger = logging.getLogger(__name__)

def _json_response_with_request_id(status_code: int, content: dict) -> JSONResponse:
    """构造带 X-Request-Id 响应头的 JSONResponse。"""
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
            "data": None
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _json_response_with_request_id(
        status_code=422,
        content={
            "success": False,
            "message": "请求参数校验失败",
            "data": exc.errors()
        },
    )


async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"未捕获异常: {exc}", exc_info=True)
    errors_total.labels(module="unhandled", exception_type=type(exc).__name__).inc()
    return _json_response_with_request_id(
        status_code=500,
        content={
            "success": False,
            "message": "服务器内部错误，请稍后重试",
            "data": None
        },
    )

# CORS 默认放行源：显式白名单，修复 allow_origins=["*"] + allow_credentials=True 的规范违规
_DEFAULT_ORIGINS = (
    "http://localhost:3000,http://localhost:5173,"
    "http://127.0.0.1:3000,http://127.0.0.1:5173"
)

# P0-S4: 仅对首次认证端点豁免 CSRF；/auth/refresh 必须携带 CSRF token。
_CSRF_EXEMPT_AUTH_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/register-with-invite",
}


def _allowed_origins() -> list[str]:
    """读取 CORS 白名单；生产环境为空时告警而非回退到通配符。"""
    origins = os.getenv("CORS_ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    parsed = [o.strip() for o in origins if o.strip()]
    if not parsed and not settings.DEBUG:
        logger.warning(
            "安全警告：生产环境 CORS_ALLOWED_ORIGINS 为空，"
            "这将导致跨域请求被拒绝。请在 .env 中配置允许的前端源。"
        )
    return parsed


# BE-SEC-01: CSRF 防护中间件（Double Submit Cookie）
# - 对状态变更方法（POST/PUT/PATCH/DELETE）且已登录的请求校验 X-CSRF-Token header
# - 与 cookie 中的 csrf_token 一致才放行
# - 未登录请求跳过（登录/注册本身无法预先获得 csrf cookie，依赖 SameSite=Lax 做基础防护）
async def csrf_middleware(request: Request, call_next):
    if request.url.path in _CSRF_EXEMPT_AUTH_PATHS:
        return await call_next(request)

    # 外部 Agent API 完全使用专用机器密钥而非浏览器 Cookie；即使调用环境意外
    # 携带了用户 Cookie，也不能让浏览器专用 CSRF 规则阻断该独立认证边界。
    # 仅放行受限的机器调用前缀，凭证、scope 与企业隔离仍由 endpoint 依赖校验。
    if (
        request.url.path.startswith("/api/v1/agent-api/v1/")
        and extract_agent_api_key(request, request.headers.get(AGENT_API_KEY_HEADER))
    ):
        return await call_next(request)

    if not validate_csrf_token(request):
        request_id = request.headers.get(REQUEST_ID_HEADER) or get_request_id()
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "CSRF token missing or invalid", "data": None},
            headers={REQUEST_ID_HEADER: request_id},
        )
    return await call_next(request)


# P3-1: request_id 中间件
# - 优先继承客户端传入的 X-Request-Id（便于前后端/网关链路串联）
# - 缺失时生成 UUID4，确保每条请求都有唯一追踪标识
# - 通过 contextvar 透传到业务代码、日志、LLM 调用链
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = incoming if incoming else get_request_id()
    set_request_id(request_id)

    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


# BE-SEC-06: 全局缓存控制头中间件
# 对敏感 API 响应禁用浏览器缓存，防止在共享设备上泄露数据
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    # 静态资源与公开健康检查可缓存；其余 API 默认禁用缓存
    if not path.startswith("/static/") and path != "/health":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
    return response


# L1: 去除 Server 响应头（备选方案，与 C2 的 --no-server-header 互补）
# uvicorn 默认返回 "Server: uvicorn" 头，暴露服务器实现，便于攻击者指纹识别。
# 此中间件在应用层兜底清除，无论 uvicorn 如何启动都生效。
async def remove_server_header_middleware(request: Request, call_next):
    response = await call_next(request)
    # Starlette MutableHeaders 不支持 pop，用 del 兜底
    if "server" in response.headers:
        del response.headers["server"]
    return response


# P1-06: HTTP 指标中间件（请求计数 + 延迟直方图）
# 使用路由模板路径作为 label，避免高基数（如 /agents/{agent_id} 而非 /agents/uuid）
# 3.2.8: 对未匹配路由（404）使用固定标签 "/unmatched"，避免原始 URL 产生标签基数爆炸
async def metrics_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start_time

    # 优先使用路由模板路径（低基数），降级为固定标签（避免原始 URL 高基数）
    route = request.scope.get("route")
    path_template = getattr(route, "path", None)
    if not path_template:
        # 未匹配到路由（404 或 OPTIONS 预检）：使用固定标签，避免原始 URL 产生高基数
        path_template = "/unmatched"
    method = request.method
    status = response.status_code

    try:
        http_requests_total.labels(method=method, path=path_template, status=status).inc()
        http_request_duration_seconds.labels(method=method, path=path_template).observe(duration)
    except Exception as e:
        # P1-4: 指标记录失败不应影响请求，但需记录以便排查
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("记录 HTTP 指标失败", exc_info=True)

    return response


def register_middleware(app: FastAPI) -> None:
    """按「由内到外」的注册顺序装配全部中间件。

    注意：注册顺序与执行顺序相反，详见模块 docstring。
    """
    app.add_middleware(
        CORSMiddleware,
        # P0 安全加固：空 origins 时不再回退到 ["*"]，避免 allow_credentials=true 与通配符同时使用
        allow_origins=_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-Request-Id",
            "X-AutoTeams-Agent-Key",
            # 品牌更名兼容期：旧名请求头继续放行，过渡期结束后移除
            "X-AutoFDE-Agent-Key",
            "Accept",
            "Origin",
            "User-Agent",
            "Cache-Control",
        ],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )

    # P0-11: 注册速率限制（必须在业务路由之前）
    setup_limiter(app)

    # 以下顺序与拆分前保持一致：csrf → request_id → cache_control → remove_server_header → metrics
    app.middleware("http")(csrf_middleware)
    app.middleware("http")(request_id_middleware)
    app.middleware("http")(cache_control_middleware)
    app.middleware("http")(remove_server_header_middleware)
    app.middleware("http")(metrics_middleware)

    # 异常处理器注册
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
