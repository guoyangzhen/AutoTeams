"""5.3.5 业务缓存层基础工具。

提供通用 Redis 缓存原语，供 service 层复用。本模块仅提供工具函数，
不修改任何业务代码；业务方按需在 service 层显式调用。

设计要点：
- 复用 rate_limit.py 的 Redis 连接参数（decode_responses=True、
  socket_connect_timeout=2、socket_timeout=2）。rate_limit.py 仅暴露
  _redis_available()（返回 bool，每次新建临时客户端探测），未提供可复用的
  客户端获取函数，故本模块自行维护一个懒加载的模块级客户端缓存。
- 客户端创建时 ping 探活；后续调用不再 ping（缓存读路径需低延迟）。
  操作过程中遇到 RedisError 时丢弃客户端，下次调用自动重连，实现自愈。
- 统一键前缀 ``autoteams:cache:``，避免与 token_blacklist / rate_limit
  命名空间冲突。
- 批量删除使用 SCAN（非 KEYS，避免阻塞 Redis）+ UNLINK（非阻塞删除，
  Redis < 4.0 回退 DELETE），生产安全。
- 降级策略：redis 包未安装、REDIS_URL 未配置或连接/操作失败时，所有操作
  返回 None/False/0，绝不抛异常，业务调用方无需 try/except 包裹。
"""
import functools
import json
import logging
from typing import Any, Awaitable, Callable, Optional, Type, TypeVar

from app.config import settings
from app.utils.redis_constants import REDIS_SOCKET_CONNECT_TIMEOUT, REDIS_SOCKET_TIMEOUT

try:
    import redis
except ModuleNotFoundError:
    redis = None  # type: ignore

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 统一缓存键前缀，与 token_blacklist / rate_limit 命名空间隔离
_CACHE_PREFIX = "autoteams:cache"

# 模块级共享 Redis 客户端（懒加载）；操作失败时置 None 触发重连
_REDIS_CLIENT: Optional["redis.Redis"] = None


def get_redis_client() -> Optional["redis.Redis"]:
    """获取共享 Redis 客户端（懒加载）。

    复用 rate_limit.py 的连接参数（decode_responses=True、socket_connect_timeout=2、
    socket_timeout=2）。客户端在模块级缓存，仅创建时 ping 探活；后续调用直接返回
    缓存实例以降低读路径延迟。操作层面遇到 RedisError 时由调用方丢弃客户端，
    下次调用自动重连。

    Returns:
        redis.Redis 实例；当 redis 包未安装、REDIS_URL 未配置或连接失败时返回 None。
    """
    global _REDIS_CLIENT
    if redis is None:
        return None

    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT

    if not settings.REDIS_URL:
        return None

    try:
        client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
        )
        client.ping()
        _REDIS_CLIENT = client
        return client
    except redis.RedisError as e:
        logger.warning(f"Redis 缓存连接失败，缓存功能将降级: {e}")
        return None
    except Exception as e:
        logger.warning(f"初始化 Redis 缓存客户端失败: {e}")
        return None


def _drop_client() -> None:
    """丢弃缓存的 Redis 客户端，下次 get_redis_client() 将重建连接。

    在操作层面捕获到 RedisError（尤其是连接断开）时调用，实现自愈。
    """
    global _REDIS_CLIENT
    _REDIS_CLIENT = None


def _full_key(key: str) -> str:
    """拼接统一前缀。"""
    return f"{_CACHE_PREFIX}:{key}"


def cache_get(key: str) -> Optional[str]:
    """从缓存读取字符串值。

    Args:
        key: 业务键（无需带前缀，模块自动拼接 ``autoteams:cache:`` 前缀）。

    Returns:
        缓存值字符串；键不存在、Redis 不可用或读取失败时返回 None。
    """
    client = get_redis_client()
    if client is None:
        return None
    try:
        return client.get(_full_key(key))
    except redis.RedisError as e:
        logger.warning(f"cache_get 失败 (key={key}): {e}")
        _drop_client()
        return None


def cache_set(key: str, value: str, ttl: int = 300) -> bool:
    """写入字符串缓存，带 TTL。

    Args:
        key: 业务键。
        value: 缓存值字符串。
        ttl: 过期秒数，默认 300（5 分钟）。ttl <= 0 视为不缓存。

    Returns:
        写入成功返回 True；Redis 不可用、参数非法或写入失败返回 False。
    """
    if ttl <= 0:
        return False
    client = get_redis_client()
    if client is None:
        return False
    try:
        client.set(_full_key(key), value, ex=ttl)
        return True
    except redis.RedisError as e:
        logger.warning(f"cache_set 失败 (key={key}): {e}")
        _drop_client()
        return False


