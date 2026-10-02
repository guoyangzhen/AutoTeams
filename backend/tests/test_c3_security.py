"""C3: 限流与核心配置 安全修复测试。

覆盖缺口：
- 5.3.1: 限流 Redis 存储强制（生产环境）
- T15: 生产环境 SQLite 拒绝启动
- L1: Server 响应头去除
- 依赖声明: C4 账户锁定 / C6 ClamAV 配置项
"""
import os
import subprocess
import sys
import textwrap

import pytest


class TestT15SqliteProductionReject:
    """T15: 生产环境（DEBUG=false）使用 SQLite 应拒绝启动。"""

    def test_production_sqlite_rejected_via_subprocess(self, tmp_path):
        """通过子进程验证：DEBUG=false + SQLite DATABASE_URL → RuntimeError。"""
        # 写入临时测试脚本（避免 -c 多行语法问题）
        script_content = textwrap.dedent(
            '''
            import sys
            sys.path.insert(0, ".")
            try:
                import app.config
                print("IMPORT_OK")
            except RuntimeError as e:
                print("RUNTIME_ERROR:", e)
                sys.exit(0)
            except Exception as e:
                print("OTHER_ERROR:", type(e).__name__, e)
                sys.exit(2)
            else:
                sys.exit(1)
            '''
        )
        script_file = tmp_path / "test_t15.py"
        script_file.write_text(script_content, encoding="utf-8")

        # 创建 backend/app 包结构（通过符号链接或复制 conftest 方式不现实，
        # 改为直接在 worktree 的 backend 目录下运行）
        backend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)))

        env = os.environ.copy()
        env["DEBUG"] = "false"
        env["USE_SQLITE"] = "true"
        env["JWT_SECRET_KEY"] = "test-secret-key-for-unit-tests-only-32chars-plus"
        env["COOKIE_SECURE"] = "true"
        env["CORS_ALLOWED_ORIGINS"] = "https://example.com"
        env["REDIS_URL"] = ""
        env["RATE_LIMIT_REQUIRE_REDIS"] = "false"
        # 避免其他启动检查干扰：本用例专测 T15 SQLite 拒绝，需把排在其前的
        # P1-4 公开注册检查、P2-6 metrics token 检查、桥接密钥检查等一并满足，
        # 防止子进程 env 继承 .env 中 REGISTRATION_ENABLED=true 时先行触发别的 RuntimeError。
        env["REGISTRATION_ENABLED"] = "false"
        env["METRICS_AUTH_TOKEN"] = "test-metrics-token"
        env["BRIDGE_INTERNAL_SECRET"] = "test-bridge-secret"
        env["ALLOW_INSECURE_REGISTRATION"] = ""
        env["LOOP_SCHEDULER_ENABLED"] = "false"
        # Keep the child output and parent decoder consistent on Windows too.
        env["PYTHONIOENCODING"] = "utf-8"

        result = subprocess.run(  # noqa: S603
            [sys.executable, str(script_file)],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=backend_dir,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # 如果 import 失败（缺少依赖等），跳过
        if "OTHER_ERROR" in stdout:
            pytest.skip(f"子进程导入失败，跳过: {stdout} | stderr: {stderr}")

        assert "RUNTIME_ERROR" in stdout, (
            f"期望 RuntimeError，实际 stdout={stdout!r}, stderr={stderr!r}, rc={result.returncode}"
        )
        assert "SQLite" in stdout

    def test_development_sqlite_allowed(self):
        """开发环境（DEBUG=true）使用 SQLite 不应报错。"""
        from app.config import settings
        # conftest 已设置 DEBUG=true
        assert settings.DEBUG is True
        assert settings is not None


class TestRateLimitRedisEnforcement:
    """5.3.1: 生产环境限流 Redis 存储强制。"""

    def test_config_has_rate_limit_require_redis(self):
        """config.py 应包含 RATE_LIMIT_REQUIRE_REDIS 配置项。"""
        from app.config import settings
        assert hasattr(settings, "RATE_LIMIT_REQUIRE_REDIS")
        assert settings.RATE_LIMIT_REQUIRE_REDIS is False

    def test_setup_limiter_rejects_when_production_redis_required_and_unavailable(
        self, monkeypatch
    ):
        """生产环境 + RATE_LIMIT_REQUIRE_REDIS=true + Redis 不可用 → RuntimeError。"""
        from app.utils.rate_limit import setup_limiter
        from app.config import settings

        monkeypatch.setattr(settings, "DEBUG", False)
        monkeypatch.setattr(settings, "RATE_LIMIT_REQUIRE_REDIS", True)
        monkeypatch.setattr(settings, "REDIS_URL", "")

        class FakeApp:
            def __init__(self):
                self.state = type("State", (), {})()

            def add_exception_handler(self, *a, **kw):
                pass

            def add_middleware(self, *a, **kw):
                pass

        with pytest.raises(RuntimeError, match="Redis"):
            setup_limiter(FakeApp())

    def test_setup_limiter_falls_back_in_development(self, monkeypatch):
        """开发环境 + Redis 不可用 → 不报错，回退内存存储。"""
        from app.utils.rate_limit import setup_limiter
        from app.config import settings

        monkeypatch.setattr(settings, "DEBUG", True)
        monkeypatch.setattr(settings, "RATE_LIMIT_REQUIRE_REDIS", True)
        monkeypatch.setattr(settings, "REDIS_URL", "")

        class FakeApp:
            def __init__(self):
                self.state = type("State", (), {})()

            def add_exception_handler(self, *a, **kw):
                pass

            def add_middleware(self, *a, **kw):
                pass

        # 不应抛异常
        setup_limiter(FakeApp())


class TestL1ServerHeaderRemoval:
    """L1: Server 响应头去除中间件。"""

    @pytest.mark.asyncio
    async def test_server_header_removed(self, client):
        """响应中不应包含 Server 头。"""
        resp = await client.get("/health")
        assert "server" not in {k.lower() for k in resp.headers.keys()}

    @pytest.mark.asyncio
    async def test_server_header_removed_on_error(self, client):
        """错误响应中也不应有 Server 头。"""
        resp = await client.get("/api/v1/nonexistent-endpoint")
        assert resp.status_code == 404
        assert "server" not in {k.lower() for k in resp.headers.keys()}


class TestC4DependencyConfig:
    """C4 依赖声明：账户锁定配置项。"""

    def test_account_lockout_threshold_default(self):
        """ACCOUNT_LOCKOUT_THRESHOLD 默认值应为 5。"""
        from app.config import settings
        assert settings.ACCOUNT_LOCKOUT_THRESHOLD == 5

    def test_account_lockout_duration_default(self):
        """ACCOUNT_LOCKOUT_DURATION_MINUTES 默认值应为 15。"""
        from app.config import settings
        assert settings.ACCOUNT_LOCKOUT_DURATION_MINUTES == 15

    def test_account_lockout_config_is_int(self):
        """账户锁定配置应为 int 类型。"""
        from app.config import settings
        assert isinstance(settings.ACCOUNT_LOCKOUT_THRESHOLD, int)
        assert isinstance(settings.ACCOUNT_LOCKOUT_DURATION_MINUTES, int)


class TestC6DependencyConfig:
    """C6 依赖声明：ClamAV 配置项。"""

    def test_clamd_host_default_empty(self):
        """CLAMD_HOST 默认应为空字符串（不启用 ClamAV）。"""
        from app.config import settings
        assert settings.CLAMD_HOST == ""

    def test_clamd_port_default(self):
        """CLAMD_PORT 默认应为 3310（ClamAV 标准端口）。"""
        from app.config import settings
        assert settings.CLAMD_PORT == 3310
