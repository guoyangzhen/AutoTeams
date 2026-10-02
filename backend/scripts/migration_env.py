"""迁移专用进程的配置与口令处理（AUD-19）。

为什么需要它
------------
`alembic` 的 `migrations/env.py` 会 ``from app.database import ...``，而
``app.database`` 导入 ``app.config``；在 ``DEBUG=false``（生产默认）下，``config.py``
会因为缺少 **HTTP 服务才需要** 的配置而直接拒绝启动：

* ``JWT_SECRET_KEY`` 仍是内置不安全默认值
* ``COOKIE_SECURE=false``
* ``METRICS_AUTH_TOKEN`` / ``BRIDGE_INTERNAL_SECRET`` 为空

结果是：`migrate` 容器只给了数据库连接，却在 import 阶段就被配置校验挡下，迁移永远
跑不起来。静态 `docker compose config` 看不到这一点——它只做变量插值。

本模块在**独立进程内**补齐这些与 DDL 无关的取值，使迁移可以启动，同时：

* **不改** ``config.py`` / ``database.py`` 的任何既有安全守卫；
* **不设置** ``DEBUG=true``（那会让所有生产校验失效）；
* **不覆盖** 运维已经显式提供的值；
* 只处理**非 HTTP** 配置，绝不生成或伪造任何数据库凭据。

口令安全策略
------------
这些口令会被 compose 直接插值进 ``postgresql+asyncpg://user:口令@host/db``。
如果口令里出现 ``@``、``:``、``/``、``#``，连接串会被静默解析成**另一个**主机或
认证失败，表现为"服务起不来"而不是"配置写错"。因此这里只接受 URL 非保留字符，
并对不合规的取值**失败关闭**（不截断、不转义、不默默接受）。
"""
from __future__ import annotations

import os
import re

#: 迁移进程内补齐的"与 DDL 无关"配置项 → 迁移作用域占位值。
#:
#: 每一项都只服务于 config.py 的导入期校验：迁移不启动 HTTP 服务、不签发令牌、
#: 不桥接本地守护进程，因此这些取值不会进入任何真实请求路径。
MIGRATION_SCOPED_DEFAULTS: dict[str, str] = {
    "JWT_SECRET_KEY": "migration-scope-only-not-used-for-any-token",
    "COOKIE_SECURE": "true",
    "METRICS_AUTH_TOKEN": "migration-scope-only-metrics-disabled",
    "BRIDGE_INTERNAL_SECRET": "migration-scope-only-bridge-disabled",
}

#: 三个受限运行角色 → 迁移完成后需要设置口令的环境变量。
ROLE_PASSWORD_VARS: dict[str, str] = {
    "autoteams_app": "APP_DB_PASSWORD",
    "autoteams_worker": "WORKER_DB_PASSWORD",
    "autoteams_bootstrap": "BOOTSTRAP_DB_PASSWORD",
}

#: 管理员口令同样会被插值进 migrate 的连接串，因此一并校验。
# 这是**变量名**，不是口令本身。
ADMIN_PASSWORD_VAR = "POSTGRES_PASSWORD"  # noqa: S105

#: 示例文件里的占位值前缀。直接 ``cp .env.prod.db.example .env.prod.db`` 而忘了改，
#: 会让四个角色用同一个**公开已知**的口令上线 —— 这必须失败关闭而不是放行。
PLACEHOLDER_PREFIX = "CHANGE_ME"

MIN_PASSWORD_LENGTH = 32
MAX_PASSWORD_LENGTH = 128

#: 日志/异常中用于遮蔽口令的占位符。
REDACTION = "***redacted***"

#: URL 非保留字符：字母、数字、``-``、``_``、``.``、``~``。
#: 与 ``openssl rand -hex 32`` / ``secrets.token_urlsafe(32)`` 的输出完全吻合。
_URL_SAFE = re.compile(r"\A[A-Za-z0-9._~-]+\Z")


class MigrationConfigError(RuntimeError):
    """迁移进程的配置/口令不满足要求（失败关闭，绝不带着问题继续跑）。"""


