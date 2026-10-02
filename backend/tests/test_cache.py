"""5.3.5: app/utils/cache.py 覆盖率补充测试。

测试覆盖：
- Redis 不可用时的降级路径（REDIS_URL 为空，所有操作返回 None/False/0）
- Redis 可用时的正常路径（通过 monkeypatch 注入 fake client）
- cache_get / cache_set / cache_delete / cache_invalidate_pattern
- cache_set_json / cache_get_json（含类型校验、序列化失败）
- cached 装饰器（命中/未命中/不缓存 None/key_builder 返回 None）
- 客户端故障自愈（操作失败后丢弃客户端，下次重建）
"""
import pytest

from app.utils import cache as cache_mod
from app.utils.cache import (
    cache_get,
    cache_set,
    cache_delete,
    cache_invalidate_pattern,
    cache_set_json,
    cache_get_json,
    cached,
    get_redis_client,
)


class _FakeRedisError(Exception):
    """模拟 redis.RedisError 的子类。"""


class _FakeRedisClient:
    """模拟 redis.Redis 客户端，记录所有调用以便断言。

    所有方法都返回可配置的预设值；scan/unlink/delete 模拟批量操作。
    """

    def __init__(self, *, store=None, fail_ops=()):
        # 内部存储：键 → 值（字符串）
        self._store = store if store is not None else {}
        # 需要失败的 operation 名集合（如 {"get", "set"}）
        self._fail_ops = set(fail_ops)
        # 调用记录（用于断言）
        self.calls = []

    def _record(self, op, *args, **kwargs):
        self.calls.append((op, args, kwargs))
        if op in self._fail_ops:
            raise _FakeRedisError(f"模拟 {op} 失败")

    def ping(self):
        self._record("ping")
        return True

    def get(self, key):
        self._record("get", key)
        return self._store.get(key)

    def set(self, key, value, ex=None):
        self._record("set", key, value, ex)
        self._store[key] = value
        return True

    def delete(self, *keys):
        self._record("delete", *keys)
        deleted = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                deleted += 1
        return deleted

    def unlink(self, *keys):
        self._record("unlink", *keys)
        return self.delete(*keys)

    def scan(self, cursor=0, match=None, count=200):
        self._record("scan", cursor, match, count)
        # 简化实现：一次返回所有匹配键
        if match is None:
            matched = list(self._store.keys())
        else:
            # glob 简化匹配：仅支持 * 通配
            import fnmatch
            matched = [k for k in self._store.keys() if fnmatch.fnmatch(k, match)]
        return 0, matched

    def close(self):
        self._record("close")


@pytest.fixture
def degraded_cache(monkeypatch):
    """强制 cache 进入降级模式：REDIS_URL 为空且 redis 包不可用。

    复用 conftest.py 已设置的 REDIS_URL='' 环境，但额外清空模块级客户端缓存，
    确保每个测试用例独立。
    """
    monkeypatch.setattr(cache_mod, "_REDIS_CLIENT", None)
    # 模拟 redis 包未安装
    monkeypatch.setattr(cache_mod, "redis", None)
    yield


@pytest.fixture
def fake_redis(monkeypatch):
    """注入 fake Redis 客户端，模拟 Redis 可用场景。

    返回 fake 客户端实例，测试可对其 store/calls 进行断言。
    """
    fake = _FakeRedisClient()

    def _fake_get_client():
        return fake

    # 绕过 ping 探活，直接返回 fake 客户端
    monkeypatch.setattr(cache_mod, "redis", type("R", (), {"RedisError": _FakeRedisError, "from_url": staticmethod(lambda *a, **kw: fake)}))
    monkeypatch.setattr(cache_mod, "_REDIS_CLIENT", fake)
    monkeypatch.setattr(cache_mod, "get_redis_client", _fake_get_client)
    return fake


# ============================================================
# 降级路径：Redis 不可用
# ============================================================


class TestCacheDegraded:
    """Redis 不可用时所有操作应透明降级，不抛异常。"""

    def test_get_returns_none_when_degraded(self, degraded_cache):
        assert cache_get("any_key") is None

    def test_set_returns_false_when_degraded(self, degraded_cache):
        assert cache_set("k", "v", ttl=60) is False

    def test_delete_returns_false_when_degraded(self, degraded_cache):
        assert cache_delete("k") is False

    def test_invalidate_pattern_returns_zero_when_degraded(self, degraded_cache):
        assert cache_invalidate_pattern("user:*") == 0

    def test_set_json_returns_false_when_degraded(self, degraded_cache):
        assert cache_set_json("k", {"a": 1}, ttl=60) is False

    def test_get_json_returns_none_when_degraded(self, degraded_cache):
        assert cache_get_json("k") is None

    def test_get_redis_client_returns_none_when_degraded(self, degraded_cache):
        assert get_redis_client() is None


