"""健康检查：liveness / readiness / 依赖探测（AUD-29）。

审计结论：
- DB 探测只有 `SELECT 1`，证明不了 schema 与迁移是否完整 —— 11 张已接入业务
  的表没有建表迁移时 `/health` 依然 healthy；
- Redis / Chroma down 只标 `degraded`，而 `api/system.py` 仍然返回 HTTP 200；
- 两个 worker 容器继承 backend 镜像的 HTTP 8000 探针，而它们根本不监听。

本模块区分两种语义：

- **liveness**（进程是否还活着）：不触碰任何依赖，任何情况下都快速返回。
  依赖故障时**不**重启容器 —— 否则 Redis 抖动会把整个集群重启掉。
- **readiness**（是否可以接流量）：DB、迁移完整性、必需依赖、worker 消费者
  全部通过才返回 200；否则 503，让编排摘流量而不是"带病服务"。

依赖探测均带短超时，避免健康检查自身阻塞事件循环。
"""
import asyncio
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from app.database import engine
from app.utils.metrics import errors_total
from app.utils.redis_constants import REDIS_SOCKET_CONNECT_TIMEOUT, REDIS_SOCKET_TIMEOUT

logger = logging.getLogger(__name__)

# 单个依赖探测超时（秒）
_HEALTH_CHECK_TIMEOUT = 3.0

#: 就绪性必须覆盖的关键业务表。缺任意一张说明迁移没跑全，业务模块访问即报错。
_REQUIRED_TABLES = frozenset(
    {
        "agents",
        "enterprises",
        "users",
        "compilation_jobs",
        "flow_cards",
        "workgroup_teams",
        "matrix_tasks",
        "workforce_profiles",
        "channel_accounts",
        "runner_devices",
    }
)

#: 队列 worker 角色。与 scripts/worker_healthcheck.py 的白名单保持一致。
WORKER_ROLES = ("compilation", "agent_build", "processing")


@dataclass
class DependencyStatus:
    name: str
    status: str  # "up" / "down" / "unknown" / "skipped"
    latency_ms: Optional[float] = None
    message: Optional[str] = None


