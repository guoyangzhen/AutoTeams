"""AutoTeams 后端服务入口。

本文件只负责装配，不再承载任何实现细节：

- 生命周期（依赖自检、后台调度器、文件监听恢复）→ :mod:`app.core.lifespan`
- 中间件（CORS / 限流 / CSRF / request_id / 缓存头 / HTTP 指标）→ :mod:`app.core.middleware`
- 全局异常处理（统一错误响应格式）→ :mod:`app.core.exceptions`
- 日志与 Sentry 初始化 → :mod:`app.core.observability`
- 业务路由（按 auth / workspace / workforce / knowledge / system 分域）→ :mod:`app.api`

新增业务端点请在对应业务域的子路由模块中实现，不要回到本文件堆叠 include_router。
"""
from fastapi import FastAPI

from app.api import api_router, public_router
from app.config import settings
from app.core.exceptions import (
    global_exception_handler,
    http_exception_handler,
    register_exception_handlers,
    validation_exception_handler,
)
from app.core.lifespan import lifespan
from app.core.middleware import register_middleware
from app.core.observability import init_observability

# 日志需在 app 创建前初始化，否则启动阶段日志走默认 handler
init_observability()

app = FastAPI(
    title=settings.PROJECT_NAME + " API",
    description="AutoTeams — 企业级 AI 数字员工平台 / Enterprise Digital Employee Operating System",
    version=settings.PROJECT_VERSION,
    lifespan=lifespan,
)

# 中间件必须先于路由注册
register_middleware(app)
register_exception_handlers(app)

# 业务端点统一挂在 /api/v1；运维探活端点（/health、/metrics）保持在根路径
app.include_router(api_router, prefix="/api/v1")
app.include_router(public_router)

# 向后兼容：异常处理器与 lifespan 仍可从 app.main 直接导入（测试与运维脚本依赖）
__all__ = [
    "app",
    "lifespan",
    "http_exception_handler",
    "validation_exception_handler",
    "global_exception_handler",
]