def cache_delete(key: str) -> bool:
    """删除单个缓存键。

    Args:
        key: 业务键。

    Returns:
        成功删除（含键原本就不存在）返回 True；Redis 不可用或出错返回 False。
    """
    client = get_redis_client()
    if client is None:
        return False
    try:
        client.delete(_full_key(key))
        return True
    except redis.RedisError as e:
        logger.warning(f"cache_delete 失败 (key={key}): {e}")
        _drop_client()
        return False


def cache_invalidate_pattern(pattern: str) -> int:
    """按模式批量删除缓存键。

    使用 SCAN（非 KEYS，避免阻塞 Redis）迭代匹配键，优先用 UNLINK（非阻塞删除），
    Redis < 4.0 或 UNLINK 失败时回退到 DELETE。

    Args:
        pattern: glob 模式，如 ``"user:*"``；自动拼接 ``autoteams:cache:`` 前缀。

    Returns:
        实际删除的键数量；Redis 不可用或出错返回 0。
    """
    client = get_redis_client()
    if client is None:
        return 0
    full_pattern = _full_key(pattern)
    try:
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = client.scan(
                cursor=cursor, match=full_pattern, count=200
            )
            keys.extend(batch)
            if cursor == 0:
                break
        if not keys:
            return 0
        try:
            deleted = client.unlink(*keys)
        except redis.RedisError:
            deleted = client.delete(*keys)
        return int(deleted)
    except redis.RedisError as e:
        logger.warning(f"cache_invalidate_pattern 失败 (pattern={pattern}): {e}")
        _drop_client()
        return 0


def cache_set_json(key: str, obj: Any, ttl: int = 300) -> bool:
    """将 Python 对象 JSON 序列化后写入缓存。

    支持普通可序列化对象及 Pydantic 模型（通过 model_dump()/dict() 适配）。

    Args:
        key: 业务键。
        obj: 待缓存对象。
        ttl: 过期秒数，默认 300。

    Returns:
        写入成功返回 True；序列化失败、Redis 不可用或写入失败返回 False。
    """
    try:
        payload = json.dumps(obj, default=_json_default, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.warning(f"cache_set_json 序列化失败 (key={key}): {e}")
        return False
    return cache_set(key, payload, ttl=ttl)


def cache_get_json(key: str, expected_type: Optional[Type] = None) -> Any:
    """从缓存读取并 JSON 反序列化为 Python 对象。

    Args:
        key: 业务键。
        expected_type: 期望的反序列化结果类型（如 ``dict``、``list``）。
            若指定且结果类型不匹配，视为未命中返回 None，避免类型污染。

    Returns:
        反序列化后的对象；键不存在、反序列化失败、类型不匹配或 Redis 不可用时
        返回 None。
    """
    raw = cache_get(key)
    if raw is None:
        return None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.warning(f"cache_get_json 反序列化失败 (key={key}): {e}")
        return None
    if expected_type is not None and not isinstance(obj, expected_type):
        logger.debug(
            f"cache_get_json 类型不匹配 (key={key}): "
            f"期望 {expected_type.__name__}, 实际 {type(obj).__name__}"
        )
        return None
    return obj


def cached(
    key_builder: Callable[..., Optional[str]],
    ttl: int = 300,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """异步函数结果缓存装饰器。

    自动缓存被装饰异步函数的返回值（JSON 序列化）。命中时直接返回反序列化结果，
    未命中时执行函数并写回缓存。Redis 不可用时透明降级为直接调用，不影响业务。

    不会缓存 ``None`` 返回值，避免缓存穿透语义混淆。

    Args:
        key_builder: 接收与被装饰函数相同参数、返回缓存键字符串的回调。
            返回 None 表示本次跳过缓存（直接执行函数不读不写）。
        ttl: 缓存过期秒数，默认 300。

    Returns:
        装饰器函数。

    Example:
        >>> @cached(key_builder=lambda user_id: f"user:{user_id}", ttl=60)
        ... async def get_user_profile(user_id: int):
        ...     ...
    """

    def decorator(
        func: Callable[..., Awaitable[T]]
    ) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            cache_key = key_builder(*args, **kwargs)
            if cache_key is not None:
                hit = cache_get_json(cache_key)
                if hit is not None:
                    return hit  # type: ignore
            result = await func(*args, **kwargs)
            if cache_key is not None and result is not None:
                cache_set_json(cache_key, result, ttl=ttl)
            return result

        return wrapper

    return decorator


def _json_default(obj: Any) -> Any:
    """json.dumps 的 default 回调，适配常见不可直接序列化类型。

    优先支持 Pydantic v2 (model_dump) / v1 (dict)，其余回退为 str。
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict") and callable(obj.dict):
        try:
            return obj.dict()
        except Exception as e:
            # Pydantic v1 dict() 在极端情况下（如未初始化字段）可能抛错
            logger.debug("obj.dict() 序列化失败，回退为 str: %s", e)
    return str(obj)