def _declared_worker_roles() -> list[str]:
    """本部署声明启用的 worker 角色（由 compose 通过环境变量给出）。"""
    if os.environ.get("WORKERS_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        return []
    declared = {
        item.strip() for item in os.environ.get("WORKER_ROLES", "").split(",") if item.strip()
    }
    return [role for role in WORKER_ROLES if role in declared]


async def _check_database() -> DependencyStatus:
    """探测数据库是否可连接。"""
    start = asyncio.get_event_loop().time()
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(
                conn.execute(text("SELECT 1")),
                timeout=_HEALTH_CHECK_TIMEOUT,
            )
        latency = (asyncio.get_event_loop().time() - start) * 1000
        return DependencyStatus(name="database", status="up", latency_ms=round(latency, 2))
    except Exception as e:  # noqa: BLE001
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("健康检查数据库探测失败: %s", e)
        return DependencyStatus(name="database", status="down", message="数据库连接失败")


async def _check_schema() -> DependencyStatus:
    """验证迁移版本与关键业务表确实存在。

    仅 `SELECT 1` 只能证明连接通；审计记录过 11 张已接入业务的表没有建表迁移，
    而 `/health` 仍返回 healthy，迁移遗漏在发布检查里完全不可见。

    AUD-29 二次复核：旧实现**先**读 ``alembic_version``，表还没列出来就已经失败，
    于是"由 ORM 元数据建表、没有迁移元数据"的 unknown 分支永远不可达；同时
    读到 version_num 也不与迁移 head 比对，落后或分叉的库照样判 up。
    """
    start = asyncio.get_event_loop().time()
    try:
        if engine.dialect.name == "sqlite":
            listing = text("SELECT name FROM sqlite_master WHERE type='table'")
        else:
            listing = text(
                "SELECT tablename AS name FROM pg_tables WHERE schemaname = current_schema()"
            )
        async with engine.connect() as conn:
            # 先列表：关键业务表缺失是硬故障，必须在任何"元数据缺失"解释之前报出来。
            table_rows = await asyncio.wait_for(
                conn.execute(listing), timeout=_HEALTH_CHECK_TIMEOUT
            )
            tables = {row[0] for row in table_rows}

            missing = _REQUIRED_TABLES - tables
            if missing:
                errors_total.labels(module=__name__, exception_type="SchemaIncomplete").inc()
                return DependencyStatus(
                    name="schema",
                    status="down",
                    message=f"缺少业务表（迁移未跑全）: {', '.join(sorted(missing))}",
                )

            if "alembic_version" not in tables:
                # 没有 alembic_version：数据库可能是由 ORM 元数据直接建表的（测试/临时环境）。
                # 关键业务表齐全只能说明"表在"，不能证明版本正确；状态如实记为 unknown，
                # 由 run_health_checks 按失败关闭处理（not_ready → 503）。
                return DependencyStatus(
                    name="schema",
                    status="unknown",
                    message="未找到 alembic_version（schema 可能由 ORM 元数据创建），无法确认迁移版本",
                )

            version_rows = await asyncio.wait_for(
                conn.execute(text("SELECT version_num FROM alembic_version")),
                timeout=_HEALTH_CHECK_TIMEOUT,
            )
            migrated = {row[0] for row in version_rows}
    except Exception as e:  # noqa: BLE001
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("健康检查 schema 探测失败: %s", e)
        return DependencyStatus(name="schema", status="down", message="迁移状态探测失败")

    try:
        heads = _alembic_heads()
    except Exception as e:  # noqa: BLE001
        errors_total.labels(module=__name__, exception_type="AlembicHeadsUnavailable").inc()
        logger.warning("健康检查无法解析迁移 head: %s", e)
        return DependencyStatus(name="schema", status="down", message="无法解析迁移 head")

    if not heads:
        return DependencyStatus(name="schema", status="down", message="迁移脚本目录没有 head")
    if migrated != heads:
        errors_total.labels(module=__name__, exception_type="SchemaRevisionMismatch").inc()
        return DependencyStatus(
            name="schema",
            status="down",
            message=(
                f"迁移版本与 head 不一致: 当前 {', '.join(sorted(migrated)) or '空'}"
                f" / head {', '.join(sorted(heads))}"
            ),
        )

    latency = (asyncio.get_event_loop().time() - start) * 1000
    return DependencyStatus(
        name="schema",
        status="up",
        latency_ms=round(latency, 2),
        message=f"revision {', '.join(sorted(migrated))}",
    )


#: 迁移 head 解析结果缓存（进程内不变：镜像里的迁移脚本不会热更新）。
_ALEMBIC_HEADS: Optional[frozenset] = None


def _alembic_heads() -> frozenset:
    """从随镜像分发的迁移脚本解析 head revision 集合。"""
    global _ALEMBIC_HEADS
    if _ALEMBIC_HEADS is None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        # app/utils/health.py -> backend/
        backend_root = pathlib.Path(__file__).resolve().parents[2]
        script_location = backend_root / "migrations"
        if not (script_location / "env.py").is_file():
            raise RuntimeError(f"迁移脚本目录缺失: {script_location}")
        cfg = Config()
        cfg.set_main_option("script_location", str(script_location))
        _ALEMBIC_HEADS = frozenset(ScriptDirectory.from_config(cfg).get_heads())
    return _ALEMBIC_HEADS



async def _check_redis() -> DependencyStatus:
    """探测缓存 Redis 是否可连接；未配置 REDIS_URL 时标记为 skipped。"""
    from app.config import settings

    if not settings.REDIS_URL:
        return DependencyStatus(name="redis", status="skipped", message="未配置 REDIS_URL")
    return await _ping_redis(settings.REDIS_URL, "redis", "缓存 Redis")


async def _ping_redis(url: str, name: str, label: str) -> DependencyStatus:
    start = asyncio.get_event_loop().time()
    try:
        import redis
    except ModuleNotFoundError:
        return DependencyStatus(name=name, status="down", message="redis 包未安装")
    try:
        client = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
        )
        await asyncio.wait_for(asyncio.to_thread(client.ping), timeout=_HEALTH_CHECK_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("健康检查 %s 探测失败: %s", label, e)
        return DependencyStatus(name=name, status="down", message=f"{label} 连接失败")
    latency = (asyncio.get_event_loop().time() - start) * 1000
    return DependencyStatus(name=name, status="up", latency_ms=round(latency, 2))


def _security_redis_url() -> str:
    """安全状态（令牌撤销）使用的 Redis 地址。

    与缓存 Redis 分开探测：`allkeys-lru` 的缓存实例故障只影响性能，但撤销库
    故障意味着登出/封禁失效 —— 只查 REDIS_URL 会让这种失守被报告成"健康"。
    """
    from app.utils.token_blacklist import _blacklist_redis_url

    return _blacklist_redis_url()


async def _check_security_redis() -> DependencyStatus:
    """探测安全状态（令牌撤销）存储；未配置时标记为 skipped。

    这里**走应用自己的撤销客户端**而不是单独 ping 一次：生产语义下该客户端会
    把连通性与淘汰策略一起校验（``allkeys-*`` 会提前驱逐撤销键，等于登出/封禁
    失效）。ping 通并不代表撤销存储真的可用。
    """
    from app.utils.token_blacklist import _blacklist_redis_url, _get_redis

    from app.config import settings

    if not _blacklist_redis_url():
        return DependencyStatus(
            name="security_redis",
            status="skipped" if settings.DEBUG else "down",
            message="未配置令牌撤销存储",
        )

    start = asyncio.get_event_loop().time()
    try:
        client = await asyncio.wait_for(
            asyncio.to_thread(_get_redis), timeout=_HEALTH_CHECK_TIMEOUT
        )
        if client is None:
            return DependencyStatus(
                name="security_redis", status="down", message="撤销存储不可用"
            )
        await asyncio.wait_for(
            asyncio.to_thread(client.ping), timeout=_HEALTH_CHECK_TIMEOUT
        )
    except Exception as e:  # noqa: BLE001
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("健康检查安全状态 Redis 探测失败: %s", e)
        return DependencyStatus(name="security_redis", status="down", message="安全状态 Redis 不可用")
    latency = (asyncio.get_event_loop().time() - start) * 1000
    return DependencyStatus(
        name="security_redis", status="up", latency_ms=round(latency, 2)
    )


async def _check_chromadb() -> DependencyStatus:
    """探测 ChromaDB 是否可连接；嵌入式模式通过 heartbeat 探测。"""
    from app.services.vector_store import ChromaDBConnectionError, get_chroma_client

    start = asyncio.get_event_loop().time()
    try:
        client = await asyncio.wait_for(get_chroma_client(), timeout=_HEALTH_CHECK_TIMEOUT)
        await asyncio.wait_for(
            asyncio.to_thread(client.heartbeat), timeout=_HEALTH_CHECK_TIMEOUT
        )
        latency = (asyncio.get_event_loop().time() - start) * 1000
        return DependencyStatus(name="chromadb", status="up", latency_ms=round(latency, 2))
    except ChromaDBConnectionError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("健康检查 ChromaDB 探测失败: %s", e)
        return DependencyStatus(name="chromadb", status="down", message=str(e))
    except Exception as e:  # noqa: BLE001
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning("健康检查 ChromaDB 探测失败: %s", e)
        return DependencyStatus(name="chromadb", status="down", message="ChromaDB 探测失败")


async def _check_workers() -> DependencyStatus:
    """验证启用的队列 worker 确实有活跃消费者。

    历史上队列有没有消费者在发布检查里完全不可见；这里读取 worker 落盘心跳，
    心跳过期即视为"没有消费者"并让就绪检查失败。
    """
    roles = _declared_worker_roles()
    if not roles:
        return DependencyStatus(name="workers", status="skipped", message="未启用队列 worker")

    from app.services.worker_heartbeat import DEFAULT_STALE_SECONDS, is_worker_alive

    alive: list[str] = []
    dead: list[str] = []
    for role in roles:
        ok, reason = is_worker_alive(role, stale_seconds=DEFAULT_STALE_SECONDS)
        (alive if ok else dead).append(role if ok else f"{role}: {reason}")

    if dead:
        errors_total.labels(module=__name__, exception_type="WorkerDown").inc()
        return DependencyStatus(name="workers", status="down", message="; ".join(dead))
    return DependencyStatus(name="workers", status="up", message=f"活跃: {', '.join(alive)}")


def required_dependencies() -> set:
    """本部署的**必需**依赖集合：这些 down/unknown 必须摘流量（503）。

    AUD-29 二次复核：旧实现把必需集合硬编码在 `api/system.py`，漏掉了 workers，
    于是"已启用队列 worker 全部掉线"只会降级成 degraded 并继续返回 200。依赖是否
    必需由**部署配置**决定（配置了 Redis 才要求 Redis，启用了 worker 才要求
    worker），因此判定逻辑收敛到这里，HTTP 端点只负责映射状态码。
    """
    from app.config import settings
    required = {"database", "schema"}
    if settings.REDIS_URL:
        required.add("redis")
    # 撤销库故障 = 登出/封禁失效，必须 503，即使缓存 Redis 正常。
    if not settings.DEBUG or _security_redis_url():
        required.add("security_redis")
    chroma_host = (settings.CHROMA_HOST or "").strip()
    if chroma_host and not chroma_host.startswith("~"):
        required.add("chromadb")
    if _declared_worker_roles():
        required.add("workers")
    return required


async def run_health_checks() -> tuple[str, list[DependencyStatus]]:
    """并行执行依赖探测，返回整体就绪状态与各依赖状态。

    状态语义（与历史实现不同的地方见模块 docstring）：

    - ``ready``：全部依赖 up/skipped；
    - ``degraded``：非必需依赖（chromadb）异常；**调用方应按部署决定是否摘流量**；
    - ``not_ready``：必需依赖 down **或 unknown** —— 业务不可用，必须 503。

    AUD-29 二次复核：旧实现只把 ``down`` 当致命，探测抛异常补上的 ``unknown``
    （以及"元数据缺失"这类无法判定的 schema 状态）会一路走到 ``ready``，
    探测自身崩溃反而被报告成"服务健康"。判定不了就摘流量，这是失败关闭。
    """
    checks = await asyncio.gather(
        _check_database(),
        _check_schema(),
        _check_redis(),
        _check_security_redis(),
        _check_chromadb(),
        _check_workers(),
        return_exceptions=True,
    )

    statuses: list[DependencyStatus] = []
    for result in checks:
        if isinstance(result, BaseException):
            errors_total.labels(module=__name__, exception_type=type(result).__name__).inc()
            logger.error("健康检查探测抛出未捕获异常: %s", result, exc_info=True)
            continue
        statuses.append(result)

    expected = {"database", "schema", "redis", "security_redis", "chromadb", "workers"}
    returned = {s.name for s in statuses}
    for missing in expected - returned:
        statuses.append(DependencyStatus(name=missing, status="unknown", message="探测异常"))
    # 必需依赖无法判定（down/unknown）一律 not_ready；其余依赖 down/unknown 才是 degraded。
    required = required_dependencies()
    if any(s.status in ("down", "unknown") for s in statuses if s.name in required):
        overall = "not_ready"
    elif any(s.status in ("down", "unknown") for s in statuses):
        overall = "degraded"
    else:
        overall = "ready"

    return overall, statuses


def serialize_statuses(statuses: list[DependencyStatus]) -> list[dict]:
    """将依赖状态序列化为可 JSON 化的字典列表。"""
    return [
        {
            "name": s.name,
            "status": s.status,
            "latency_ms": s.latency_ms,
            "message": s.message,
        }
        for s in statuses
    ]


def liveness_payload() -> dict:
    """liveness：不触碰依赖，只证明进程能响应。"""
    return {"status": "alive", "service": "AutoTeams Backend"}
