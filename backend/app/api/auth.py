from datetime import datetime, timezone, timedelta
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.invitation import Invitation
from app.models.user import User
from app.models.enterprise import Enterprise
from app.schemas.user import (
    UserCreate,
    UserLogin,
    UserResponse,
    Token,
    UpdateProfileRequest,
    ChangePasswordRequest,
)
from app.utils.auth.password import verify_password, get_password_hash
from app.utils.auth.deps import bind_authenticated_tenant
from app.services.auth_service import (
    register,
    register_with_invite,
    login,
    refresh_tokens,
    logout,
    AccountLockedError,
)
from app.utils.security import (
    get_current_user,
    generate_csrf_token,
    compute_request_fingerprint,
    get_token_max_age,
)
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_auth, rate_limit_api
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


def _set_auth_cookies(response: Response, token: Token) -> None:
    """P1-1 + BE-SEC-01: 将 access / refresh token 写入 HttpOnly Cookie，
    同时下发非 HttpOnly 的 csrf_token cookie 用于 Double Submit Cookie 防护。

    3.4.7: cookie max_age 从 token 的实际 exp 声明反推，避免与 token 真实过期
    时间不同步（服务层可通过 expires_delta 覆盖默认过期时间，配置默认值与
    token 实际值可能不一致）。解析失败时回退到配置默认值，不阻塞登录流程。
    """
    common = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "domain": settings.COOKIE_DOMAIN,
        "path": "/",
    }
    response.set_cookie(
        settings.COOKIE_ACCESS_TOKEN_NAME,
        token.access_token,
        max_age=get_token_max_age(
            token.access_token,
            fallback=timedelta(hours=settings.JWT_EXPIRATION_HOURS),
        ),
        **common,
    )
    response.set_cookie(
        settings.COOKIE_REFRESH_TOKEN_NAME,
        token.refresh_token,
        max_age=get_token_max_age(
            token.refresh_token,
            fallback=timedelta(days=settings.JWT_REFRESH_EXPIRATION_DAYS),
        ),
        **common,
    )
    # BE-SEC-01: csrf_token 必须可被前端 JS 读取，因此不能设 HttpOnly
    # csrf_token 无对应 JWT，沿用 refresh token 的过期周期（与登录会话等长）
    response.set_cookie(
        "csrf_token",
        generate_csrf_token(),
        max_age=get_token_max_age(
            token.refresh_token,
            fallback=timedelta(days=settings.JWT_REFRESH_EXPIRATION_DAYS),
        ),
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path="/",
        httponly=False,
    )


def _clear_auth_cookies(response: Response) -> None:
    """P1-1 + BE-SEC-01: 清除认证 Cookie 与 csrf_token。"""
    common = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "domain": settings.COOKIE_DOMAIN,
        "path": "/",
    }
    response.delete_cookie(settings.COOKIE_ACCESS_TOKEN_NAME, **common)
    response.delete_cookie(settings.COOKIE_REFRESH_TOKEN_NAME, **common)
    response.delete_cookie(
        "csrf_token",
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path="/",
        httponly=False,
    )


class RefreshTokenRequest(BaseModel):
    """refresh token 请求体。

    3.1.3: 仅在 DEBUG 模式下保留 refresh_token 字段作为兼容回退（便于 API 测试）；
    生产环境（DEBUG=false）直接拒绝 body 提交的 refresh_token，强制走 HttpOnly Cookie，
    避免 XSS 攻击者窃取 token 后通过 body 提交绕过 SameSite 防护。
    """
    refresh_token: str = ""


def _resolve_refresh_token(request: Request, refresh_in: RefreshTokenRequest) -> str:
    """3.1.3: 解析 refresh token，生产环境仅信任 Cookie。

    - 生产环境（DEBUG=false）：仅从 HttpOnly Cookie 读取，body 提交一律拒绝
    - 开发/测试环境（DEBUG=true）：优先 Cookie，回退到 body（兼容旧客户端与 API 测试）
    """
    cookie_token = request.cookies.get(settings.COOKIE_REFRESH_TOKEN_NAME)
    if cookie_token:
        return cookie_token
    if settings.DEBUG and refresh_in.refresh_token:
        # DEBUG 模式保留 body 回退，便于无 Cookie 容器的 API 测试
        return refresh_in.refresh_token
    # 生产环境或 DEBUG 下未提供 body：统一返回缺失
    return ""


