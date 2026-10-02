"""PostgreSQL 行级安全（RLS）的租户上下文（AUD-19）。

现状问题
--------
迁移 `2026_07_24_0200` 为 `agents` / `agent_kpis` 建了
`USING (enterprise_id::text = current_setting('app.current_enterprise_id', true))`
策略，但**应用代码从未设置这个变量**，因此 `current_setting(..., true)` 恒为 NULL，
策略对所有行返回 false。同时 Compose 用 `POSTGRES_USER`（官方镜像创建的是
超级用户）同时做迁移账户与运行账户，超级用户直接绕过 RLS。

也就是说：RLS 既没有生效，也让运维误以为存在第二道租户隔离。

本模块提供缺失的那一半
---------------------
`app.current_enterprise_id` 通过 `SET LOCAL` 在**每个事务开始时**写入：

* `SET LOCAL` 的作用域是当前事务，事务结束自动清除 —— 连接池复用时不会串租户；
* 每个请求通过 `ContextVar` 绑定自己的企业，`get_current_user` 解析出用户后调用
  `bind_tenant_context()`；
* 后台 worker / 定时任务没有用户上下文时显式绑定"无租户"（空串），RLS 策略
  会拒绝所有行 —— 这是刻意的失败关闭，而不是"无租户即可读全部"；
* SQLite 不支持 RLS，本模块在 SQLite 上是空操作（但仍保留显式绑定，便于测试）。

启用方式见 `config.DATABASE_APP_ROLE` 与 `docs` 中的切换说明：在完成权限矩阵
验证之前，应用仍可用迁移账户运行，但启动时会打 CRITICAL 告警。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from enum import Enum
from typing import Iterator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

#: 当前请求/任务的企业 ID。None 表示"未绑定租户"。
_current_enterprise_id: ContextVar[Optional[str]] = ContextVar(
    "autoteams_current_enterprise_id", default=None
)

#: SQLAlchemy 自定义 GUC 名称（与迁移中的策略表达式一致）。
TENANT_GUC = "app.current_enterprise_id"

#: 供策略使用的"无租户"哨兵值。空串不会匹配任何 enterprise_id。
NO_TENANT = ""

#: 当前已认证的用户 ID。与租户 GUC 同一信任模型：只有刚验过凭据的入口才写它，
#: 匿名安全事件（登录失败/账户锁定）没有主体，保持空串。
AUTH_USER_GUC = "app.current_user_id"
NO_SUBJECT = ""
_current_user_id: ContextVar[Optional[str]] = ContextVar(
    "autoteams_current_user_id", default=None
)


def get_authenticated_user_id() -> Optional[str]:
    """返回当前上下文已认证的用户 ID（未认证时为 None）。"""
    return _current_user_id.get()


def bind_authenticated_user(user_id: Optional[str]) -> Token:
    """绑定已认证主体，返回可用于恢复的 Token。"""
    return _current_user_id.set(user_id or None)


def reset_authenticated_user(token: Token) -> None:
    """恢复上一次的主体上下文。"""
    _current_user_id.reset(token)


@contextmanager
def authenticated_user_scope(user_id: Optional[str]) -> Iterator[Optional[str]]:
    """以上下文管理器形式绑定已认证主体。"""
    token = bind_authenticated_user(user_id)
    try:
        yield user_id
    finally:
        reset_authenticated_user(token)



def get_current_enterprise_id() -> Optional[str]:
    """返回当前上下文绑定的企业 ID（未绑定时为 None）。"""
    return _current_enterprise_id.get()


def bind_tenant_context(enterprise_id: Optional[str]) -> Token:
    """把企业 ID 绑定到当前上下文，返回可用于恢复的 Token。"""
    return _current_enterprise_id.set(enterprise_id or None)


def reset_tenant_context(token: Token) -> None:
    """恢复上一次的企业上下文。"""
    _current_enterprise_id.reset(token)


@contextmanager
def tenant_scope(enterprise_id: Optional[str]) -> Iterator[Optional[str]]:
    """以上下文管理器形式绑定企业 ID。"""
    token = bind_tenant_context(enterprise_id)
    try:
        yield enterprise_id
    finally:
        reset_tenant_context(token)


async def apply_tenant_context(
    connection: AsyncConnection | AsyncSession,
    enterprise_id: Optional[str] = None,
) -> None:
    """在**当前事务**内写入租户上下文与已认证主体。

    使用 `SET LOCAL`：事务一结束就自动清除，连接归还池中时不会残留上一个租户的值。
    """
    value = enterprise_id if enterprise_id is not None else get_current_enterprise_id()
    if value is None:
        # 显式写空串：策略会拒绝所有行，而不是"未设置变量"时按策略默认值放行。
        payload = NO_TENANT
    else:
        payload = value
    await connection.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :value, true)"),
        {"value": str(payload)},
    )
    # 主体 GUC 用同样的写法落库：匿名事件写空串，受控审计入口据此要求
    # "p_user_id 必须等于当前已认证主体"。
    subject = get_authenticated_user_id()
    await connection.execute(
        text(f"SELECT set_config('{AUTH_USER_GUC}', :value, true)"),
        {"value": NO_SUBJECT if subject is None else str(subject)},
    )


class TenantResolver(str, Enum):
    """允许在绑定租户前执行的解析函数（迁移 d5e6f7a8b9c0）。

    只接受枚举成员而不是任意 SQL 字符串：这些语句是 ``SECURITY DEFINER``，
    拼出来的字符串一旦可被调用方影响，就等于把"任意 SQL"的口子留在引导路径上。
    每个函数都要求出示调用方持有的密钥（设备长期凭据摘要、设备令牌摘要、
    渠道回调密钥摘要），因此不是"凭标识换租户"。
    """

    RUNNER_CREDENTIAL = (
        "SELECT public.app_resolve_runner_credential_tenant"
        "(:device_id, :secret_hash)"
    )
    RUNNER_ACCESS = "SELECT public.app_resolve_runner_token_tenant(:token_hash)"
    CHANNEL_ACCOUNT = (
        "SELECT public.app_resolve_channel_tenant(:account_id, :secret_hash)"
    )


async def resolve_and_bind_tenant(
    connection: AsyncConnection | AsyncSession,
    resolver: TenantResolver,
    parameters: dict[str, str],
) -> Optional[str]:
    """用已认证的凭据解析出唯一租户，并把该租户绑定到当前事务。

    解析不出租户（凭据无效/已吊销/不属于任何活跃设备或渠道）时返回 ``None``，
    并把上下文显式写成"无租户"：事务继续保持失败关闭，而不是退回未绑定状态。
    调用方必须把 ``None`` 当作认证失败处理。
    """
    result = await connection.execute(text(resolver.value), parameters)
    enterprise_id = result.scalar_one_or_none()
    if enterprise_id is None:
        bind_tenant_context(None)
        await apply_tenant_context(connection)
        return None
    enterprise_id = str(enterprise_id)
    bind_tenant_context(enterprise_id)
    await apply_tenant_context(connection, enterprise_id)
    return enterprise_id

async def apply_tenant_context_on_begin(connection: AsyncConnection) -> None:
    """事务开始钩子（配合 SQLAlchemy `begin` 事件使用）。"""
    if connection.dialect.name == "postgresql":
        await apply_tenant_context(connection)


def rls_is_enforced_for_runtime_user() -> bool:
    """运行账户是否会被 RLS 约束。

    PostgreSQL 超级用户与带 `BYPASSRLS` 的角色会绕过策略。Compose 目前用
    `POSTGRES_USER`（超级用户）作为运行账户，因此 RLS 实际不生效。
    配置 `DATABASE_APP_ROLE` 指向一个非超级用户后，应用会在启动时校验并告警。
    """
    from app.config import settings

    return bool(getattr(settings, "DATABASE_APP_ROLE", "").strip())


__all__ = [
    "AUTH_USER_GUC",
    "NO_SUBJECT",
    "NO_TENANT",
    "TENANT_GUC",
    "TenantResolver",
    "apply_tenant_context",
    "apply_tenant_context_on_begin",
    "authenticated_user_scope",
    "bind_authenticated_user",
    "bind_tenant_context",
    "get_authenticated_user_id",
    "get_current_enterprise_id",
    "reset_authenticated_user",
    "reset_tenant_context",
    "resolve_and_bind_tenant",
    "rls_is_enforced_for_runtime_user",
    "tenant_scope",
]
