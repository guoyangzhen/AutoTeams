"""基于 Redis 的分布式信号量。

5.3.3: 替换进程内 asyncio.Semaphore，支持多实例共享并发限制。
用于 SSE 流并发控制等场景，防止单实例或集群整体过载。

设计要点：
- 基于 Redis INCR + EXPIRE + DECR 实现计数信号量
- Lua 脚本保证 acquire/release 的原子性
- Redis 不可用时回退到 asyncio.Semaphore（保持单实例行为）
- acquire 支持超时（与 asyncio.wait_for 兼容）
- 信号量键带 TTL 防止进程崩溃后计数泄漏

使用方式：
    semaphore = get_distributed_semaphore("sse", limit=50)
    await asyncio.wait_for(semaphore.acquire(), timeout=5.0)
    try:
        ...  # 临界区
    finally:
        semaphore.release()
"""
import asyncio
import logging
from typing import Optional

from app.config import settings
from app.utils.branding import LEGACY_STATE_NAMESPACE
from app.utils.redis_constants import REDIS_SOCKET_CONNECT_TIMEOUT, REDIS_SOCKET_TIMEOUT

logger = logging.getLogger(__name__)

# 复用 rate_limit 的 Redis 探测逻辑
try:
    import redis as redis_lib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    redis_lib = None  # type: ignore[assignment]


# Lua 脚本：原子性获取信号量
# KEYS[1] = 信号量键
# ARGV[1] = 最大并发数
# ARGV[2] = TTL（秒，用于防止泄漏）
# 返回 1 表示获取成功，0 表示已满
_ACQUIRE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current < tonumber(ARGV[1]) then
    local next = current + 1
    redis.call('SET', KEYS[1], next, 'EX', tonumber(ARGV[2]))
    return 1
else
    return 0
end
"""

# Lua 脚本：原子性释放信号量
# KEYS[1] = 信号量键
# 返回释放后的剩余计数
_RELEASE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current > 0 then
    local next = current - 1
    if next == 0 then
        redis.call('DEL', KEYS[1])
    else
        redis.call('SET', KEYS[1], next, 'EX', 300)
    end
    return next
else
    return 0
end
"""


def _redis_available() -> bool:
    """探测 Redis 是否可用。复用 rate_limit 的探测模式。"""
    if redis_lib is None or not settings.REDIS_URL:
        return False
    try:
        client = redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
        )
        client.ping()
        client.close()
        return True
    except Exception as e:
        logger.debug(f"Redis 不可用，分布式信号量将回退到 asyncio.Semaphore: {e}")
        return False


class RedisSemaphore:
    """基于 Redis 的分布式信号量。

    接口兼容 asyncio.Semaphore（acquire/release），
    便于在单实例或多实例环境下无缝切换。
    """

    def __init__(self, name: str, limit: int, ttl: int = 300):
        self._name = name
        self._key = f"{LEGACY_STATE_NAMESPACE}:semaphore:{name}"
        self._limit = limit
        self._ttl = ttl
        self._client: Optional["redis_lib.Redis"] = None
        self._acquire_sha: Optional[str] = None
        self._release_sha: Optional[str] = None
        # 轮询间隔（秒）
        self._poll_interval = 0.1

    def _get_client(self):
        """懒加载 Redis 客户端。"""
        if self._client is None:
            self._client = redis_lib.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
            )
            # 预加载 Lua 脚本
            try:
                self._acquire_sha = self._client.script_load(_ACQUIRE_SCRIPT)
                self._release_sha = self._client.script_load(_RELEASE_SCRIPT)
            except Exception as e:
                logger.warning(f"分布式信号量 Lua 脚本加载失败: {e}")
                self._client = None
                return None
        return self._client

    def _drop_client(self):
        """丢弃客户端实例（操作失败时调用，触发重连）。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                # 关闭时出错也无妨，反正要丢弃；记 debug 便于排查
                logger.debug("redis 客户端关闭失败（已忽略）: %s", e)
        self._client = None
        self._acquire_sha = None
        self._release_sha = None

    async def acquire(self) -> bool:
        """获取信号量。与 asyncio.Semaphore.acquire() 接口兼容。

        注意：与 asyncio.Semaphore 不同，此方法在已满时立即返回 False，
        调用方应配合 asyncio.wait_for 实现超时等待。
        但为兼容 asyncio.wait_for 的接口，此方法会内部轮询直到获取成功或超时。
        """
        while True:
            client = self._get_client()
            if client is None:
                # Redis 不可用，返回 True 让调用方放行（降级策略）
                return True
            try:
                result = client.evalsha(
                    self._acquire_sha, 1, self._key, self._limit, self._ttl
                )
                if result == 1:
                    return True
                # 信号量已满，等待后重试
                await asyncio.sleep(self._poll_interval)
            except Exception as e:
                logger.warning(f"分布式信号量获取失败，降级放行: {e}")
                self._drop_client()
                return True

    def release(self) -> None:
        """释放信号量。与 asyncio.Semaphore.release() 接口兼容。"""
        client = self._get_client()
        if client is None:
            return
        try:
            client.evalsha(self._release_sha, 1, self._key)
        except Exception as e:
            logger.warning(f"分布式信号量释放失败: {e}")
            self._drop_client()


# 模块级缓存：信号量实例（与原 agents.py 的 _sse_semaphore 模式一致）
_instances: dict[str, object] = {}


def get_distributed_semaphore(name: str, limit: int) -> object:
    """获取分布式信号量实例。

    Redis 可用时返回 RedisSemaphore（多实例共享并发限制），
    Redis 不可用时回退到 asyncio.Semaphore（单实例行为）。

    与原 `_get_sse_semaphore()` 接口兼容，返回的对象都有 acquire()/release() 方法。
    注意：asyncio.Semaphore 必须在事件循环中创建，
    因此 asyncio.Semaphore 实例也懒加载（首次调用时创建）。
    """
    if name in _instances:
        return _instances[name]

    if _redis_available():
        semaphore = RedisSemaphore(name, limit)
        logger.info(f"使用 Redis 分布式信号量: {name} (limit={limit})")
    else:
        semaphore = asyncio.Semaphore(limit)
        logger.info(f"使用 asyncio 本地信号量: {name} (limit={limit})")

    _instances[name] = semaphore
    return semaphore
