"""FastAPI 依赖注入：当前用户解析。

3.3.4: 从 utils/security.py 拆分而来，专注"请求级依赖注入"职责——
从 Cookie / Authorization 头解析 access token 并加载当前用户。

向后兼容：utils/security.py 仍作为 re-export shim 保留所有公开符号。
"""
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.models.user import User
from app.utils.token_blacklist import is_token_revoked
from app.utils.auth.tokens import decode_token

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # P1-1: 优先从 HttpOnly Cookie 读取 access token，无 Cookie 时回退到 Authorization 头
    # 保留 Authorization 回退便于 API 测试、第三方集成与渐进迁移
    token = request.cookies.get(settings.COOKIE_ACCESS_TOKEN_NAME)
    if not token and credentials:
        token = credentials.credentials
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # P0-S3: 校验 access token 是否已被吊销（登出/安全事件）
    if is_token_revoked(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 已被吊销",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭据",
        )
    # P2-3: 校验 token 类型，防止 refresh token 被用作 access token
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 token 类型",
        )
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        # 3.1.7: 统一返回 401 + 通用错误信息，避免与 token 无效时的 401 产生状态码差异
        # 被用于用户枚举攻击的信号。原实现返回 404 会暴露"token 有效但用户被删"的情况。
        # 仅在服务端日志记录 404 区分，不向客户端暴露。
        logger.warning(f"token 有效但用户不存在（user_id={user_id}），可能是已删除用户")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # P2-3: 校验 is_active，软禁用用户不能使用 API
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用",
        )
    # AUD-19: RLS 策略依赖 `app.current_enterprise_id`。认证成功后把租户绑定到
    # 当前上下文，后续每个事务在 begin 时以 SET LOCAL 写入（连接池复用不串租户）。
    await bind_authenticated_tenant(db, user)
    return user


async def bind_authenticated_tenant(db, user) -> None:
    """在**认证成功的可信入口**绑定租户与已认证主体（AUD-19）。

    只有刚验过凭据的地方才能调用：绑定租户等于授予该租户的全部数据可见性，
    因此绝不能由 `log_audit` 之类的下游函数根据"传进来的 user 对象"自动放宽。
    主体 GUC 走同一信任模型，匿名安全事件因此没有主体。

    用户查询已经在这个 Session 里开启了事务，begin 钩子当时只能写入空租户，
    所以这里必须立即更新当前事务。
    """
    from app.utils.db_tenant_context import (
        apply_tenant_context,
        bind_authenticated_user,
        bind_tenant_context,
    )

    bind_tenant_context(user.enterprise_id)
    bind_authenticated_user(user.id)
    if db.get_bind().dialect.name == "postgresql":
        await apply_tenant_context(db, user.enterprise_id)