# ============================================================
# 正常路径：Redis 可用
# ============================================================


class TestCacheGetSet:
    """cache_get / cache_set 基本读写与 TTL 处理。"""

    def test_set_then_get(self, fake_redis):
        assert cache_set("foo", "bar", ttl=60) is True
        # 内部 store 应包含带前缀的键
        assert fake_redis._store["autoteams:cache:foo"] == "bar"
        assert cache_get("foo") == "bar"

    def test_get_missing_key_returns_none(self, fake_redis):
        assert cache_get("nonexistent") is None

    def test_set_with_non_positive_ttl_returns_false(self, fake_redis):
        # ttl <= 0 视为不缓存
        assert cache_set("k", "v", ttl=0) is False
        assert cache_set("k", "v", ttl=-1) is False
        # 不应写入 store
        assert "autoteams:cache:k" not in fake_redis._store

    def test_set_passes_ttl_to_redis(self, fake_redis):
        cache_set("k", "v", ttl=120)
        # 检查 set 调用的 ex 参数
        set_calls = [c for c in fake_redis.calls if c[0] == "set"]
        assert len(set_calls) == 1
        assert set_calls[0][1] == ("autoteams:cache:k", "v", 120)

    def test_key_has_unified_prefix(self, fake_redis):
        cache_set("user:123", "data", ttl=60)
        assert "autoteams:cache:user:123" in fake_redis._store


class TestCacheDelete:
    """cache_delete 单键删除。"""

    def test_delete_existing_key(self, fake_redis):
        cache_set("k", "v", ttl=60)
        assert cache_delete("k") is True
        assert cache_get("k") is None

    def test_delete_missing_key_returns_true(self, fake_redis):
        # 删除不存在的键也应返回 True（语义：最终状态一致）
        assert cache_delete("never_existed") is True


class TestCacheInvalidatePattern:
    """cache_invalidate_pattern 批量删除。"""

    def test_invalidate_matching_keys(self, fake_redis):
        cache_set("user:1", "a", ttl=60)
        cache_set("user:2", "b", ttl=60)
        cache_set("post:1", "c", ttl=60)

        deleted = cache_invalidate_pattern("user:*")
        assert deleted == 2
        # user:* 已删除，post:* 保留
        assert cache_get("user:1") is None
        assert cache_get("user:2") is None
        assert cache_get("post:1") == "c"

    def test_invalidate_no_match_returns_zero(self, fake_redis):
        cache_set("post:1", "c", ttl=60)
        deleted = cache_invalidate_pattern("user:*")
        assert deleted == 0

    def test_invalidate_falls_back_to_delete_on_unlink_failure(self, monkeypatch):
        """UNLINK 失败时应回退到 DELETE。"""
        fake = _FakeRedisClient()
        # 让 unlink 失败但 delete 成功
        fake._fail_ops = {"unlink"}
        monkeypatch.setattr(cache_mod, "redis", type("R", (), {"RedisError": _FakeRedisError, "from_url": staticmethod(lambda *a, **kw: fake)}))
        monkeypatch.setattr(cache_mod, "_REDIS_CLIENT", fake)
        monkeypatch.setattr(cache_mod, "get_redis_client", lambda: fake)

        cache_set("k1", "v1", ttl=60)
        deleted = cache_invalidate_pattern("k*")
        assert deleted == 1


# ============================================================
# JSON 序列化路径
# ============================================================


class TestCacheJson:
    """cache_set_json / cache_get_json 序列化与类型校验。"""

    def test_set_then_get_json_dict(self, fake_redis):
        assert cache_set_json("obj", {"a": 1, "b": "hello"}, ttl=60) is True
        result = cache_get_json("obj")
        assert result == {"a": 1, "b": "hello"}

    def test_set_then_get_json_list(self, fake_redis):
        cache_set_json("lst", [1, 2, 3], ttl=60)
        assert cache_get_json("lst") == [1, 2, 3]

    def test_get_json_with_expected_type_match(self, fake_redis):
        cache_set_json("d", {"k": "v"}, ttl=60)
        assert cache_get_json("d", expected_type=dict) == {"k": "v"}

    def test_get_json_with_expected_type_mismatch_returns_none(self, fake_redis):
        cache_set_json("d", {"k": "v"}, ttl=60)
        # 期望 list 但实际 dict，应返回 None
        assert cache_get_json("d", expected_type=list) is None

    def test_get_json_with_corrupted_data_returns_none(self, fake_redis):
        # 直接写入损坏的 JSON 字符串
        fake_redis._store["autoteams:cache:bad"] = "{not valid json"
        assert cache_get_json("bad") is None

    def test_set_json_serialization_failure_returns_false(self, fake_redis):
        """_json_default 调用 str(obj) 抛 TypeError 时应返回 False。

        _json_default 最终回退到 str(obj)；当 str(obj) 抛 TypeError 时，
        json.dumps 会向上传播，cache_set_json 捕获后返回 False。
        """
        class TypeErrObj:
            def __repr__(self):
                raise TypeError("force type error")

            def __str__(self):
                raise TypeError("force type error")

        result = cache_set_json("k", TypeErrObj(), ttl=60)
        assert result is False

    def test_set_json_supports_pydantic_v2_model(self, fake_redis):
        """支持 Pydantic v2 model_dump 适配。"""
        from pydantic import BaseModel

        class Item(BaseModel):
            name: str
            price: float

        item = Item(name="book", price=9.9)
        assert cache_set_json("item", item, ttl=60) is True
        # 反序列化后字段一致
        result = cache_get_json("item")
        assert result["name"] == "book"
        assert result["price"] == 9.9


