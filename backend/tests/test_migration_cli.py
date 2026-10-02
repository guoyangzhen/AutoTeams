"""迁移专用 CLI 的启动与口令策略（AUD-19）。

不连接数据库：这里只验证"迁移进程能不能在生产配置下启动"以及口令的安全策略。
真正的 DDL 与 `ALTER ROLE` 行为由隔离 PostgreSQL 用例和部署验收覆盖。

关键回归点是 **导入期配置守卫**：`migrations/env.py` 会 import `app.database`
→ `app.config`，而生产默认 `DEBUG=false`。如果没有迁移作用域配置，容器会在
import 阶段就因 `JWT_SECRET_KEY` 等**与 DDL 无关**的 HTTP 配置而拒绝启动 ——
静态 `docker compose config` 看不到这一点，因为它只做变量插值。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.migration_env import (  # noqa: E402
    MIGRATION_SCOPED_DEFAULTS,
    PLACEHOLDER_PREFIX,
    REDACTION,
    ROLE_PASSWORD_VARS,
    MigrationConfigError,
    apply_migration_scoped_environment,
    collect_role_passwords,
    redact_secrets,
    validate_urlsafe_password,
)

SAFE_PASSWORD = "a" * 40
ADMIN_URL = (
    "postgresql+asyncpg://autoteams:" + "b" * 40 + "@postgres:5432/autoteams"
)


#: 子进程里必须清掉的变量：它们模拟"migrate 容器只有数据库相关配置"。
_APP_ONLY_VARS = (
    "DEBUG",
    "JWT_SECRET_KEY",
    "COOKIE_SECURE",
    "METRICS_AUTH_TOKEN",
    "BRIDGE_INTERNAL_SECRET",
    "CORS_ALLOWED_ORIGINS",
    "FRONTEND_URL",
    "REGISTRATION_ENABLED",
    "DEMO_MODE_ENABLED",
    "DATABASE_APP_ROLE",
    "DATABASE_WORKER_URL",
    "DATABASE_WORKER_ROLE",
    "DATABASE_BOOTSTRAP_URL",
    "DATABASE_BOOTSTRAP_ROLE",
    *ROLE_PASSWORD_VARS.values(),
    "POSTGRES_PASSWORD",
)


def _subprocess_env(tmp_path, **extra):
    """构造"迁移容器"环境：保留操作系统必需变量，移除全部应用层配置。"""
    env = dict(os.environ)
    for name in _APP_ONLY_VARS:
        env.pop(name, None)
    env.update({
        "PYTHONPATH": str(BACKEND_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "LOG_DIR": str(tmp_path),
        "DATABASE_URL": ADMIN_URL,
        "USE_SQLITE": "false",
    })
    for name in ROLE_PASSWORD_VARS.values():
        env[name] = SAFE_PASSWORD
    env["POSTGRES_PASSWORD"] = SAFE_PASSWORD
    env.update(extra)
    return env


class TestPasswordPolicy:
    def test_accepts_url_safe_generated_secrets(self):
        for value in ("a" * 40, "A-b_c.d~e" * 5, "0123456789abcdef" * 2):
            assert validate_urlsafe_password("APP_DB_PASSWORD", value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "short",
            "a" * 31,
            "pass" + "w" * 40 + "!",          # !
            "pass" + "w" * 39 + "@host",       # @ 会切断连接串
            "pass" + "w" * 39 + ":5432",       # : 会被当成端口
            "pass" + "w" * 39 + "/db",         # / 会被当成路径
            "pass" + "w" * 39 + "#frag",      # # 会被当成片段
            "pass" + "w" * 39 + "?x=1",       # ?
            "a" * 200,                          # 过长
            "",
            "   ",
        ],
    )
    def test_rejects_anything_that_would_break_or_ambiguously_parse_the_url(self, value):
        with pytest.raises(MigrationConfigError):
            validate_urlsafe_password("APP_DB_PASSWORD", value)

    def test_rejects_missing_password(self):
        with pytest.raises(MigrationConfigError) as excinfo:
            validate_urlsafe_password("WORKER_DB_PASSWORD", None)
        assert "WORKER_DB_PASSWORD" in str(excinfo.value)

    def test_error_message_never_echoes_the_secret(self):
        secret = "leaked" + "!" * 40
        with pytest.raises(MigrationConfigError) as excinfo:
            validate_urlsafe_password("APP_DB_PASSWORD", secret)
        message = str(excinfo.value)
        assert "APP_DB_PASSWORD" in message
        assert "leaked" not in message
        assert "!" not in message

    def test_rejects_example_placeholder_values(self, monkeypatch):
        """直接复制示例文件会让生产使用公开已知的口令，必须失败关闭。"""
        for name in (*ROLE_PASSWORD_VARS.values(), "POSTGRES_PASSWORD"):
            monkeypatch.setenv(name, SAFE_PASSWORD)
        monkeypatch.setenv(
            "APP_DB_PASSWORD",
            PLACEHOLDER_PREFIX + "_APP_ROLE_PASSWORD_AT_LEAST_32_CHARS",
        )
        with pytest.raises(MigrationConfigError) as excinfo:
            collect_role_passwords()
        message = str(excinfo.value)
        assert "APP_DB_PASSWORD" in message
        assert PLACEHOLDER_PREFIX in message

    def test_rejects_a_password_shared_by_two_roles(self, monkeypatch):
        """共用口令会把数据库里分开的权限重新合并。"""
        shared = "s" * 44
        monkeypatch.setenv("APP_DB_PASSWORD", shared)
        monkeypatch.setenv("WORKER_DB_PASSWORD", shared)
        monkeypatch.setenv("BOOTSTRAP_DB_PASSWORD", "b" * 44)
        monkeypatch.setenv("POSTGRES_PASSWORD", "d" * 44)
        with pytest.raises(MigrationConfigError) as excinfo:
            collect_role_passwords()
        message = str(excinfo.value)
        assert "APP_DB_PASSWORD" in message and "WORKER_DB_PASSWORD" in message
        assert shared not in message

    def test_rejects_admin_password_shared_with_a_role(self, monkeypatch):
        for name in ROLE_PASSWORD_VARS.values():
            monkeypatch.setenv(name, SAFE_PASSWORD)
        monkeypatch.setenv("POSTGRES_PASSWORD", SAFE_PASSWORD)
        with pytest.raises(MigrationConfigError):
            collect_role_passwords()

    def test_accepts_four_distinct_passwords(self, monkeypatch):
        distinct = {
            "APP_DB_PASSWORD": "a" * 40,
            "WORKER_DB_PASSWORD": "b" * 40,
            "BOOTSTRAP_DB_PASSWORD": "c" * 40,
            "POSTGRES_PASSWORD": "d" * 40,
        }
        for name, value in distinct.items():
            monkeypatch.setenv(name, value)
        passwords = collect_role_passwords()
        assert sorted(passwords) == sorted(ROLE_PASSWORD_VARS)

    def test_redaction_removes_every_known_secret(self):
        first, second = "a" * 40, "b" * 40
        secrets = [first, second]
        message = f"psycopg2 error: ALTER ROLE ... PASSWORD '{first}' params=('{second}',)"
        redacted = redact_secrets(message, secrets)
        for secret in secrets:
            assert secret not in redacted
        assert REDACTION in redacted
        # 不该动的内容仍然保留，便于排障
        assert "ALTER ROLE" in redacted

    def test_collect_role_passwords_validates_all_three_and_admin(self, monkeypatch):
        distinct = ["a" * 40, "b" * 40, "c" * 40, "d" * 40]
        for name, value in zip(
            (*ROLE_PASSWORD_VARS.values(), "POSTGRES_PASSWORD"), distinct, strict=True
        ):
            monkeypatch.setenv(name, value)
        passwords = collect_role_passwords()
        assert set(passwords) == set(ROLE_PASSWORD_VARS)
        assert sorted(passwords.values()) == sorted(distinct[:3])

        monkeypatch.setenv("APP_DB_PASSWORD", "bad@password")
        with pytest.raises(MigrationConfigError) as excinfo:
            collect_role_passwords()
        assert "APP_DB_PASSWORD" in str(excinfo.value)


class TestMigrationScopedEnvironment:
    def test_fills_missing_non_http_config_without_touching_debug(self, monkeypatch):
        for name in MIGRATION_SCOPED_DEFAULTS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("DEBUG", raising=False)

        applied = apply_migration_scoped_environment()

        assert set(applied) == set(MIGRATION_SCOPED_DEFAULTS)
        for name, value in MIGRATION_SCOPED_DEFAULTS.items():
            assert os.environ[name] == value
        # 绝不能用 DEBUG=true 绕过生产守卫
        assert "DEBUG" not in os.environ or os.environ["DEBUG"] != "true"

    def test_never_overrides_operator_provided_values(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "operator-supplied-value")
        monkeypatch.delenv("METRICS_AUTH_TOKEN", raising=False)

        apply_migration_scoped_environment()

        assert os.environ["JWT_SECRET_KEY"] == "operator-supplied-value"
        assert os.environ["METRICS_AUTH_TOKEN"] == MIGRATION_SCOPED_DEFAULTS[
            "METRICS_AUTH_TOKEN"
        ]

    def test_does_not_define_a_default_database_password(self):
        """迁移作用域占位值绝不能包含任何数据库凭据。"""
        for name, value in MIGRATION_SCOPED_DEFAULTS.items():
            assert "PASSWORD" not in name
            assert "DATABASE" not in name
            assert "postgres://" not in value
            assert "postgresql" not in value


class TestProductionImport:
    """用真实子进程证明：只有数据库连接的迁移环境现在可以完成 import。"""

    def test_app_config_import_fails_without_migration_scope(self, tmp_path):
        """先固定住问题的存在：没有迁移作用域配置时，生产 import 会被守卫拦下。"""
        result = subprocess.run(
            [sys.executable, "-c", "import app.config; print('IMPORTED')"],
            cwd=str(BACKEND_ROOT),
            env=_subprocess_env(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert result.returncode != 0
        assert "IMPORTED" not in result.stdout
        assert "JWT_SECRET_KEY" in result.stderr

    def test_migration_cli_entry_imports_with_only_database_config(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from scripts.migration_env import apply_migration_scoped_environment;"
                    "apply_migration_scoped_environment();"
                    "import app.config, app.database;"
                    "print('DEBUG=' + str(app.config.settings.DEBUG))"
                ),
            ],
            cwd=str(BACKEND_ROOT),
            env=_subprocess_env(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert result.returncode == 0, result.stderr
        # 能 import，但 DEBUG 仍然是生产模式（守卫没有被绕过）
        assert "DEBUG=False" in result.stdout

    def test_migrate_cli_rejects_bad_password_before_touching_the_database(self, tmp_path):
        env = _subprocess_env(tmp_path, APP_DB_PASSWORD="bad@password")
        result = subprocess.run(
            [sys.executable, "-m", "scripts.migrate_cli"],
            cwd=str(BACKEND_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert result.returncode == 2
        assert "APP_DB_PASSWORD" in result.stderr
        # 口令本身绝不出现在输出里
        assert "bad@password" not in result.stderr + result.stdout

    def test_admin_dsn_converts_to_sync_driver(self, monkeypatch):
        from scripts.migrate_cli import _admin_dsn

        monkeypatch.setenv("DATABASE_URL", ADMIN_URL)
        assert _admin_dsn() == ADMIN_URL.replace("postgresql+asyncpg://", "postgresql://")

    def test_admin_dsn_requires_a_database_url(self, monkeypatch):
        from scripts.migrate_cli import _admin_dsn

        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(MigrationConfigError):
            _admin_dsn()


class TestRolePasswordWriteTransaction:
    """口令轮换必须是原子的：要么三个角色都换，要么都不换。"""

    class _FakeCursor:
        def __init__(self, owner):
            self._owner = owner

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement, params=None):
            self._owner.executed.append((str(statement), params))
            if self._owner.fail_on and self._owner.fail_on in str(statement):
                raise RuntimeError("simulated ALTER ROLE failure")

    class _FakeConnection:
        def __init__(self, fail_on=None):
            self.executed = []
            self.commits = 0
            self.rollbacks = 0
            self.closed = False
            self.autocommit = None
            self.fail_on = fail_on
            self._fail_on = fail_on

        def cursor(self):
            return TestRolePasswordWriteTransaction._FakeCursor(self)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    def _patch_connect(self, monkeypatch, connection):
        import psycopg2

        monkeypatch.setattr(psycopg2, "connect", lambda dsn: connection)
        monkeypatch.setenv("DATABASE_URL", ADMIN_URL)

    def test_all_roles_are_written_in_one_transaction(self, monkeypatch):
        from scripts.migrate_cli import _set_role_passwords

        connection = self._FakeConnection()
        self._patch_connect(monkeypatch, connection)

        _set_role_passwords({"autoteams_app": "a" * 40, "autoteams_worker": "b" * 40})

        assert connection.autocommit is False, "必须是显式事务，不能逐条自动提交"
        assert connection.commits == 1, "三个角色应在一个事务里提交"
        assert connection.rollbacks == 0
        assert connection.closed is True
        assert len(connection.executed) == 2

    def test_a_failure_rolls_back_the_whole_rotation(self, monkeypatch):
        from scripts.migrate_cli import _set_role_passwords

        connection = TestRolePasswordWriteTransaction._FakeConnection(
            fail_on="autoteams_worker"
        )
        self._patch_connect(monkeypatch, connection)

        with pytest.raises(RuntimeError, match="simulated"):
            _set_role_passwords({"autoteams_app": "a" * 40, "autoteams_worker": "b" * 40})

        assert connection.commits == 0, "失败时不得留下半轮换"
        assert connection.rollbacks == 1
        assert connection.closed is True


class TestFailureOutputRedaction:
    def test_migration_failure_does_not_echo_passwords(self, monkeypatch, capsys):
        """驱动异常可能带 SQL 与绑定参数；输出前必须遮蔽口令。"""
        from scripts import migrate_cli

        secret_app = "app" * 15
        secret_admin = "adm" * 15
        for name, value in (
            ("APP_DB_PASSWORD", secret_app),
            ("WORKER_DB_PASSWORD", "wrk" * 15),
            ("BOOTSTRAP_DB_PASSWORD", "bst" * 15),
            ("POSTGRES_PASSWORD", secret_admin),
        ):
            monkeypatch.setenv(name, value)

        def boom():
            raise RuntimeError(
                f"ALTER ROLE autoteams_app PASSWORD '{secret_app}' "
                f"failed for admin {secret_admin}"
            )

        monkeypatch.setattr(migrate_cli, "_run_migrations", boom)

        assert migrate_cli.main() == 1

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert secret_app not in combined
        assert secret_admin not in combined
        assert REDACTION in combined
        # 仍要保留可排障的信息
        assert "alembic upgrade head 失败" in combined

    def test_password_step_failure_does_not_echo_passwords(self, monkeypatch, capsys):
        from scripts import migrate_cli

        secret = "pw" * 20
        for name, value in (
            ("APP_DB_PASSWORD", secret),
            ("WORKER_DB_PASSWORD", "wrk" * 15),
            ("BOOTSTRAP_DB_PASSWORD", "bst" * 15),
            ("POSTGRES_PASSWORD", "adm" * 15),
        ):
            monkeypatch.setenv(name, value)

        def boom(_passwords):
            raise RuntimeError(f"password literal {secret} rejected")

        monkeypatch.setattr(migrate_cli, "_set_role_passwords", boom)

        assert migrate_cli.main() == 1

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert secret not in combined
        assert REDACTION in combined


class TestMigrationScriptLocation:
    def test_script_location_is_pinned_to_the_backend_root(self, monkeypatch):
        """容器 cwd 不保证是 backend 根目录；script_location 必须显式指定。"""
        from alembic import command
        from alembic.config import Config

        from scripts import migrate_cli

        recorded = {}

        def fake_upgrade(config, revision):
            recorded["script_location"] = config.get_main_option("script_location")
            recorded["revision"] = revision

        monkeypatch.setattr(command, "upgrade", fake_upgrade)
        migrate_cli._run_migrations()

        assert recorded["revision"] == "head"
        assert Path(recorded["script_location"]) == migrate_cli.BACKEND_ROOT / "migrations"
        assert Path(recorded["script_location"]).is_dir()
