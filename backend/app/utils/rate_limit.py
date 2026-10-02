"""P0-11: 速率限制工具。

独立模块，避免 main.py ↔ api/* 之间的循环导入。
main.py 通过 setup_limiter(app) 注册中间件与异常处理；
api/* 通过 limiter.limit(...) 装饰端点。

P1/P2-INFRA: 当 REDIS_URL 配置且 Redis 可达时，自动切换到 Redis 后端存储，
支持多实例共享限流计数；否则回退到内存存储（单实例）。
"""
import logging
import os
from typing import Callable

from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.utils.redis_constants import REDIS_SOCKET_CONNECT_TIMEOUT, REDIS_SOCKET_TIMEOUT

logger = logging.getLogger(__name__)


_TRUSTED_PROXIES = {
    "127.0.0.1",
    "::1",
    "localhost",
}
_trusted_env = os.getenv("TRUSTED_PROXIES", "")
if _trusted_env:
    for proxy in _trusted_env.split(","):
        proxy = proxy.strip()
        if proxy:
            _TRUSTED_PROXIES.add(proxy)


def _get_client_ip(request: Request) -> str:
    """P1-04: 安全获取客户端真实 IP，防止 X-Forwarded-For 欺骗。

    策略：
    1. 如果直接连接 IP (request.client.host) 在可信代理列表中，
       则从 X-Forwarded-For 头从最右侧（最接近服务端）向左侧遍历，
       找到第一个不在可信代理列表中的 IP 作为客户端 IP；
    2. 否则直接使用 request.client.host，不信任任何代理头；
    3. 始终验证 IP 格式，防止注入攻击。
    """
    client_host = request.client.host if request.client else "127.0.0.1"

    if client_host not in _TRUSTED_PROXIES:
        return client_host

    # 优先使用 X-Real-IP（由唯一入口 Nginx 设置）
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip and _is_valid_ip(real_ip) and real_ip not in _TRUSTED_PROXIES:
        return real_ip

    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        # 从右侧（最接近服务端）向左侧遍历，找到第一个非可信代理 IP
        for ip in reversed(forwarded_for.split(",")):
            ip = ip.strip()
            if ip and _is_valid_ip(ip) and ip not in _TRUSTED_PROXIES:
                return ip

    return client_host


def _is_valid_ip(ip: str) -> bool:
    """简单验证 IP 地址格式（IPv4/IPv6）。"""
    import ipaddress
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _get_key(request: Request) -> str:
    """限流 key：优先使用认证后的 user_id（更精准），回退到客户端 IP。

    通过 request.state.user_id 获取（如果上游中间件已设置），
    否则用 IP（登录/注册等未认证端点也用 IP）。
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    return f"ip:{_get_client_ip(request)}"


# 全局 limiter 实例（默认内存存储；setup_limiter 时若 Redis 可用则切换）
limiter = Limiter(key_func=_get_key)


def _redis_available() -> bool:
    """探测 Redis 是否可用于限流存储。"""
    if not settings.REDIS_URL:
        return False
    try:
        import redis
        client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
        )
        client.ping()
        return True
    except Exception as e:
        logger.warning(f"Redis 限流存储不可用，回退到内存存储: {e}")
        return False


def setup_limiter(app) -> None:
    """注册 limiter 到 FastAPI 应用。

    必须在 app.include_router 之前调用，否则装饰器不会生效。
    同时注册 SlowAPIMiddleware（处理限流计数）与 RateLimitExceeded 异常处理器。

    5.3.1: 限流存储后端策略——
    - 生产环境（DEBUG=false）且 RATE_LIMIT_REQUIRE_REDIS=true 时，
      Redis 不可用直接拒绝启动，避免多实例各自内存计数被绕过。
    - 开发/测试环境或未强制要求时，Redis 可用则切换、不可用则回退内存存储。
    """
    from slowapi.middleware import SlowAPIMiddleware
    from limits.storage import storage_from_string

    redis_ok = _redis_available()

    if redis_ok:
        try:
            limiter._storage = storage_from_string(settings.REDIS_URL)
            logger.info("速率限制已切换到 Redis 后端存储")
        except Exception as e:
            logger.warning(f"切换 Redis 限流存储失败，继续使用内存存储: {e}")
            redis_ok = False

    # 5.3.1: 生产环境强制 Redis 校验
    if not settings.DEBUG and settings.RATE_LIMIT_REQUIRE_REDIS and not redis_ok:
        raise RuntimeError(
            "拒绝启动：生产环境 RATE_LIMIT_REQUIRE_REDIS=true 但 Redis 不可用。"
            "多实例部署下内存限流可被绕过，请配置可用的 REDIS_URL 或设 "
            "RATE_LIMIT_REQUIRE_REDIS=false（不推荐）。"
        )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)


def rate_limit_auth() -> Callable:
    """认证端点限流装饰器（login/register/refresh）。"""
    return limiter.limit(f"{settings.RATE_LIMIT_AUTH_PER_MINUTE}/minute")


def rate_limit_chat() -> Callable:
    """聊天端点限流装饰器（chat/stream）。"""
    return limiter.limit(f"{settings.RATE_LIMIT_CHAT_PER_MINUTE}/minute")


def rate_limit_agent_api() -> Callable:
    """外部 Agent REST API 限流，独立于浏览器会话和 SSE 配额。"""
    return limiter.limit(f"{settings.AGENT_API_RATE_LIMIT_PER_MINUTE}/minute")


def rate_limit_sse() -> Callable:

    """P1-02-D: SSE 流式聊天限流装饰器（比普通 chat 更严格）。

    SSE 连接占用时间长，每个连接会持有信号量直到结束，
    因此限制每用户/每 IP 的并发连接频率，防止单用户耗尽 SSE 信号量池。
    """
    return limiter.limit(f"{settings.RATE_LIMIT_SSE_PER_MINUTE}/minute")


def rate_limit_upload() -> Callable:
    """上传端点限流装饰器。"""
    return limiter.limit(f"{settings.RATE_LIMIT_UPLOAD_PER_MINUTE}/minute")


def rate_limit_scan() -> Callable:
    """扫描端点限流装饰器。"""
    return limiter.limit(f"{settings.RATE_LIMIT_SCAN_PER_MINUTE}/minute")


def rate_limit_api() -> Callable:
    """P1-01: 通用业务端点限流装饰器（setup/process/loop/planner/conversations/files）。"""
    return limiter.limit(f"{settings.RATE_LIMIT_API_PER_MINUTE}/minute")


def rate_limit_admin() -> Callable:
    """P1-01: 管理操作限流装饰器（enterprise invite/members/role 等 admin 端点）。"""
    return limiter.limit(f"{settings.RATE_LIMIT_ADMIN_PER_MINUTE}/minute")


def rate_limit_health() -> Callable:
    """BE-SEC-05: /health 端点限流装饰器（低频探测防护）。"""
    return limiter.limit(f"{settings.RATE_LIMIT_HEALTH_PER_MINUTE}/minute")


def rate_limit_metrics() -> Callable:
    """BE-SEC-05: /metrics 端点限流装饰器（不影响 Prometheus 正常抓取）。"""
    return limiter.limit(f"{settings.RATE_LIMIT_METRICS_PER_MINUTE}/minute")
