"""测试公共 fixtures。

使用内存 SQLite 隔离测试数据库，不干扰开发环境的 autoteams.db。
"""
import os

# 必须在任何 app / chromadb 导入之前执行：Chroma 的 ONNX 缓存路径在类定义时就固定。
from tests._chroma_cache_env import ensure_chroma_onnx_cache_writable  # noqa: E402

ensure_chroma_onnx_cache_writable()

# P3-5: 测试环境放宽限流，避免大量测试共享内存存储导致 429
os.environ.setdefault("RATE_LIMIT_AUTH_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_API_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_ADMIN_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_UPLOAD_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_SCAN_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_HEALTH_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_METRICS_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_SSE_PER_MINUTE", "10000")
os.environ.setdefault("RATE_LIMIT_CHAT_PER_MINUTE", "10000")
# P3-5: 测试环境使用嵌入式 ChromaDB，避免依赖外部 Chroma 服务
os.environ.setdefault("CHROMA_HOST", "")
# P1/P2-INFRA: 测试环境不连接 Redis，使用内存回退（token 黑名单、限流均走内存）
os.environ.setdefault("REDIS_URL", "")

# P3-5: 测试环境必须提供安全 JWT 密钥并开启 DEBUG，避免 config.py 的启动安全检查失败
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only-32chars-plus"
)
os.environ.setdefault("DEBUG", "true")
# 测试套件大量用例（auth/shadow/…）依赖公开注册创建用户；项目根 .env 关闭了注册，
# 这里在测试环境强制开启，避免测试被 REGISTRATION_DISABLED 拦截。
os.environ.setdefault("REGISTRATION_ENABLED", "true")
# O-07: 测试环境禁用 APScheduler，避免后台定时任务干扰测试或抢占事件循环
os.environ["LOOP_SCHEDULER_ENABLED"] = "false"
# 测试环境统一将默认 LLM Provider 隔离为 openai 基线，避免本地 .env 配置污染单测期望
os.environ["DEFAULT_LLM_PROVIDER"] = "openai"
os.environ["OPENAI_MODEL"] = "gpt-3.5-turbo"
os.environ["OPENAI_MODEL_STRONG"] = "gpt-4"
os.environ["OPENAI_MODEL_CHEAP"] = "gpt-3.5-turbo"
os.environ["LLM_ALLOW_FALLBACK"] = "true"

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings
from app.database import Base, get_db
# 导入所有模型，确保 Base.metadata 包含全部表定义
import app.models  # noqa: F401
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_engine():
    """每个测试函数用独立的内存数据库，避免测试间互相污染。"""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    """独立的数据库 session，用于直接操作测试数据库。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


class _CSRFClient:
    """BE-SEC-01: 自动为状态变更请求附加 X-CSRF-Token header 的测试客户端包装。

    大部分集成测试不需要关心 CSRF 细节；CSRF 专项测试可使用 raw_client fixture。
    """

    def __init__(self, inner: AsyncClient):
        self._inner = inner

    async def _request(self, method: str, url, **kwargs):
        if method in ("post", "put", "patch", "delete"):
            csrf = self._inner.cookies.get("csrf_token")
            if csrf:
                headers = kwargs.setdefault("headers", {})
                headers["X-CSRF-Token"] = csrf
        return await getattr(self._inner, method)(url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._request("post", url, **kwargs)

    async def put(self, url, **kwargs):
        return await self._request("put", url, **kwargs)

    async def patch(self, url, **kwargs):
        return await self._request("patch", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._request("delete", url, **kwargs)

    async def get(self, url, **kwargs):
        return await self._inner.get(url, **kwargs)

    async def head(self, url, **kwargs):
        return await self._inner.head(url, **kwargs)

    async def options(self, url, **kwargs):
        return await self._inner.options(url, **kwargs)

    async def request(self, method: str, url, **kwargs):
        return await self._request(method.lower(), url, **kwargs)

    @property
    def cookies(self):
        return self._inner.cookies

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest_asyncio.fixture
async def raw_client(test_engine):
    """原始 FastAPI 测试客户端（不自动附加 CSRF header），用于 CSRF 专项测试。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(raw_client):
    """FastAPI 测试客户端，覆盖 get_db 使用测试数据库；自动处理 CSRF token。"""
    yield _CSRFClient(raw_client)


async def register_user(client: AsyncClient, email: str, password: str, name: str):
    """P3-5: 注册测试用户。Cookie 由 httpx 自动保存。"""
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": name,
        "password": password,
    })
    assert resp.status_code == 201, f"注册失败: {resp.text}"
    return resp


