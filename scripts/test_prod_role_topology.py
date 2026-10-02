"""Production Compose must not hand the admin database credential to runtime services.

AUD-19：迁移、普通运行、队列领取、渠道引导是四种不同能力，对应四种不同的数据库
身份。这个文件用**静态拓扑检查**把边界钉死，避免"图省事把管理员 URL 复制到四个
服务"这种回退悄悄发生。

被检查的边界：

1. 只有一次性 ``migrate`` 服务（以及 ``postgres`` / ``backup`` 这两个天然需要管理员
   身份的容器）可以使用管理员口令；
2. ``backend`` 用 ``autoteams_app``，**没有**任何跨租户队列能力；
3. 三个队列 Worker 用 ``autoteams_worker``，并且**不**拿到 bootstrap 口令；
4. ``backend`` 显式配置 bootstrap 引导连接（渠道验签材料读取），不外发；
5. 四个口令都必须显式配置（``:?`` 失败关闭），没有任何默认生产秘密；
6. 生产编排里不允许出现 ``DEBUG=true``；
7. ``backend`` 不得再用镜像里"先迁移再启动"的入口脚本。

这些是**配置层**的断言，不是数据库权限的证据；真实权限行为由 backend/tests 下
的 PostgreSQL 用例覆盖。
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.prod.yml"

WORKERS = ("compilation-worker", "processing-worker", "agent-build-worker")

#: 允许接触数据库管理员口令的服务。
#: migrate  —— 一次性 DDL；postgres —— 集群初始化；backup —— pg_dump 必然需要。
ADMIN_ALLOWED = {"migrate", "postgres", "backup"}

#: 四个必须显式配置、且没有默认值的口令变量。
REQUIRED_SECRETS = (
    "POSTGRES_PASSWORD",
    "APP_DB_PASSWORD",
    "WORKER_DB_PASSWORD",
    "BOOTSTRAP_DB_PASSWORD",
)


def _db_user(url: str) -> str:
    """从连接串里取数据库用户名（口令在它之后，不参与断言）。"""
    return url.split("://", 1)[1].split(":", 1)[0]


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _env_map(service: dict) -> dict:
    """把 environment 列表/字典统一成 {NAME: value}。"""
    env = service.get("environment", {})
    if isinstance(env, dict):
        return dict(env)
    result = {}
    for item in env:
        name, _, value = item.partition("=")
        result[name] = value
    return result


class AdminCredentialScopeTests(unittest.TestCase):
    def test_only_migrate_and_admin_natives_reference_admin_password(self):
        services = _services()
        offenders = []
        for name, service in services.items():
            if name in ADMIN_ALLOWED:
                continue
            if "POSTGRES_PASSWORD" in yaml.dump(service):
                offenders.append(name)
        self.assertEqual(offenders, [], f"运行服务不得接触管理员口令: {offenders}")

    def test_migrate_is_the_only_service_running_ddl_with_admin_url(self):
        migrate = _services()["migrate"]
        env = _env_map(migrate)
        self.assertIn("POSTGRES_PASSWORD", env["DATABASE_URL"])
        # 管理员身份来自 ${POSTGRES_USER}（默认 autoteams），不是固定字符串，
        # 因此按前缀断言，不去解析模板里的冒号。
        self.assertTrue(
            env["DATABASE_URL"].startswith("postgresql+asyncpg://${POSTGRES_USER"),
            env["DATABASE_URL"],
        )
        # 入口必须是迁移专用 CLI，而不是裸 alembic：alembic 会 import
        # app.config，在 DEBUG=false 下因缺少 HTTP 配置而直接拒绝启动。
        self.assertIn("scripts.migrate_cli", " ".join(migrate["command"]))
        self.assertNotIn("alembic", " ".join(migrate["command"]))
        # 一次性任务：不能被编排成常驻或自动重启
        self.assertEqual(str(migrate.get("restart", "")), "no")
        # 不注入应用层密钥
        self.assertIsNone(migrate.get("env_file"))
        # read_only 根文件系统下日志目录必须落在可写的 tmpfs 上
        self.assertEqual(env["LOG_DIR"], "/tmp/logs")
        self.assertIn("/tmp", migrate["tmpfs"])

    def test_migrate_receives_the_three_role_passwords_and_nothing_else(self):
        """必要且最小的例外：迁移要用管理员连接为三个受限角色设置口令。

        因此 migrate 额外拿到这三个口令 —— 但**仅此而已**，不得顺带拿到应用层
        密钥，也不得出现在任何其它服务里。
        """
        env = _env_map(_services()["migrate"])
        for name in ("APP_DB_PASSWORD", "WORKER_DB_PASSWORD", "BOOTSTRAP_DB_PASSWORD"):
            with self.subTest(variable=name):
                self.assertIn(name, env)
        for forbidden in ("JWT_SECRET_KEY", "METRICS_AUTH_TOKEN", "BRIDGE_INTERNAL_SECRET"):
            with self.subTest(variable=forbidden):
                self.assertNotIn(forbidden, env)

    def test_runtime_services_wait_for_a_successful_migration(self):
        services = _services()
        for name in ("backend", *WORKERS):
            with self.subTest(service=name):
                depends = services[name]["depends_on"]
                self.assertEqual(
                    depends["migrate"]["condition"], "service_completed_successfully"
                )


class RoleSeparationTests(unittest.TestCase):
    def test_api_uses_app_role_and_has_no_queue_capability(self):
        env = _env_map(_services()["backend"])
        self.assertEqual(_db_user(env["DATABASE_URL"]), "autoteams_app")
        self.assertEqual(env["DATABASE_APP_ROLE"], "autoteams_app")
        # 队列连接串/角色不属于 API
        for forbidden in ("DATABASE_WORKER_URL", "DATABASE_WORKER_ROLE"):
            self.assertNotIn(forbidden, env)

    def test_api_declares_bootstrap_connection_for_channel_material(self):
        env = _env_map(_services()["backend"])
        self.assertEqual(_db_user(env["DATABASE_BOOTSTRAP_URL"]), "autoteams_bootstrap")
        self.assertEqual(env["DATABASE_BOOTSTRAP_ROLE"], "autoteams_bootstrap")

    def test_role_credentials_reach_only_their_owner_and_migrate(self):
        """受限角色口令只出现在"用它的那一个服务"和 migrate（设置口令的必要例外）。

        尤其是 bootstrap 通道：它是跨租户能力，绝不能落到任何 worker、前端或
        协作服务手里。
        """
        services = _services()
        owner_of = {
            "APP_DB_PASSWORD": "backend",
            "BOOTSTRAP_DB_PASSWORD": "backend",
            "WORKER_DB_PASSWORD": None,  # 三个 worker
        }
        for variable, owner in owner_of.items():
            for name, service in services.items():
                if name in (owner, "migrate"):
                    continue
                if variable == "WORKER_DB_PASSWORD" and name in WORKERS:
                    continue
                with self.subTest(variable=variable, service=name):
                    self.assertNotIn(variable, yaml.dump(service))

    def test_workers_use_queue_role_and_never_the_app_or_bootstrap_url(self):
        services = _services()
        for name in WORKERS:
            with self.subTest(worker=name):
                env = _env_map(services[name])
                self.assertEqual(_db_user(env["DATABASE_URL"]), "autoteams_worker")
                self.assertEqual(_db_user(env["DATABASE_WORKER_URL"]), "autoteams_worker")
                self.assertEqual(env["DATABASE_WORKER_ROLE"], "autoteams_worker")
                # 队列进程内的默认引擎就是这个角色，生产连接守卫按它核验身份
                self.assertEqual(env["DATABASE_APP_ROLE"], "autoteams_worker")
                self.assertNotIn("DATABASE_BOOTSTRAP_URL", env)
                self.assertNotIn("DATABASE_BOOTSTRAP_ROLE", env)

    def test_role_names_are_fixed_not_templated(self):
        """角色名由迁移创建；允许用环境变量改写会让配置与数据库实际角色脱节。"""
        for name, service in _services().items():
            for key, value in _env_map(service).items():
                if key in (
                    "DATABASE_APP_ROLE",
                    "DATABASE_WORKER_ROLE",
                    "DATABASE_BOOTSTRAP_ROLE",
                ):
                    with self.subTest(service=name, key=key):
                        self.assertNotIn("${", value)


class SecretHygieneTests(unittest.TestCase):
    def test_migration_receives_every_password_required_by_its_cli(self):
        env = _env_map(_services()["migrate"])
        for variable in REQUIRED_SECRETS:
            with self.subTest(variable=variable):
                self.assertEqual(env[variable], f"${{{variable}:?{variable} required}}")
        self.assertEqual(env["DEBUG"], "false")

    def test_all_four_secrets_are_required_without_defaults(self):
        text = COMPOSE.read_text(encoding="utf-8")
        for secret in REQUIRED_SECRETS:
            with self.subTest(secret=secret):
                self.assertIn(f"${{{secret}:?", text, f"{secret} 必须用 :? 失败关闭")

    def test_app_env_file_carries_no_database_passwords(self):
        """`.env.prod` 会被写进 backend/worker 容器，因此不得含任何数据库口令。"""
        example = (ROOT / ".env.prod.example").read_text(encoding="utf-8")
        for secret in REQUIRED_SECRETS:
            with self.subTest(secret=secret):
                self.assertNotIn(f"\n{secret}=", example)

    def test_db_env_example_documents_every_credential(self):
        example = (ROOT / ".env.prod.db.example").read_text(encoding="utf-8")
        for secret in REQUIRED_SECRETS:
            with self.subTest(secret=secret):
                self.assertIn(f"{secret}=", example)

    def test_production_never_enables_debug(self):
        services = _services()
        for name, service in services.items():
            env = _env_map(service)
            with self.subTest(service=name):
                self.assertNotIn("DEBUG=true", str(env.get("DEBUG", "")))
        for name in ("backend", *WORKERS):
            with self.subTest(service=name):
                self.assertEqual(_env_map(services[name])["DEBUG"], "false")

    def test_backend_does_not_use_the_migrating_entrypoint(self):
        """镜像默认 CMD 是 migrate_and_start.sh；API 必须显式覆盖，否则又自己迁移。"""
        backend = _services()["backend"]
        command = " ".join(backend["command"])
        self.assertNotIn("migrate_and_start.sh", command)
        self.assertIn("uvicorn", command)
        self.assertIn("app.main:app", command)


if __name__ == "__main__":
    unittest.main()