# ============================================================
# 装饰器 cached
# ============================================================


class TestCachedDecorator:
    """@cached 装饰器命中/未命中/降级行为。"""

    @pytest.mark.asyncio
    async def test_decorator_caches_result(self, fake_redis):
        call_count = 0

        @cached(key_builder=lambda x: f"double:{x}", ttl=60)
        async def double(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        # 首次调用：执行函数并写缓存
        assert await double(5) == 10
        assert call_count == 1
        # 二次调用：命中缓存，不执行函数
        assert await double(5) == 10
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_decorator_skips_cache_when_key_builder_returns_none(self, fake_redis):
        """key_builder 返回 None 时跳过缓存（直接执行，不读不写）。"""
        call_count = 0

        @cached(key_builder=lambda x: None, ttl=60)
        async def fn(x):
            nonlocal call_count
            call_count += 1
            return x

        assert await fn(1) == 1
        assert await fn(1) == 1
        assert call_count == 2  # 两次都执行

    @pytest.mark.asyncio
    async def test_decorator_does_not_cache_none(self, fake_redis):
        """None 返回值不应被缓存（避免缓存穿透语义混淆）。"""
        call_count = 0

        @cached(key_builder=lambda x: f"fn:{x}", ttl=60)
        async def fn(x):
            nonlocal call_count
            call_count += 1
            return None

        assert await fn(1) is None
        assert await fn(1) is None
        assert call_count == 2  # None 不缓存，两次都执行

    @pytest.mark.asyncio
    async def test_decorator_degrades_to_direct_call_when_redis_unavailable(self, degraded_cache):
        """Redis 不可用时装饰器应透明降级为直接调用。"""
        call_count = 0

        @cached(key_builder=lambda x: f"fn:{x}", ttl=60)
        async def fn(x):
            nonlocal call_count
            call_count += 1
            return x * 10

        assert await fn(2) == 20
        assert await fn(2) == 20
        # 降级时每次都执行
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_caches_complex_object(self, fake_redis):
        """装饰器应支持复杂可序列化对象。"""
        call_count = 0

        @cached(key_builder=lambda uid: f"user:{uid}", ttl=60)
        async def get_user(uid):
            nonlocal call_count
            call_count += 1
            return {"id": uid, "name": "alice", "roles": ["admin", "user"]}

        result1 = await get_user("u1")
        result2 = await get_user("u1")
        assert result1 == result2
        assert call_count == 1


# ============================================================
# 故障自愈
# ============================================================


class TestClientSelfHealing:
    """操作失败时应丢弃客户端，下次调用重建。"""

    def test_get_drops_client_on_failure(self, monkeypatch):
        """cache_get 捕获 RedisError 后应丢弃客户端。"""
        fake = _FakeRedisClient(fail_ops={"get"})

        monkeypatch.setattr(cache_mod, "redis", type("R", (), {"RedisError": _FakeRedisError, "from_url": staticmethod(lambda *a, **kw: fake)}))
        monkeypatch.setattr(cache_mod, "_REDIS_CLIENT", fake)
        # get_redis_client 直接返回缓存的 fake（避免 ping 探活）
        monkeypatch.setattr(cache_mod, "get_redis_client", lambda: fake)

        # 首次 get 失败应返回 None
        assert cache_get("k") is None
        # 客户端应被丢弃
        assert cache_mod._REDIS_CLIENT is None

    def test_set_drops_client_on_failure(self, monkeypatch):
        """cache_set 捕获 RedisError 后应丢弃客户端。"""
        fake = _FakeRedisClient(fail_ops={"set"})
        monkeypatch.setattr(cache_mod, "redis", type("R", (), {"RedisError": _FakeRedisError, "from_url": staticmethod(lambda *a, **kw: fake)}))
        monkeypatch.setattr(cache_mod, "_REDIS_CLIENT", fake)
        monkeypatch.setattr(cache_mod, "get_redis_client", lambda: fake)

        assert cache_set("k", "v", ttl=60) is False
        assert cache_mod._REDIS_CLIENT is None
