"""5.3.3: app/utils/distributed_semaphore.py 覆盖率补充测试。

测试覆盖：
- Redis 不可用时回退到 asyncio.Semaphore（与原 SSE 信号量行为一致）
- RedisSemaphore acquire/release 通过 fake Redis 验证 Lua 脚本调用
- 信号量已满时的轮询等待行为
- 操作失败时的降级放行（acquire 返回 True）与客户端丢弃自愈
- get_distributed_semaphore 实例缓存（同 name 返回同一实例）
"""
import asyncio

import pytest

from app.utils import distributed_semaphore as ds_mod
from app.utils.distributed_semaphore import (
    RedisSemaphore,
    get_distributed_semaphore,
)


class _FakeRedisError(Exception):
    """模拟 redis.RedisError。"""


class _FakeRedisClient:
    """模拟 redis.Redis，记录 evalsha 调用并可控制返回值。

    - acquire_results: list[int]，每次 acquire 调用依次返回这些值
    - release_results: list[int]，每次 release 调用依次返回这些值
    - fail_evalsha: bool，evalsha 是否抛异常

    acquire 与 release 通过 evalsha 的 args 数量区分：
    - acquire: evalsha(sha, 1, key, limit, ttl) — 3 args
    - release: evalsha(sha, 1, key) — 1 arg
    """

    def __init__(self, *, acquire_results=None, release_results=None, fail_evalsha=False):
        self._acquire_results = list(acquire_results or [1])
        self._release_results = list(release_results or [0])
        self._fail_evalsha = fail_evalsha
        self.calls = []

    def script_load(self, script):
        self.calls.append(("script_load", script))
        return f"sha_{abs(hash(script)) % 10000}"

    def evalsha(self, sha, numkeys, *args):
        self.calls.append(("evalsha", sha, numkeys, args))
        if self._fail_evalsha:
            raise _FakeRedisError("evalsha 失败")
        # 通过 args 数量区分 acquire(3) 与 release(1)
        if len(args) >= 3:
            # acquire 调用
            return self._acquire_results.pop(0) if self._acquire_results else 1
        else:
            # release 调用
            return self._release_results.pop(0) if self._release_results else 0

    def close(self):
        self.calls.append(("close",))


def _install_fake_redis(monkeypatch, fake):
    """让 distributed_semaphore 模块使用 fake Redis。"""
    fake_redis_module = type("redis", (), {
        "RedisError": _FakeRedisError,
        "from_url": staticmethod(lambda *a, **kw: fake),
    })
    monkeypatch.setattr(ds_mod, "redis_lib", fake_redis_module)
    # 让 _redis_available() 直接返回 True
    monkeypatch.setattr(ds_mod, "_redis_available", lambda: True)


# ============================================================
# 降级路径：Redis 不可用
# ============================================================


class TestDegradedFallback:
    """Redis 不可用时应回退到 asyncio.Semaphore。"""

    def test_get_distributed_semaphore_returns_asyncio_when_redis_unavailable(self, monkeypatch):
        """REDIS_URL 为空或 redis 包未安装时，返回 asyncio.Semaphore。"""
        monkeypatch.setattr(ds_mod, "_redis_available", lambda: False)
        # 清空实例缓存
        monkeypatch.setattr(ds_mod, "_instances", {})

        sem = get_distributed_semaphore("test_degraded", 5)
        assert isinstance(sem, asyncio.Semaphore)
        assert sem._value == 5

    @pytest.mark.asyncio
    async def test_asyncio_semaphore_acquire_release(self, monkeypatch):
        """asyncio.Semaphore 回退路径应可正常 acquire/release。"""
        monkeypatch.setattr(ds_mod, "_redis_available", lambda: False)
        monkeypatch.setattr(ds_mod, "_instances", {})

        sem = get_distributed_semaphore("test_async", 2)
        await sem.acquire()
        await sem.acquire()
        # 第 3 次应阻塞，超时后未获取
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sem.acquire(), timeout=0.1)
        sem.release()
        sem.release()


# ============================================================
# Redis 可用路径
# ============================================================


class TestRedisSemaphoreAcquire:
    """RedisSemaphore.acquire() 通过 Lua 脚本原子获取。"""

    @pytest.mark.asyncio
    async def test_acquire_success_returns_true(self, monkeypatch):
        fake = _FakeRedisClient(acquire_results=[1])
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_acquire_success", 5)
        result = await asyncio.wait_for(sem.acquire(), timeout=1.0)
        assert result is True
        # 应有一次 evalsha 调用
        evalsha_calls = [c for c in fake.calls if c[0] == "evalsha"]
        assert len(evalsha_calls) == 1

    @pytest.mark.asyncio
    async def test_acquire_polls_when_full(self, monkeypatch):
        """信号量已满时应轮询，最终获取成功。"""
        # 第一次返回 0（已满），第二次返回 1（成功）
        fake = _FakeRedisClient(acquire_results=[0, 1])
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_poll", 5, ttl=300)
        # 缩短轮询间隔避免测试慢
        sem._poll_interval = 0.01
        result = await asyncio.wait_for(sem.acquire(), timeout=2.0)
        assert result is True
        # 应有两次 acquire 调用
        evalsha_calls = [c for c in fake.calls if c[0] == "evalsha"]
        assert len(evalsha_calls) == 2

    @pytest.mark.asyncio
    async def test_acquire_degrades_to_true_on_evalsha_failure(self, monkeypatch):
        """Lua 脚本执行失败时应降级放行（返回 True）。"""
        fake = _FakeRedisClient(fail_evalsha=True)
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_fail", 5)
        result = await asyncio.wait_for(sem.acquire(), timeout=1.0)
        assert result is True
        # 失败后客户端应被丢弃
        assert sem._client is None

    @pytest.mark.asyncio
    async def test_acquire_degrades_when_client_unavailable(self, monkeypatch):
        """_get_client 返回 None 时应直接返回 True（降级放行）。"""
        # 模拟 script_load 失败导致 _get_client 返回 None
        fake = _FakeRedisClient()
        fake.script_load = lambda s: (_ for _ in ()).throw(_FakeRedisError("script_load 失败"))
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_no_client", 5)
        result = await sem.acquire()
        assert result is True


