"""Access token 撤销存储（AUD-09）。

历史问题（审计复现）：

1. 本模块用 `jwt.decode(...)` 解码但**不传 audience/issuer**，而正常认证解码
   (`app/utils/auth/tokens.py:decode_token`) 会校验它们。结果：一旦配置
   `JWT_AUDIENCE`，带 aud 的 access token 在黑名单侧解码抛异常并被当作
   "未撤销"，登出无法让尚未过期的 token 失效。
2. Redis 客户端在**运行中**读写失败时静默回退到进程内字典，多实例之间撤销
   状态立刻不一致。
3. 撤销键与普通缓存共用一个 `allkeys-lru` 的 Redis，撤销键可能在 TTL 到期前
   被 LRU 驱逐，同样导致"登出无效"。

本模块的契约：

* 解码规则与认证路径完全一致（共用 `auth.tokens` 的严格解码）；
* 生产环境撤销存储不可用时**失败关闭**（拒绝授权），并记录 CRITICAL；
* 生产环境要求撤销存储是**独占的 `noeviction` 实例**：撤销键与渠道重放键都带
  TTL，而 `volatile-*` 家族对带 TTL 的键同样会在到期前淘汰，只有 `noeviction`
  能保证"键活到自己的 TTL 结束"。`CONFIG GET` 失败或返回空值一律**拒绝启动**，
  不再"告警放行"——无法证明安全状态不会被驱逐时，不得放行；
* 撤销键使用独立前缀，且可用 `TOKEN_BLACKLIST_REDIS_URL` 指向独立实例。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional


from app.config import settings
from app.utils.branding import LEGACY_STATE_NAMESPACE
from app.utils.auth.tokens import decode_token_payload
from app.utils.redis_constants import REDIS_SOCKET_CONNECT_TIMEOUT, REDIS_SOCKET_TIMEOUT

try:
    import redis
except ModuleNotFoundError:  # pragma: no cover - 生产镜像必装
    redis = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: 开发/测试回退：进程内撤销表（仅单进程有效，生产禁止使用）
_in_memory_blacklist: dict[str, float] = {}
_redis_client: Optional["redis.Redis"] = None

#: 唯一允许的 Redis 淘汰策略。
#:
#: 撤销键与重放键**都带 TTL**，而 `volatile-*` 家族只保证"不驱逐没有 TTL 的键"，
#: 对带 TTL 的安全键照样会在到期前按 LRU/LFU/TTL 随机淘汰。登出撤销因此必须
#: 独占 `noeviction` 实例：宁可写入失败（fail-closed）也不能悄悄丢撤销记录。
ALLOWED_EVICTION_POLICIES = frozenset({"noeviction"})

#: 检查过一次淘汰策略后不再重复 CONFIG GET（Redis 策略运行期不会变）。
_eviction_policy_checked = False


class TokenRevocationStoreUnavailable(RuntimeError):
    """生产环境撤销存储不可用。调用方必须拒绝授权，不得静默降级。"""


def _blacklist_redis_url() -> str:
    """撤销存储使用的 Redis 地址。

    允许通过 `TOKEN_BLACKLIST_REDIS_URL` 指向独立实例，使安全状态与可淘汰的
    缓存彻底分离（AUD-09 的"安全状态与可淘汰缓存分开"要求）。
    """
    return getattr(settings, "TOKEN_BLACKLIST_REDIS_URL", "") or settings.REDIS_URL


def _check_eviction_policy(client) -> None:
    """生产环境确认撤销存储不会驱逐安全键（严格只接受 noeviction）。"""
    global _eviction_policy_checked
    if _eviction_policy_checked or settings.DEBUG:
        return
    try:
        raw = client.config_get("maxmemory-policy")
    except Exception as exc:  # noqa: BLE001 - 托管 Redis 常禁用 CONFIG
        raise TokenRevocationStoreUnavailable(
            f"无法读取 Redis maxmemory-policy（{exc}），无法证明撤销键不会被淘汰。"
            "请为撤销存储授予 CONFIG 权限，或改用 noeviction 的独立实例。"
        ) from exc

    values = raw.values() if isinstance(raw, dict) else []
    policy = next(iter(values), "") if values else ""
    if policy not in ALLOWED_EVICTION_POLICIES:
        # 含"读不到策略"的空值：无法证明安全状态不被驱逐，必须失败关闭。
        raise TokenRevocationStoreUnavailable(
            f"Redis 淘汰策略为 {policy or '<读取为空>'}：只有 noeviction 能保证带 TTL 的"
            "撤销/重放键活到 TTL 结束（volatile-* 同样会淘汰带 TTL 的键）。"
            "请改用 noeviction 的独立实例。"
        )
    _eviction_policy_checked = True


def _get_redis() -> Optional["redis.Redis"]:
    """获取撤销存储连接。

    生产环境（`DEBUG=false`）下不可用时抛 `TokenRevocationStoreUnavailable`；
    开发环境回退到进程内字典并告警。
    """
    global _redis_client
    if redis is None:
        if not settings.DEBUG:
            raise TokenRevocationStoreUnavailable(
                "生产环境 token 撤销需要 redis 包，但未安装。请运行: pip install redis"
            )
        return None

    if _redis_client is not None:
        return _redis_client

    url = _blacklist_redis_url()
    if not url:
        if not settings.DEBUG:
            raise TokenRevocationStoreUnavailable(
                "生产环境 token 撤销需要 TOKEN_BLACKLIST_REDIS_URL 或 REDIS_URL，但都未配置。"
            )
        return None

    try:
        client = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            # 撤销检查在请求线程内同步执行：没有读超时会让 Redis 假死时
            # 整个请求无限挂起（fail-closed 也来不及生效）。
            socket_timeout=REDIS_SOCKET_TIMEOUT,
        )
        client.ping()
    except Exception as exc:  # noqa: BLE001
        if not settings.DEBUG:
            raise TokenRevocationStoreUnavailable(
                f"生产环境 token 撤销存储不可用，不允许降级为进程内状态: {exc}"
            ) from exc
        logger.warning("Redis 撤销存储连接失败，回退到内存黑名单: %s", exc)
        return None

    if not settings.DEBUG:
        _check_eviction_policy(client)
    _redis_client = client
    return client


def reset_redis_client() -> None:
    """清空缓存的连接与策略检查标记（仅供测试使用）。"""
    global _redis_client, _eviction_policy_checked
    _redis_client = None
    _eviction_policy_checked = False
    _in_memory_blacklist.clear()


def security_redis_client():
    """返回「安全状态 Redis」客户端（撤销存储），供其他安全用途复用。

    该实例已经过淘汰策略校验（不允许 `allkeys-*`），适合放置重放防护这类
    不能被 LRU 提前驱逐的安全键。生产环境不可用时抛
    `TokenRevocationStoreUnavailable`，调用方必须自行决定失败关闭策略。
    """
    return _get_redis()


def _key(jti: str) -> str:
    return f"autoteams:token_revoked:{jti}"


def _decode_for_revocation(token: str) -> Optional[dict]:
    """用与认证路径完全一致的规则解码。

    `decode_token_payload` 会校验 exp/iss/aud；解码失败返回 None（该 token
    不可能被本系统签发，无需撤销）。
    """
    try:
        return decode_token_payload(token)
    except Exception:  # noqa: BLE001 - 含 HTTPException
        return None


def revoke_access_token(token: str) -> None:
    """把 access token 加入撤销表，TTL 与 token 剩余有效期一致。"""
    if not token:
        return

    payload = _decode_for_revocation(token)
    if payload is None:
        logger.debug("吊销跳过：无法按认证规则解析该 token")
        return

    jti = payload.get("jti")
    exp = payload.get("exp")
    token_type = payload.get("type")
    if not jti or token_type != "access":  # noqa: S105
        # 只撤销 access token；refresh token 通过数据库 hash 清除
        return

    ttl_seconds = 0
    if exp:
        ttl_seconds = max(0, int(exp - datetime.now(timezone.utc).timestamp()))
    if ttl_seconds <= 0:
        return  # 已过期，无需记录

    client = _get_redis()
    if client:
        # Dual-write until old instances are drained; existing revocations keep working.
        # Legacy first: a later write failure still blocks both reader versions.
        client.setex(f"{LEGACY_STATE_NAMESPACE}:token_revoked:{jti}", ttl_seconds, "1")
        client.setex(_key(jti), ttl_seconds, "1")
        return

    _in_memory_blacklist[jti] = exp or 0
    logger.warning(
        "使用进程内 token 撤销表（仅开发环境）；生产多实例下撤销状态不会共享。"
    )


def is_token_revoked(token: str) -> bool:
    """检查 token 是否已被吊销。

    生产环境撤销存储不可用时**拒绝授权**（返回 True），而不是"看起来正常"
    地放行 —— 后者等于把登出撤销变成可绕过的检查。
    """
    if not token:
        return False

    payload = _decode_for_revocation(token)
    if payload is None:
        return False

    jti = payload.get("jti")
    if not jti:
        # 无 jti 的历史 token 无法撤销；生产不接受这种 token（签发方一定写 jti）。
        return False

    try:
        client = _get_redis()
    except TokenRevocationStoreUnavailable:
        if not settings.DEBUG:
            logger.critical(
                "token 撤销存储不可用，按拒绝授权处理（fail-closed）。"
                "请立即恢复 Redis 或配置 TOKEN_BLACKLIST_REDIS_URL。"
            )
            return True
        return False

    if client:
        try:
            return (client.exists(_key(jti)) > 0
                    or client.exists(f"{LEGACY_STATE_NAMESPACE}:token_revoked:{jti}") > 0)
        except Exception as exc:  # noqa: BLE001 - 运行期故障
            if not settings.DEBUG:
                logger.critical(
                    "读取 token 撤销状态失败，按拒绝授权处理（fail-closed）: %s", exc
                )
                return True
            logger.warning("Redis 读取撤销状态失败，回退内存: %s", exc)

    now = datetime.now(timezone.utc).timestamp()
    for j in [k for k, exp in _in_memory_blacklist.items() if 0 < exp < now]:
        _in_memory_blacklist.pop(j, None)
    return jti in _in_memory_blacklist


__all__ = [
    "ALLOWED_EVICTION_POLICIES",
    "TokenRevocationStoreUnavailable",
    "is_token_revoked",
    "reset_redis_client",
    "security_redis_client",
]
