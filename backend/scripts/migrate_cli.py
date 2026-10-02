"""迁移专用 CLI 入口（AUD-19）。

生产编排里，管理员数据库身份只应该出现在一次性 `migrate` 服务中。它做两件事：

1. ``alembic upgrade head``（DDL）；
2. 迁移创建了 ``autoteams_app`` / ``autoteams_worker`` / ``autoteams_bootstrap`` 三个
   LOGIN 角色但**不会**设置口令，因此升级成功后用**管理员**连接显式设置这三个角色
   的口令。

为什么要有独立的 CLI，而不是直接 `alembic upgrade head`：
``migrations/env.py`` 会 import ``app.database`` → ``app.config``，而生产默认
``DEBUG=false`` 下 config.py 会因为缺少 HTTP 服务才需要的配置（JWT 密钥、
Cookie 安全开关、metrics/bridge 令牌）直接拒绝启动。直接跑 alembic 会在 import
阶段就失败，迁移根本跑不起来。

本入口**不**放宽任何既有守卫：DEBUG 不被打开，config.py / database.py 一行未改，
迁移作用域占位值只在**本进程**内存在，且绝不覆盖运维显式提供的配置。

口令处理：三个角色口令由 compose 插值进环境变量，这里用参数化语句写库
（标识符走 ``psycopg2.sql.Identifier``，口令走绑定参数），日志与异常里**不出现**
任何口令。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.migration_env import (  # noqa: E402
    MigrationConfigError,
    apply_migration_scoped_environment,
    collect_role_passwords,
    redact_secrets,
)


def _admin_dsn() -> str:
    """管理员连接串（psycopg2 同步驱动）。

    compose 里给的是 ``postgresql+asyncpg://``，这里换成 psycopg2 —— 它是
    requirements.txt 里的生产依赖（alembic 迁移同样依赖它）。
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise MigrationConfigError("DATABASE_URL 未配置，迁移无法连接数据库。")
    return url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    # 显式指定 script_location：容器的工作目录不保证是 backend 根目录，
    # 依赖 cwd 会让迁移在某些编排下找不到版本脚本。
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(config, "head")


def _set_role_passwords(role_passwords: dict[str, str]) -> None:
    """用管理员连接为受限角色设置口令（**单个事务**）。

    标识符与口令都走 psycopg2 的安全构造/绑定，不做字符串拼接；只打印角色名。
    三个角色在同一个事务里提交：任何一个失败就整体回滚，避免出现"轮换了一半"的
    状态（例如 app 已换新口令而 worker 还是旧口令），那种状态下服务会以难以定位
    的认证错误失败。
    """
    import psycopg2
    from psycopg2 import sql as pgsql

    connection = psycopg2.connect(_admin_dsn())
    try:
        connection.autocommit = False
        try:
            with connection.cursor() as cur:
                for role, password in role_passwords.items():
                    cur.execute(
                        pgsql.SQL("ALTER ROLE {} PASSWORD {}").format(
                            pgsql.Identifier(role), pgsql.Literal(password)
                        )
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        for role in role_passwords:
            # 只打印角色名，不打印口令
            print(f"[migrate] 已设置受限角色口令: {role}")
    finally:
        connection.close()


def main() -> int:
    # 1) 补齐迁移进程需要的非 HTTP 配置（不设置 DEBUG，不覆盖已有取值）
    apply_migration_scoped_environment()

    # 2) 先校验口令再碰数据库：不合规立即失败，避免应用服务用坏连接串启动
    try:
        role_passwords = collect_role_passwords()
    except MigrationConfigError as exc:
        print(f"[migrate] 配置校验失败: {exc}", file=sys.stderr)
        return 2

    # 驱动异常可能带上执行的 SQL 与绑定参数；在输出前把已知口令全部遮蔽。
    secrets_to_hide = list(role_passwords.values()) + [
        os.environ.get("POSTGRES_PASSWORD", "")
    ]

    # 3) 执行 DDL
    try:
        _run_migrations()
    except Exception as exc:  # noqa: BLE001 - 迁移失败必须阻断启动并暴露原因
        print(
            f"[migrate] alembic upgrade head 失败: "
            f"{redact_secrets(exc, secrets_to_hide)}",
            file=sys.stderr,
        )
        return 1

    # 4) 为三个受限角色设置口令
    try:
        _set_role_passwords(role_passwords)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[migrate] 设置受限角色口令失败: "
            f"{redact_secrets(exc, secrets_to_hide)}",
            file=sys.stderr,
        )
        return 1

    print("[migrate] 迁移与角色口令设置完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
