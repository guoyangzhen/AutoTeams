"""系统域路由（system domain）。

聚合「系统治理 / 基础设施 / 运维探活」类端点：

- ``router``        挂载于 ``/api/v1``：审计日志、业务指标、模型 API 配置、
                    影子评估、反事实影子评估、Local Runner 2.0
- ``public_router`` 挂载于根路径：``/health``、``/metrics``
                    （供负载均衡 / Prometheus 探活，不带 ``/api/v1`` 前缀以保持既有路径兼容）

.. warning::
   ``shadow`` 必须先于 ``counterfactual_shadow`` 注册：``/shadow`` 下存在动态段路由，
   顺序颠倒会让反事实影子端点被吞掉。
"""
import hmac
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import settings
from app.utils.health import (
    liveness_payload,
    required_dependencies,
    run_health_checks,
    serialize_statuses,
)
from app.utils.rate_limit import rate_limit_health, rate_limit_metrics
from app.utils.request_context import REQUEST_ID_HEADER, get_request_id

from app.api.audit_logs import router as audit_logs_router
from app.api.counterfactual_shadow import router as counterfactual_shadow_router
from app.api.llm_config import router as llm_config_router
from app.api.metrics import router as business_metrics_router
from app.api.runner_v2 import router as runner_v2_router
from app.api.shadow import router as shadow_router

logger = logging.getLogger(__name__)

router = APIRouter()

# 顺序敏感：shadow 需先于 counterfactual_shadow 注册
router.include_router(audit_logs_router)
router.include_router(business_metrics_router)
router.include_router(llm_config_router)
router.include_router(shadow_router)
router.include_router(counterfactual_shadow_router)
router.include_router(runner_v2_router)


# ============================================================
# 公开端点（根路径，不带 /api/v1 前缀）
# ============================================================
public_router = APIRouter()


@public_router.get("/health")
@rate_limit_health()
async def health_check(request: Request):
    """就绪性检查（`/health/ready` 的向后兼容别名，容器 HEALTHCHECK 使用）。

    AUD-29 修正：迁移遗漏、队列无消费者、认证 Redis 故障都会返回 503。
    旧实现对 database 以外的一切降级都返回 200，发布检查看不见这些问题。
    """
    overall, dependencies = await run_health_checks()
    degraded_is_fatal = _degraded_is_fatal(dependencies)
    status_code = (
        200 if (overall == "ready" or (overall == "degraded" and not degraded_is_fatal)) else 503
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "service": "AutoTeams Backend",
            "version": "2.0.0",
            "features": [
                "loop_engineering",
                "agent_management",
                "file_management",
                "task_planning",
                "persistent_state",
            ],
            "dependencies": serialize_statuses(dependencies),
        },
        headers={REQUEST_ID_HEADER: get_request_id()},
    )


@public_router.get("/health/live")
@rate_limit_health()
async def liveness(request: Request):
    """存活探针：不触碰任何依赖。

    依赖（Redis / Chroma / DB）故障时**不**应重启容器 —— 那会把一次依赖抖动
    放大成整个集群重启。是否接流量由 readiness 决定。
    """
    return JSONResponse(
        status_code=200,
        content={**liveness_payload(), "version": "2.0.0"},
        headers={REQUEST_ID_HEADER: get_request_id()},
    )


@public_router.get("/health/ready")
@rate_limit_health()
async def readiness(request: Request):
    """就绪探针：验证业务确实可服务（迁移完整 + 必需依赖 + worker 消费者）。

    与历史 `/health` 的区别：迁移遗漏、队列没有消费者、认证 Redis 故障
    都会让这里返回 503，发布检查与告警能立刻发现，而不是"healthy 但业务 500"。
    """
    overall, dependencies = await run_health_checks()
    # degraded 取决于部署：chromadb 是必需依赖时不能带病接流量。
    degraded_is_fatal = _degraded_is_fatal(dependencies)
    status_code = 200 if (overall == "ready" or (overall == "degraded" and not degraded_is_fatal)) else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "service": "AutoTeams Backend",
            "version": "2.0.0",
            "dependencies": serialize_statuses(dependencies),
        },
        headers={REQUEST_ID_HEADER: get_request_id()},
    )


def _degraded_is_fatal(dependencies) -> bool:
    """degraded/not_ready 时是否必须摘流量：看异常的是不是"部署必需的依赖"。

    AUD-29 二次复核：旧实现把必需集合硬编码在这里且**漏掉 workers**，
    "已启用队列 worker 全部掉线"只会被判成 degraded → HTTP 200，发布检查看不见。
    判定逻辑收敛到 `health.required_dependencies()`，并把 `unknown`（探测崩溃或
    无法判定）同样视为致命 —— 判不出来就不能宣称健康。
    """
    required = required_dependencies()
    return any(
        d.status in ("down", "unknown") and d.name in required for d in dependencies
    )


# P1-06: Prometheus 指标端点
# P1-06-E: 访问控制 —— 通过 METRICS_AUTH_TOKEN 环境变量保护
# 生产环境设置 METRICS_AUTH_TOKEN 后，Prometheus 抓取需带 ?token=xxx 参数
# 未设置 token 时（开发环境）允许直接访问
@public_router.get("/metrics")
@rate_limit_metrics()
async def metrics(request: Request):
    expected_token = settings.METRICS_AUTH_TOKEN
    if expected_token:
        # token 通过 query 参数或 Authorization header 传递
        provided_token = request.query_params.get("token") or ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            provided_token = provided_token or auth_header[7:]
        # BE-SEC-06: 使用恒定时间比较，防止时序攻击
        if not hmac.compare_digest(provided_token, expected_token):
            return JSONResponse(
                status_code=403,
                content={"success": False, "message": "Forbidden", "data": None},
            )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