def validate_urlsafe_password(variable: str, value: str | None) -> str:
    """校验口令可直接放进 URL；不合格立即抛错，不做截断或转义。

    只在错误信息里出现**变量名**，绝不回显口令本身。
    """
    if value is None or not value.strip():
        raise MigrationConfigError(
            f"{variable} 未配置。该口令会被拼进数据库连接串，必须显式提供，"
            "不接受默认秘密。"
        )
    if not _URL_SAFE.match(value):
        raise MigrationConfigError(
            f"{variable} 含 URL 保留字符（@ : / # ? 等），会导致连接串被静默解析成"
            "错误的主机或认证失败。请改用 URL 安全字符，例如："
            "openssl rand -hex 32 或 python -c \"import secrets;"
            "print(secrets.token_urlsafe(32))\"。"
        )
    if value.startswith(PLACEHOLDER_PREFIX):
        # 先判占位值：直接复制示例文件是最常见的失误，报错应当直指原因，
        # 而不是让用户去调长度。
        raise MigrationConfigError(
            f"{variable} 仍是示例文件里的占位值（{PLACEHOLDER_PREFIX}…）。"
            "直接复制示例文件会让生产使用公开已知的口令，请先生成真实随机口令。"
        )
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH:
        raise MigrationConfigError(
            f"{variable} 长度必须在 {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} 之间，"
            f"当前为 {len(value)}。"
        )
    return value


def redact_secrets(text: str, secrets) -> str:
    """把已知的口令从文本中抹掉。

    驱动异常可能带上执行的 SQL 与绑定参数；口令一旦进入日志就等于泄漏。
    """
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, REDACTION)
    return result


def _reject_reused_passwords(values: dict[str, str]) -> None:
    """四个身份必须使用互不相同的口令。

    共用口令会把数据库里分开的权限重新合并：例如 worker 与 app 共用一个口令，
    等于让 API 角色拿到跨租户领取/恢复能力；bootstrap 与 app 共用则让 API 拿到
    渠道引导能力。
    """
    by_value: dict[str, list[str]] = {}
    for name, value in values.items():
        by_value.setdefault(value, []).append(name)
    for shared in by_value.values():
        if len(shared) > 1:
            raise MigrationConfigError(
                "以下变量使用了**同一个**口令: "
                f"{', '.join(sorted(shared))}。每个角色必须使用不同口令，"
                "否则数据库中的权限隔离会被共享口令抵消。"
            )


def apply_migration_scoped_environment() -> list[str]:
    """补齐迁移进程需要的非 HTTP 配置，返回被补齐的变量名（不含取值）。

    - 不设置 ``DEBUG``：生产守卫必须保持生效。
    - 不覆盖已有取值：运维显式提供的配置优先。
    """
    applied: list[str] = []
    for name, value in MIGRATION_SCOPED_DEFAULTS.items():
        if os.environ.get(name, "").strip():
            continue
        os.environ[name] = value
        applied.append(name)
    if applied:
        # 只打印变量名。值是迁移作用域占位符，但仍不落到日志里。
        print(f"[migrate] 已补齐迁移作用域配置: {', '.join(sorted(applied))}")
    return applied


def collect_role_passwords() -> dict[str, str]:
    """读取并校验三个受限角色口令（以及管理员口令），返回 role -> password。

    在**任何**数据库连接建立之前调用：口令不合规时整个迁移直接失败，
    避免"应用服务用坏掉的连接串启动、排查困难"。
    """
    passwords: dict[str, str] = {}
    checked: dict[str, str] = {}
    for role, variable in ROLE_PASSWORD_VARS.items():
        value = validate_urlsafe_password(variable, os.environ.get(variable))
        passwords[role] = value
        checked[variable] = value
    # 管理员口令也进连接串，同样必须 URL 安全且不能与角色口令共用。
    admin = validate_urlsafe_password(ADMIN_PASSWORD_VAR, os.environ.get(ADMIN_PASSWORD_VAR))
    checked[ADMIN_PASSWORD_VAR] = admin
    # 四个身份共用口令会把数据库里分开的权限重新合并。
    _reject_reused_passwords(checked)
    return passwords


__all__ = [
    "ADMIN_PASSWORD_VAR",
    "MIGRATION_SCOPED_DEFAULTS",
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "MigrationConfigError",
    "PLACEHOLDER_PREFIX",
    "REDACTION",
    "ROLE_PASSWORD_VARS",
    "apply_migration_scoped_environment",
    "collect_role_passwords",
    "redact_secrets",
    "validate_urlsafe_password",
]