@router.post("/register", status_code=status.HTTP_201_CREATED)
@rate_limit_auth()
async def register_user(
    request: Request,
    response: Response,
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    """注册新用户（无企业归属）。

    P0-02-A/B: 此端点仅创建无企业的用户（超级管理员/系统账号）。
    普通企业用户必须通过 /auth/register-with-invite 走邀请流程。
    BE-SEC-08: 生产环境可通过 REGISTRATION_ENABLED=false 关闭公开注册。
    """
    # BE-SEC-08: 检查公开注册开关
    if not settings.REGISTRATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.REGISTRATION_DISABLED,
        )
    try:
        fingerprint = compute_request_fingerprint(request)
        user, token = await register(db, user_in, fingerprint=fingerprint)
        _set_auth_cookies(response, token)
        # AUD-19：审计写入按租户策略放行，认证成功后立即绑定（本用户可能尚无企业，
        # 此时走"无租户主体"的受控审计通道，而不是给日志开一个宽松口子）。
        await bind_authenticated_tenant(db, user)
        await log_audit(db, user, "register", "user", user.id, request=request)
        await db.commit()
        return success_response({
            "user": UserResponse.model_validate(user).model_dump(),
            # P1-1: token 已写入 HttpOnly Cookie，响应体中不再返回
        })
    except ValueError as e:
        logger.warning(f"注册失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorCode.REGISTRATION_FAILED) from e


@router.post("/login")
@rate_limit_auth()
async def login_user(
    request: Request,
    response: Response,
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    try:
        fingerprint = compute_request_fingerprint(request)
        user, token = await login(db, credentials, fingerprint=fingerprint)
        _set_auth_cookies(response, token)
        # AUD-19：登录成功同样要在写审计前绑定租户。
        await bind_authenticated_tenant(db, user)
        await log_audit(db, user, "login", "user", user.id, request=request)
        await db.commit()
        return success_response({
            "user": UserResponse.model_validate(user).model_dump(),
            # P1-1: token 已写入 HttpOnly Cookie，响应体中不再返回
        })
    except AccountLockedError:
        # M9: 账户锁定 — 返回 403 + ACCOUNT_LOCKED，不暴露具体剩余时间
        logger.warning(f"M9 登录拒绝（账户锁定）：email={credentials.email}")
        await log_audit(
            db, None, "login_locked", "user", credentials.email,
            request=request,
            details={"reason": "account_locked"},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.ACCOUNT_LOCKED,
        ) from None
    except ValueError as e:
        logger.warning(f"登录失败: {e}")
        # P1-16: 记录登录失败审计日志，resource_id 使用邮箱便于安全分析
        await log_audit(
            db, None, "login_failed", "user", credentials.email,
            request=request,
            details={"reason": str(e)},
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ErrorCode.INVALID_CREDENTIALS) from e


@router.post("/refresh")
@rate_limit_auth()
async def refresh_token_endpoint(
    request: Request,
    response: Response,
    refresh_in: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """用 refresh token 获取新的 access + refresh token 对。

    P1-1: refresh token 优先从 HttpOnly Cookie 读取。
    3.1.3: 生产环境（DEBUG=false）拒绝 body 提交的 refresh_token，仅信任 Cookie，
    避免 XSS 攻击者窃取 token 后通过 body 绕过 SameSite 防护。
    客户端在 access token 过期后调用此端点，无需用户重新登录。
    """
    refresh_token = _resolve_refresh_token(request, refresh_in)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.MISSING_REFRESH_TOKEN,
        )

    try:
        fingerprint = compute_request_fingerprint(request)
        user, token = await refresh_tokens(db, refresh_token, fingerprint=fingerprint)
        _set_auth_cookies(response, token)
        await bind_authenticated_tenant(db, user)
        await log_audit(db, user, "refresh_token", "user", user.id, request=request)
        await db.commit()
        return success_response({
            # P1-1: token 已写入 HttpOnly Cookie，响应体中不再返回
        })
    except ValueError as e:
        logger.warning(f"刷新 token 失败: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ErrorCode.INVALID_REFRESH_TOKEN) from e


@router.post("/logout")
@rate_limit_api()
async def logout_user(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """登出：清除 refresh_token_hash，使 refresh token 失效。

    P0-S3: 将当前 access token 一并加入黑名单，使其立即失效。
    """
    access_token = request.cookies.get(settings.COOKIE_ACCESS_TOKEN_NAME)
    await logout(db, current_user, access_token=access_token)
    await log_audit(db, current_user, "logout", "user", current_user.id, request=request)
    await db.commit()
    _clear_auth_cookies(response)
    return success_response({"message": "已登出"})


@router.get("/me")
@rate_limit_api()
async def get_me(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return success_response(UserResponse.model_validate(current_user).model_dump())


@router.put("/me")
@rate_limit_api()
async def update_me(
    request: Request,
    user_in: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A1：更新当前用户资料（姓名）。

    - 仅允许当前登录用户修改自己的资料（get_current_user 保证）。
    - 更新后返回最新的 UserResponse，供前端同步。
    """
    current_user.name = user_in.name
    await log_audit(
        db, current_user, "update_profile", "user", current_user.id,
        request=request, details={"name": user_in.name},
    )
    await db.commit()
    await db.refresh(current_user)
    return success_response(UserResponse.model_validate(current_user).model_dump())


@router.post("/change-password")
@rate_limit_auth()
async def change_password(
    request: Request,
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A1：修改当前用户密码。

    - 校验当前密码是否正确（verify_password），防止越权改密。
    - 新密码长度校验在 ChangePasswordRequest 中完成（≥8 且 ≤72 字节）。
    - 更新后刷新 access token 的 fingerprint 相关会话（若采用 fingerprint 绑定，
      此处仅更新密码哈希，token 仍有效由 refresh 流程处理）。
    """
    if not verify_password(data.current_password, current_user.password_hash):
        await log_audit(
            db, current_user, "change_password_failed", "user", current_user.id,
            request=request, details={"reason": "wrong_current_password"},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.INVALID_CREDENTIALS,
        )

    current_user.password_hash = get_password_hash(data.new_password)
    await log_audit(
        db, current_user, "change_password", "user", current_user.id,
        request=request,
    )
    await db.commit()
    return success_response({"message": "密码已更新"})


@router.get("/invite/{token}")
@rate_limit_api()
async def validate_invite(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """P0-02-C: 通过 Invitation 表校验邀请 token。

    同时校验：
    - 邀请状态为 pending
    - 未过期
    - 邀请次数未用完
    - 所属企业未软删除
    """
    invitation = await _load_and_validate_invitation(db, token, for_use=False)
    enterprise_result = await db.execute(
        select(Enterprise).where(Enterprise.id == invitation.enterprise_id)
    )
    enterprise = enterprise_result.scalar_one_or_none()
    if not enterprise or not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.INVITE_ENTERPRISE_NOT_FOUND)

    return success_response({
        "enterprise_id": enterprise.id,
        "enterprise_name": enterprise.name,
        "email_required": invitation.email is not None,
        "remaining_uses": max(
            0, (enterprise.invite_max_uses or 0) - (enterprise.invite_used_count or 0)
        ),
    })


@router.post("/register-with-invite", status_code=status.HTTP_201_CREATED)
@rate_limit_auth()
async def register_with_invite_endpoint(
    request: Request,
    response: Response,
    user_in: UserCreate,
    invite_token: str,
    db: AsyncSession = Depends(get_db),
):
    """P0-02-C: 通过邀请 token 注册用户。

    校验流程：
    1. 查 Invitation 表（不再查 Enterprise.invite_token）
    2. 邀请 status="pending"
    3. 未过期（expires_at > now）
    4. 邀请次数未用完（invite_used_count < invite_max_uses）
    5. 邮箱匹配（若 Invitation.email 已指定）
    6. 调用 register_with_invite 服务（原子化完成注册 + 计数递增 + 状态置位）
    """
    invitation = await _load_and_validate_invitation(db, invite_token, for_use=True)

    # P0-02-C: 邮箱匹配校验
    if invitation.email and invitation.email != user_in.email:
        raise HTTPException(
            status_code=403,
            detail=ErrorCode.INVITE_EMAIL_MISMATCH,
        )

    # P0-02-C: 邀请次数校验
    enterprise_result = await db.execute(
        select(Enterprise).where(Enterprise.id == invitation.enterprise_id)
    )
    enterprise = enterprise_result.scalar_one_or_none()
    if not enterprise or not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.INVITE_ENTERPRISE_NOT_FOUND)
    if (enterprise.invite_used_count or 0) >= (enterprise.invite_max_uses or 0):
        raise HTTPException(status_code=400, detail=ErrorCode.INVITE_USED_UP)

    try:
        fingerprint = compute_request_fingerprint(request)
        user, token = await register_with_invite(db, user_in, invitation, fingerprint=fingerprint)
        _set_auth_cookies(response, token)
        # 邀请注册的用户直接带企业归属：写审计前必须先绑定该租户。
        await bind_authenticated_tenant(db, user)
        await log_audit(
            db, user, "register_with_invite", "user", user.id,
            request=request,
            details={"invitation_id": invitation.id, "enterprise_id": invitation.enterprise_id},
        )
        await db.commit()
        return success_response({
            "user": UserResponse.model_validate(user).model_dump(),
            # P1-1: token 已写入 HttpOnly Cookie，响应体中不再返回
        })
    except ValueError as e:
        logger.warning(f"邀请注册失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ErrorCode.INVITE_REGISTRATION_FAILED) from e


async def _load_and_validate_invitation(
    db: AsyncSession, token: str, for_use: bool
) -> Invitation:
    """加载并校验邀请记录。

    for_use=True 时表示即将使用此邀请注册（更严格的校验）。
    返回 Invitation 实例，校验失败抛 HTTPException。
    """
    result = await db.execute(
        select(Invitation).where(Invitation.token == token)
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        raise HTTPException(status_code=404, detail=ErrorCode.INVITE_NOT_FOUND)

    if invitation.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.INVITE_USED_OR_CANCELLED,
        )

    if invitation.expires_at:
        exp = invitation.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail=ErrorCode.INVITE_EXPIRED)

    return invitation
