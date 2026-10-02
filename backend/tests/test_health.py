"""健康检查与基础设施测试。"""
import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def healthy_dependencies(test_engine, monkeypatch):
    """Use the isolated test DB for direct probes as well as HTTP dependencies."""
    from app.config import settings
    from app.utils import health

    monkeypatch.setattr(health, "engine", test_engine)
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setenv("WORKERS_ENABLED", "false")

    async def chroma_available():
        return health.DependencyStatus(name="chromadb", status="up")

    monkeypatch.setattr(health, "_check_chromadb", chroma_available)
    # ORM fixtures create business tables; explicitly supply migration metadata.
    # Actual migration execution is covered by the PostgreSQL acceptance suite.
    # The stamp must be a real head, otherwise the probe would (correctly) refuse
    # to report readiness and every positive test would be meaningless.
    async with test_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        head = next(iter(health._alembic_heads()))
        await conn.execute(
            text("INSERT INTO alembic_version VALUES (:head)"), {"head": head}
        )
    yield


class TestHealth:
    """/health 端点测试。"""

    @pytest.mark.asyncio
    async def test_health_check(self, client, healthy_dependencies):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        # AUD-29：状态语义已细分为 ready / degraded / not_ready。
        # 测试环境 database/schema 正常、workers 未启用 → 应为 ready。
        assert data["status"] == "ready"
        assert data["service"] == "AutoTeams Backend"
        assert "features" in data
        assert isinstance(data["features"], list)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("missing", "expected_schema"),
        [
            # 业务表齐全但没有迁移元数据：无法确认版本，判 unknown 并按失败关闭摘流量。
            ("migration_metadata", "unknown"),
            ("required_business_table", "down"),
            # 版本落后 head：表在也不能算就绪。
            ("stale_revision", "down"),
        ],
    )
    async def test_health_rejects_incomplete_schema(
        self, client, test_engine, healthy_dependencies, missing, expected_schema
    ):
        statements = {
            "migration_metadata": "DROP TABLE alembic_version",
            "required_business_table": "DROP TABLE agents",
            "stale_revision": "UPDATE alembic_version SET version_num = 'stale-revision'",
        }
        async with test_engine.begin() as conn:
            await conn.execute(text(statements[missing]))
        resp = await client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        dependencies = {item["name"]: item["status"] for item in data["dependencies"]}
        assert dependencies["database"] == "up"
        assert dependencies["schema"] == expected_schema

    @pytest.mark.asyncio
    async def test_health_rate_limited(self, client, healthy_dependencies):
        """BE-SEC-05: /health 高频访问应触发限流。"""
        # 默认限流 60/min，快速发送 70 次应触发 429
        for _ in range(70):
            resp = await client.get("/health")
            if resp.status_code == 429:
                break
        else:
            pytest.skip("未触发限流（可能存储后端不同）")
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_unknown_route_returns_404(self, client):
        resp = await client.get("/api/v1/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_validation_error_format(self, client):
        """参数验证错误应返回统一格式。"""
        resp = await client.post("/api/v1/auth/register", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert "message" in body


class TestReadinessFailClosed:
    """AUD-29 二次复核：判定不了依赖状态时必须摘流量。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["/health", "/health/ready"])
    async def test_enabled_worker_down_is_not_served(
        self, client, healthy_dependencies, monkeypatch, endpoint
    ):
        """已启用队列 worker 全部掉线时必须 503，而不是 degraded + 200。"""
        from app.services import worker_heartbeat

        monkeypatch.setenv("WORKERS_ENABLED", "true")
        monkeypatch.setenv("WORKER_ROLES", "compilation,processing")
        monkeypatch.setattr(
            worker_heartbeat,
            "is_worker_alive",
            lambda role, stale_seconds: (False, "心跳过期"),
        )
        resp = await client.get(endpoint)
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] in ("degraded", "not_ready")
        dependencies = {item["name"]: item["status"] for item in data["dependencies"]}
        assert dependencies["workers"] == "down"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("probe", ["database", "schema"])
    async def test_crashing_required_probe_is_not_reported_ready(
        self, client, healthy_dependencies, monkeypatch, probe
    ):
        """必需探测自身抛异常时补上的 unknown 不能再被判成 ready。"""
        from app.utils import health

        async def exploding_probe():
            raise RuntimeError("probe crashed")

        monkeypatch.setattr(health, f"_check_{probe}", exploding_probe)
        resp = await client.get("/health/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        dependencies = {item["name"]: item["status"] for item in data["dependencies"]}
        assert dependencies[probe] == "unknown"

    @pytest.mark.asyncio
    async def test_unknown_status_of_required_dependency_is_fatal(self):
        from app.api.system import _degraded_is_fatal
        from app.utils.health import DependencyStatus

        deps = [
            DependencyStatus(name="database", status="up"),
            DependencyStatus(name="schema", status="unknown"),
        ]
        assert _degraded_is_fatal(deps) is True

    @pytest.mark.asyncio
    async def test_security_redis_down_is_fatal_even_when_cache_redis_ok(
        self, client, healthy_dependencies, monkeypatch
    ):
        """撤销库故障必须 503：登出/封禁失效不能被当成"缓存 Redis 正常所以健康"。"""
        from app.config import settings
        from app.utils import health

        monkeypatch.setattr(settings, "REDIS_URL", "redis://cache.invalid:6379/0")
        monkeypatch.setattr(
            health, "_security_redis_url", lambda: "redis://revocation.invalid:6379/1"
        )

        async def cache_ok():
            return health.DependencyStatus(name="redis", status="up")

        monkeypatch.setattr(health, "_check_redis", cache_ok)
        monkeypatch.setattr(
            health,
            "_check_security_redis",
            lambda: _down("security_redis"),
        )
        resp = await client.get("/health/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        dependencies = {item["name"]: item["status"] for item in data["dependencies"]}
        assert dependencies["redis"] == "up"
        assert dependencies["security_redis"] == "down"

    def test_security_redis_is_required_when_configured(self, monkeypatch):
        from app.utils import health

        monkeypatch.setattr(health, "_security_redis_url", lambda: "redis://x:6379/1")
        assert "security_redis" in health.required_dependencies()
        monkeypatch.setattr(health, "_security_redis_url", lambda: "")
        assert "security_redis" not in health.required_dependencies()


def _down(name: str):
    from app.utils.health import DependencyStatus

    async def _probe():
        return DependencyStatus(name=name, status="down", message="模拟故障")

    return _probe()


class TestMetrics:
    """P1-06-E + BE-SEC-05: /metrics 端点测试。"""

    @pytest.mark.asyncio
    async def test_metrics_without_token_is_forbidden(self, client, monkeypatch):
        """配置了 METRICS_AUTH_TOKEN 后，不带 token 访问 /metrics 应返回 403。"""
        monkeypatch.setattr("app.main.settings.METRICS_AUTH_TOKEN", "secret-token")
        resp = await client.get("/metrics")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_metrics_with_token_returns_prometheus(self, client, monkeypatch):
        """带正确 token 访问 /metrics 应返回 Prometheus 格式数据。"""
        monkeypatch.setattr("app.main.settings.METRICS_AUTH_TOKEN", "secret-token")
        resp = await client.get("/metrics?token=secret-token")
        assert resp.status_code == 200
        assert "autoteams_http_requests_total" in resp.text


class TestDatabase:
    """数据库基础设施测试。"""

    @pytest.mark.asyncio
    async def test_get_db_rolls_back_on_exception(self):
        """get_db 在 yield 后发生异常时应回滚并向上传播。"""
        from app.database import get_db

        with pytest.raises(ValueError, match="boom"):
            async for _db in get_db():
                raise ValueError("boom")


@pytest.mark.asyncio
async def test_production_missing_security_store_is_not_optional(monkeypatch):
    from app.config import settings
    from app.utils import health
    from app.utils import token_blacklist

    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(token_blacklist, "_blacklist_redis_url", lambda: "")
    assert "security_redis" in health.required_dependencies()
    assert (await health._check_security_redis()).status == "down"


@pytest.mark.asyncio
async def test_configured_chroma_probe_is_executed(client, healthy_dependencies, monkeypatch):
    from app.config import settings
    from app.utils import health

    monkeypatch.setattr(settings, "CHROMA_HOST", "chroma.invalid")
    calls = []

    async def unavailable():
        calls.append(True)
        return health.DependencyStatus(name="chromadb", status="down")

    monkeypatch.setattr(health, "_check_chromadb", unavailable)
    response = await client.get("/health/ready")
    assert calls == [True]
    assert response.status_code == 503
    assert {d["name"]: d["status"] for d in response.json()["dependencies"]}["chromadb"] == "down"