class TestRedisSemaphoreRelease:
    """RedisSemaphore.release() 通过 Lua 脚本原子释放。"""

    def test_release_calls_evalsha(self, monkeypatch):
        fake = _FakeRedisClient(acquire_results=[1], release_results=[0])
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_release", 5)
        # 强制初始化客户端
        client = sem._get_client()
        assert client is not None

        sem.release()
        # 应有 evalsha 调用（release）
        evalsha_calls = [c for c in fake.calls if c[0] == "evalsha"]
        assert len(evalsha_calls) >= 1

    def test_release_noop_when_client_unavailable(self, monkeypatch):
        """Redis 不可用时 release 应静默无操作。"""
        fake = _FakeRedisClient()
        fake.script_load = lambda s: (_ for _ in ()).throw(_FakeRedisError("script_load 失败"))
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_release_noop", 5)
        # _get_client 返回 None
        assert sem._get_client() is None
        # release 应静默返回，不抛异常
        sem.release()

    def test_release_drops_client_on_failure(self, monkeypatch):
        """release 失败时应丢弃客户端。"""
        fake = _FakeRedisClient(fail_evalsha=True)
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_release_fail", 5)
        # 先初始化客户端
        sem._get_client()
        assert sem._client is not None

        sem.release()
        # 失败后客户端应被丢弃
        assert sem._client is None


# ============================================================
# 实例缓存
# ============================================================


class TestGetInstanceCache:
    """get_distributed_semaphore 应按 name 缓存实例。"""

    def test_same_name_returns_same_instance(self, monkeypatch):
        """同名信号量应复用同一实例。"""
        monkeypatch.setattr(ds_mod, "_redis_available", lambda: False)
        monkeypatch.setattr(ds_mod, "_instances", {})

        sem1 = get_distributed_semaphore("shared", 5)
        sem2 = get_distributed_semaphore("shared", 10)  # limit 不同也复用
        assert sem1 is sem2

    def test_different_names_return_different_instances(self, monkeypatch):
        monkeypatch.setattr(ds_mod, "_redis_available", lambda: False)
        monkeypatch.setattr(ds_mod, "_instances", {})

        sem1 = get_distributed_semaphore("a", 5)
        sem2 = get_distributed_semaphore("b", 5)
        assert sem1 is not sem2

    def test_returns_redis_semaphore_when_redis_available(self, monkeypatch):
        """Redis 可用时应返回 RedisSemaphore 实例。"""
        fake = _FakeRedisClient()
        _install_fake_redis(monkeypatch, fake)
        monkeypatch.setattr(ds_mod, "_instances", {})

        sem = get_distributed_semaphore("redis_sem", 5)
        assert isinstance(sem, RedisSemaphore)
        assert sem._name == "redis_sem"
        assert sem._limit == 5


# ============================================================
# Lua 脚本预加载
# ============================================================


class TestLuaScriptPreload:
    """RedisSemaphore 在客户端初始化时预加载 acquire/release Lua 脚本。"""

    def test_script_load_called_on_init(self, monkeypatch):
        fake = _FakeRedisClient()
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_preload", 5)
        sem._get_client()

        script_load_calls = [c for c in fake.calls if c[0] == "script_load"]
        assert len(script_load_calls) == 2  # acquire + release
        # 第一个是 acquire 脚本（含 ARGV[1] = limit）
        acquire_script = script_load_calls[0][1]
        assert "GET" in acquire_script
        assert "SET" in acquire_script
        # 第二个是 release 脚本（含 DECR 语义）
        release_script = script_load_calls[1][1]
        assert "DEL" in release_script

    def test_script_load_failure_drops_client(self, monkeypatch):
        """script_load 失败时应丢弃客户端，_get_client 返回 None。"""
        fake = _FakeRedisClient()

        def fail_script_load(script):
            raise _FakeRedisError("script_load 失败")

        fake.script_load = fail_script_load
        _install_fake_redis(monkeypatch, fake)

        sem = RedisSemaphore("test_preload_fail", 5)
        assert sem._get_client() is None
        assert sem._client is None
        assert sem._acquire_sha is None
        assert sem._release_sha is None


# ============================================================
# 键命名约定
# ============================================================


class TestKeyNaming:
    """信号量键应带统一前缀 autofde:semaphore:{name}。"""

    def test_key_uses_unified_prefix(self, monkeypatch):
        monkeypatch.setattr(ds_mod, "_redis_available", lambda: False)
        monkeypatch.setattr(ds_mod, "_instances", {})

        sem = get_distributed_semaphore("sse", 50)
        # 仅 RedisSemaphore 有 _key 属性；asyncio.Semaphore 没有
        # 这里 Redis 不可用会返回 asyncio.Semaphore，所以单独构造 RedisSemaphore 验证
        rs = RedisSemaphore("sse", 50)
        assert rs._key == "autofde:semaphore:sse"
