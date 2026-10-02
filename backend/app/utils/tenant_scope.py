"""企业 + 资源 + 角色 统一授权工具（AUD-03 / AUD-07 / AUD-30）。

背景
----
历史代码里各业务模块各自实现 `_verify_enterprise_admin` / `_get_agent_or_404`
之类的私有守卫，结果出现"父资源校验了、子资源没校验"的越权缺口：只要知道
资源 ID，任意已登录成员就能跨企业读写。

本模块提供唯一的企业边界实现，供各业务模块复用：

1. :func:`load_tenant_scoped` —— 任何资源读取都必须把 `enterprise_id` 写进
   WHERE 条件，"不存在"与"跨企业"统一返回 404（不泄露资源是否存在）。
2. :func:`assert_role` —— 企业边界之上的角色门禁（403）。
3. :func:`is_system_admin` —— 仅 `enterprise_id IS NULL AND role='admin'`
   的系统超管可以跨企业；公开注册产生的 `enterprise_id IS NULL` 普通成员
   **不是**超管（与 :func:`app.utils.rbac.require_admin` 的修复保持一致）。

所有业务模块都应通过这里加载资源，禁止再写"只按主键查询"的语句。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.utils.error_codes import ErrorCode

T = TypeVar("T")

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
VALID_ROLES = (ROLE_ADMIN, ROLE_MEMBER)


def is_system_admin(user: User) -> bool:
    """系统超管：未归属任何企业且角色为 admin。

    注意：`/auth/register` 会创建 `enterprise_id=None, role="member"` 的用户，
    因此"没有 enterprise_id"本身绝不等于超管。
    """
    return user.role == ROLE_ADMIN and user.enterprise_id is None


def is_enterprise_admin(user: User) -> bool:
    """企业管理员：已归属企业且角色为 admin。"""
    return user.role == ROLE_ADMIN and user.enterprise_id is not None


def is_admin(user: User) -> bool:
    """任意 admin（系统超管或企业管理员）。"""
    return user.role == ROLE_ADMIN


def assert_role(user: User, *allowed_roles: str) -> User:
    """要求当前用户角色在白名单内，否则 403。"""
    if user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.FORBIDDEN,
        )
    return user


def assert_enterprise_admin(user: User) -> User:
    """要求企业管理员（或系统超管），否则 403。"""
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.FORBIDDEN,
        )
    return user


def require_enterprise_bound(user: User) -> str:
    """要求用户已归属企业，返回 enterprise_id。

    未归属企业的用户（含公开注册用户）不得访问任何企业私有资源，
    否则他们会成为"无租户上下文"的万能读者。
    """
    if not user.enterprise_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.ENTERPRISE_ACCESS_DENIED,
        )
    return user.enterprise_id


def assert_enterprise(user: User, enterprise_id: Optional[str]) -> None:
    """校验资源所属企业与调用者一致（企业超管可跨企业）。"""
    if enterprise_id is None or enterprise_id == user.enterprise_id:
        return
    if is_system_admin(user):
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ErrorCode.NOT_FOUND,
    )


def not_found(detail: str = ErrorCode.NOT_FOUND) -> HTTPException:
    """统一的"不存在"异常。

    跨企业访问必须走这里而不是 403：403 会确认资源真实存在，
    构成跨租户资源枚举信道。
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ErrorCode.FORBIDDEN)


def tenant_scoped_stmt(
    model: Any,
    enterprise_column: Any,
    enterprise_id: Optional[str],
) -> Select:
    """构造"按企业过滤"的查询基线。"""
    if enterprise_id is None:
        # 无租户上下文的调用者不应看到任何行。
        return select(model).where(enterprise_column.is_(None))
    return select(model).where(enterprise_column == enterprise_id)


async def load_tenant_scoped(
    db: AsyncSession,
    model: Any,
    primary_key_column: Any,
    resource_id: str,
    enterprise_column: Any,
    user: User,
    *,
    not_found_detail: str = ErrorCode.NOT_FOUND,
    extra_conditions: Optional[Iterable[Any]] = None,
    for_update: bool = False,
) -> T:
    """按 `主键 + enterprise_id` 加载资源；不存在与跨企业统一 404。

    这是所有业务模块加载租户私有资源的唯一入口。任何只按主键查询的
    写法都会重新引入 AUD-03 类越权。

    :param extra_conditions: 追加的等值条件（例如 `MatrixTask.team_id == team_id`），
        用于把 URL 中的父资源与子资源绑定。
    """
    conditions = [primary_key_column == resource_id]
    if user.enterprise_id is not None:
        conditions.append(enterprise_column == user.enterprise_id)
    else:
        # 未归属企业的普通成员：只看得到 enterprise_id 为 NULL 的资源，
        # 且这些资源本身不承载任何企业数据。
        conditions.append(enterprise_column.is_(None))
    if extra_conditions:
        conditions.extend(extra_conditions)

    stmt = select(model).where(*conditions)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise not_found(not_found_detail)
    return row  # type: ignore[return-value]


async def load_tenant_scoped_first(
    db: AsyncSession,
    model: Any,
    enterprise_column: Any,
    user: User,
    *conditions: Any,
) -> Optional[T]:
    """在企业范围内取第一条匹配记录（用于"当前企业唯一"的配置类资源）。"""
    base: Sequence[Any]
    if user.enterprise_id is not None:
        base = [enterprise_column == user.enterprise_id, *conditions]
    else:
        base = [enterprise_column.is_(None), *conditions]
    result = await db.execute(select(model).where(*base))
    return result.scalars().first()  # type: ignore[return-value]


__all__ = [
    "ROLE_ADMIN",
    "ROLE_MEMBER",
    "VALID_ROLES",
    "assert_enterprise",
    "assert_enterprise_admin",
    "assert_role",
    "forbidden",
    "is_admin",
    "is_enterprise_admin",
    "is_system_admin",
    "load_tenant_scoped",
    "load_tenant_scoped_first",
    "not_found",
    "require_enterprise_bound",
    "tenant_scoped_stmt",
]
