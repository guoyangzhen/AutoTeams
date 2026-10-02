"""可观测性初始化：结构化日志 + Sentry 错误上报。

从 main.py 提取（原 main.py:94-136），由 :func:`init_observability` 在 app 创建前调用。

- P1-06: 结构化日志 + 文件轮转（替代原 basicConfig）
- P3-2: Sentry 仅在配置 DSN 时启用；``before_send`` 过滤 PII 敏感字段
"""
import logging

from app.config import settings
from app.utils.logging_config import setup_logging
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_HINTS = (
    "password",
    "token",
    "secret",
    "key",
    "cookie",
    "authorization",
    "api_key",
)

# 请求头按子串匹配（与拆分前一致）
_SENSITIVE_HEADERS = ("authorization", "cookie", "x-csrf-token", "x-api-key")


def _before_send(event, hint):
    """过滤 Sentry 事件中的 PII 敏感字段。"""
    if event is None:
        return event
    # 移除可能包含敏感信息的上下文
    contexts = event.get("contexts") or {}
    for ctx_name in ("user", "extra"):
        ctx = contexts.get(ctx_name) or {}
        for key in list(ctx.keys()):
            if any(s in key.lower() for s in _SENSITIVE_KEY_HINTS):
                ctx[key] = "[filtered]"
    # 移除请求头中的敏感字段
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    for key in list(headers.keys()):
        if any(s in key.lower() for s in _SENSITIVE_HEADERS):
            headers[key] = "[filtered]"
    return event


def _init_sentry() -> None:
    """P3-2: 初始化 Sentry（仅在配置 DSN 时启用，失败不阻断启动）。"""
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=[
                StarletteIntegration(),
                FastApiIntegration(),
            ],
            before_send=_before_send,
        )
        logger.info("Sentry 错误上报已启用", extra={"environment": settings.SENTRY_ENVIRONMENT})
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(f"Sentry 初始化失败（非致命）: {e}", exc_info=True)


def init_observability() -> None:
    """初始化日志与错误上报，进程内仅需调用一次。"""
    # P1-06: 替换原 basicConfig 为结构化日志 + 文件轮转
    setup_logging(debug=settings.DEBUG, log_dir=settings.LOG_DIR)
    if settings.SENTRY_DSN:
        _init_sentry()