async def login_user(client: AsyncClient, email: str, password: str):
    """P3-5: 登录测试用户。Cookie 由 httpx 自动保存。"""
    resp = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": password,
    })
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp


@pytest_asyncio.fixture
async def authenticated_client(client):
    """P3-5: 已登录的测试客户端，Cookie 中已包含 access_token。"""
    await register_user(client, "auth@test.com", "pass1234", "测试用户")
    yield client


@pytest_asyncio.fixture
async def enterprise_authenticated_client(client, test_engine):
    """已登录且绑定企业归属的测试客户端。

    用途：loop/process 等端点通过 `_verify_agent_access` / `_verify_task_access`
    校验企业归属，无企业用户（enterprise_id=None 且非 admin）会收到 403。
    本 fixture 注册用户并绑定到固定企业 `ent-test`，使这类测试能拿到 404/200。
    """
    from app.models.enterprise import Enterprise
    from app.models.user import User
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        ent = Enterprise(id="ent-test", name="测试企业")
        s.add(ent)
        await s.commit()

    await register_user(client, "entuser@test.com", "pass1234", "企业用户")
    # 从 DB 读取新注册用户 id 并绑定企业
    async with factory() as s:
        result = await s.execute(
            select(User).where(User.email == "entuser@test.com")
        )
        user = result.scalar_one_or_none()
        assert user is not None, "企业用户注册失败"
        user.enterprise_id = "ent-test"
        await s.commit()

    yield client


@pytest.fixture
def upload_root(monkeypatch, tmp_path):
    """P1-SANDBOX: 为测试提供临时上传根目录，避免污染开发环境的 uploads。"""
    root = tmp_path / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "UPLOAD_ROOT", str(root))
    yield str(root)


@pytest.fixture(autouse=True)
def _reset_audit_signature_cache():
    """3.2.2: 每个测试用例前重置审计签名缓存，避免跨测试污染。

    缓存是进程级的，而 test_engine fixture 为每个测试创建独立的内存 DB，
    若不重置，前一个测试的缓存会被下一个测试误用，导致 prev_hash 链断裂。
    """
    from app.utils.audit import reset_audit_cache
    reset_audit_cache()
    yield


# ============================================================
# 共享 fixtures 自动发现（AUD-21）
# ============================================================
# mock_llm / mock_vector_store / mock_redis / v3_tables / demo_enterprise 定义在
# conftest_extensions.py。pytest 只从 conftest 模块的命名空间收集 fixture，
# 因此这里显式导入；此前每个测试文件各自 `from tests.conftest_extensions import ...`，
# 让同一名字同时出现在模块作用域和 fixture 参数里（160 处 F811），
# 新增 fixture 也容易被漏掉。
from tests.conftest_extensions import (  # noqa: E402
    DEMO_ENTERPRISE_ID,
    demo_enterprise,
    mock_llm,
    mock_llm_with_responses,
    mock_redis,
    mock_vector_store,
    v3_tables,
)


# ============================================================
# Chroma ONNX 模型缓存可写性（测试环境修复）
# ============================================================
# chromadb 的 ONNXMiniLM_L6_V2 在**类定义时**用 `Path.home()` 算出模型缓存目录。
# 在部分机器上 `~/.cache/chroma/onnx_models` 存在但 ACL 不可读，于是向量化步骤
# 以 "Permission denied" 失败 —— 看起来像产品缺陷，实际是环境问题。
#
# 这里只在默认路径**不可写**时改道到临时目录，正常机器完全不受影响。
@pytest.fixture(scope="session", autouse=True)
def _chroma_onnx_cache_usable():
    import os
    import tempfile
    from pathlib import Path

    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    default_path = getattr(ONNXMiniLM_L6_V2, "DOWNLOAD_PATH", None)
    if default_path is None:
        yield  # 该版本没有这个属性，交给原始行为
        return

    probe = Path(default_path) / ".write-probe"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        yield  # 默认路径可写，保持原样
        return
    except OSError:
        pass

    fallback = Path(tempfile.gettempdir()) / "autoteams-chroma-onnx-cache"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        ONNXMiniLM_L6_V2.DOWNLOAD_PATH = fallback
        os.environ["AUTOTEAMS_CHROMA_ONNX_CACHE_REDIRECTED"] = str(fallback)
    except OSError:
        yield  # 改道也失败就交回原始错误，不掩盖问题
        return
    yield
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = default_path
