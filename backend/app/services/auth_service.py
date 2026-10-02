import logging
import time
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.utils.branding import LEGACY_STATE_NAMESPACE
from app.utils.redis_constants import REDIS_SOCKET_CONNECT_TIMEOUT
from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.invitation import Invitation
from app.schemas.user import UserCreate, UserLogin, Token
from app.utils.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
    hash_token,
    verify_token_hash,
    decode_token,
)
from app.utils.metrics import errors_total
from app.utils.token_blacklist import revoke_access_token
from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# M9: 账户锁定 / 暴力破解检测
#
# 使用 Redis（生产环境多实例共享）+ 进程内字典（开发/测试回退）双层存储。
# 锁定状态以邮箱为键（即使用户不存在也记录失败次数，防止通过登录接口枚举用户）。
# ---------------------------------------------------------------------------

class AccountLockedError(Exception):
    """M9: 账户因连续登录失败被临时锁定。"""

    def __init__(self, email: str, unlock_at: float):
        self.email = email
        self.unlock_at = unlock_at
        remaining = max(0, int(unlock_at - time.time()))
        super().__init__(
            f"账户已锁定，请 {remaining // 60 + 1} 分钟后再试"
        )


# 开发/测试回退：进程内锁定状态（仅当 Redis 不可用时使用）
# _attempts: email -> 失败次数
# _locked_until: email -> 解锁时间戳（Unix 秒）
_in_memory_attempts: dict[str, int] = {}
_in_memory_locked_until: dict[str, float] = {}


def _get_lockout_redis():
    """懒加载 Redis 连接；与 token_blacklist 复用相同的 REDIS_URL 配置。

    BE-SEC-10: 生产环境（DEBUG=false）强制要求 Redis，不可用时拒绝启动。
    连接失败或 redis 包未安装时返回 None（开发环境回退到进程内字典）。
    """
    try:
        import redis
    except ModuleNotFoundError:
        if not settings.DEBUG:
            raise RuntimeError(
                "拒绝启动：生产环境账户锁定需要 redis 包，但未安装。"
                "请运行: pip install redis"
            ) from None
        return None

    if not settings.REDIS_URL:
        if not settings.DEBUG:
            raise RuntimeError(
                "拒绝启动：生产环境账户锁定需要 REDIS_URL，但未配置。"
            )
        return None

    try:
        client = redis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT
        )
        client.ping()
        return client
    except Exception as e:
        if not settings.DEBUG:
            raise RuntimeError(
                f"拒绝启动：生产环境 Redis 连接失败，账户锁定不可降级: {e}"
            ) from e
        logger.warning(f"账户锁定 Redis 连接失败，回退到内存存储: {e}")
        return None


def _attempts_key(email: str) -> str:
    return f"{LEGACY_STATE_NAMESPACE}:login_attempts:{email}"


def _locked_key(email: str) -> str:
    return f"{LEGACY_STATE_NAMESPACE}:login_locked:{email}"


def is_account_locked(email: str) -> bool:
    """M9: 检查账户是否处于锁定状态。"""
    email = (email or "").lower().strip()
    if not email:
        return False

    client = _get_lockout_redis()
    if client:
        try:
            return bool(client.exists(_locked_key(email)))
        except Exception as e:
            logger.warning(f"Redis 读取锁定状态失败，回退内存: {e}")

    # 内存回退：清理已过期的锁定
    now = time.time()
    expired = [k for k, v in _in_memory_locked_until.items() if v <= now]
    for k in expired:
        _in_memory_locked_until.pop(k, None)
        _in_memory_attempts.pop(k, None)
    return email in _in_memory_locked_until


def record_failed_login(email: str) -> int:
    """M9: 记录一次失败登录，返回当前失败次数。

    达到阈值时自动设置锁定标记。
    """
    email = (email or "").lower().strip()
    if not email:
        return 0

    threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD
    duration = settings.ACCOUNT_LOCKOUT_DURATION_MINUTES
    client = _get_lockout_redis()

    if client:
        try:
            pipe = client.pipeline()
            pipe.incr(_attempts_key(email))
            pipe.expire(_attempts_key(email), duration * 120)  # 2h 窗口
            attempts_raw, _ = pipe.execute()
            attempts = int(attempts_raw or 0)
            if attempts >= threshold:
                client.setex(_locked_key(email), duration * 60, "1")
                logger.warning(
                    f"M9 账户锁定：email={email}，失败次数={attempts}，"
                    f"锁定 {duration} 分钟"
                )
            return attempts
        except Exception as e:
            logger.warning(f"Redis 写入失败次数失败，回退内存: {e}")

    # 内存回退
    attempts = _in_memory_attempts.get(email, 0) + 1
    _in_memory_attempts[email] = attempts
    if attempts >= threshold:
        _in_memory_locked_until[email] = time.time() + duration * 60
        logger.warning(
            f"M9 账户锁定（内存）：email={email}，失败次数={attempts}，"
            f"锁定 {duration} 分钟"
        )
    return attempts


def reset_failed_attempts(email: str) -> None:
    """M9: 登录成功后清除失败计数与锁定标记。"""
    email = (email or "").lower().strip()
    if not email:
        return

    client = _get_lockout_redis()
    if client:
        try:
            client.delete(_attempts_key(email), _locked_key(email))
            return
        except Exception as e:
            logger.warning(f"Redis 清除失败计数失败，回退内存: {e}")

    _in_memory_attempts.pop(email, None)
    _in_memory_locked_until.pop(email, None)


def get_remaining_lockout_seconds(email: str) -> int:
    """M9: 返回账户剩余锁定秒数（未锁定返回 0）。

    安全失败原则：Redis 故障时返回 ACCOUNT_LOCKOUT_DURATION_MINUTES * 60，
    避免降级为"未锁定"导致攻击者绕过账户锁定策略进行暴力破解。
    """
    email = (email or "").lower().strip()
    if not email:
        return 0

    client = _get_lockout_redis()
    if client:
        try:
            ttl = client.ttl(_locked_key(email))
            return max(0, int(ttl))
        except Exception as e:
            logger.warning(f"Redis 锁定查询失败，安全降级返回满锁定时长: {e}")
            # 返回满锁定时长，避免 Redis 故障时绕过账户锁定
            return int(settings.ACCOUNT_LOCKOUT_DURATION_MINUTES) * 60

    unlock_at = _in_memory_locked_until.get(email, 0)
    return max(0, int(unlock_at - time.time()))


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def register(
    db: AsyncSession,
    user_in: UserCreate,
    fingerprint: Optional[str] = None,
) -> tuple[User, Token]:
    """注册新用户（无企业归属）。

    P0-02-B: 此函数仅用于超级管理员/系统账号注册（无 enterprise_id）。
    普通企业用户必须通过 register_with_invite 走邀请流程。
    """
    existing = await get_user_by_email(db, user_in.email)
    if existing:
        raise ValueError("该邮箱已被注册")

    user = User(
        email=user_in.email,
        password_hash=get_password_hash(user_in.password),
        name=user_in.name,
        # P0-02-B: enterprise_id 强制为 None，不再从 user_in 读取
        enterprise_id=None,
        role="member",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    # P2-3: 先生成 token（设置 refresh_token_hash），再统一 commit
    # 这样用户记录和 refresh_token_hash 一次性持久化
    token = _create_token(user, fingerprint=fingerprint)
    await db.commit()

    return user, token


async def register_with_invite(
    db: AsyncSession,
    user_in: UserCreate,
    invitation: Invitation,
    fingerprint: Optional[str] = None,
) -> tuple[User, Token]:
    """通过邀请注册新用户。

    P0-02-B: 强制使用 Invitation.enterprise_id 作为用户的企业归属，
    邀请状态由调用方负责预先校验（status="pending" 且未过期）。

    本函数负责：
    1. 校验邮箱未被注册
    2. 创建用户（enterprise_id 来自 Invitation）
    3. 递增 Enterprise.invite_used_count
    4. 置 Invitation.status="used"、used_by_user_id、used_at
    5. 一次性 commit（事务原子性，P0-02-D 防竞态）

    邀请次数上限校验由调用方负责（auth.py 端点在调用前校验）。
    """
    existing = await get_user_by_email(db, user_in.email)
    if existing:
        raise ValueError("该邮箱已被注册")

    # P0-02-D: 锁定 Enterprise 行防止并发竞态（SQLite 忽略 with_for_update，但语义上正确）
    enterprise_result = await db.execute(
        select(Enterprise)
        .where(Enterprise.id == invitation.enterprise_id)
        .with_for_update()
    )
    enterprise = enterprise_result.scalar_one_or_none()
    if not enterprise:
        raise ValueError("邀请所属企业不存在")
    if not enterprise.is_active:
        raise ValueError("企业已被禁用")
    # 二次校验邀请次数（即使端点已校验，并发情况下仍可能在事务间被使用）
    if enterprise.invite_used_count >= enterprise.invite_max_uses:
        raise ValueError("邀请次数已用完")

    user = User(
        email=user_in.email,
        password_hash=get_password_hash(user_in.password),
        name=user_in.name,
        enterprise_id=invitation.enterprise_id,
        role="member",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # 递增邀请使用计数
    enterprise.invite_used_count = (enterprise.invite_used_count or 0) + 1

    # 置 Invitation 状态为已使用
    invitation.status = "used"
    invitation.used_by_user_id = user.id
    invitation.used_at = datetime.now(timezone.utc)

    # 生成 token（设置 refresh_token_hash）
    token = _create_token(user, fingerprint=fingerprint)

    # 一次性 commit：用户、企业计数、邀请状态、token 全部原子化持久化
    await db.commit()

    return user, token


async def login(
    db: AsyncSession, credentials: UserLogin, fingerprint: Optional[str] = None
) -> tuple[User, Token]:
    email = (credentials.email or "").lower().strip()

    # M9: 登录前检查账户是否被锁定（即使邮箱不存在也检查，防止枚举）
    if is_account_locked(email):
        remaining = get_remaining_lockout_seconds(email)
        logger.warning(
            f"M9 登录拒绝（账户锁定）：email={email}，剩余 {remaining}s"
        )
        raise AccountLockedError(
            email, time.time() + remaining
        )

    user = await get_user_by_email(db, email)
    if not user or not verify_password(credentials.password, user.password_hash):
        # M9: 记录失败次数，达到阈值自动锁定
        attempts = record_failed_login(email)
        logger.info(
            f"M9 登录失败：email={email}，累计失败 {attempts} 次"
        )
        raise ValueError("邮箱或密码错误")

    # M9: 登录成功，清除失败计数
    reset_failed_attempts(email)

    # P2-3: 登录时更新 last_login_at + 写入 refresh_token_hash，一次 commit
    user.last_login_at = datetime.now(timezone.utc)
    token = _create_token(user, fingerprint=fingerprint)
    await db.commit()

    return user, token


def _create_token(
    user: User,
    family_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Token:
    """创建 access + refresh token 对。

    P2-3: refresh_token 的哈希存储到 User.refresh_token_hash，
    登出时清除该字段即可使 refresh token 失效（无需维护黑名单）。
    P2-T8b: 支持 family_id 实现 Refresh Token 家族检测，防止重放攻击。
    P0-S4: refresh token 携带请求指纹（fingerprint），环境变化时拒绝刷新。
    注意：此函数仅设置 user.refresh_token_hash 和 refresh_token_family_id（pending 状态），
    调用方负责在适当时候 await db.commit() 持久化。
    """
    # P2-T8b: 登录/注册时（无 family_id）生成新家族；刷新时复用现有家族
    token_family_id = family_id or str(uuid_lib.uuid4())

    access_token = create_access_token(data={"sub": str(user.id), "email": user.email})
    refresh_data: dict = {"sub": str(user.id), "email": user.email}
    if fingerprint:
        refresh_data["fp"] = fingerprint
    refresh_token = create_refresh_token(
        data=refresh_data,
        family_id=token_family_id,
    )

    # 写入 refresh_token_hash 和家族 ID（pending 状态，由调用方 commit）
    user.refresh_token_hash = hash_token(refresh_token)
    user.refresh_token_family_id = token_family_id

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
    )


async def refresh_tokens(
    db: AsyncSession,
    refresh_token: str,
    fingerprint: Optional[str] = None,
) -> tuple[User, Token]:
    """用 refresh token 获取新的 access token + refresh token 对。

    流程（P2-T8b 家族检测增强版）：
    1. 解码 refresh token，校验未过期且类型为 refresh
    2. 查库找到用户
    3. 校验家族 ID 匹配（防止跨家族 token 重放）
    4. 校验 refresh_token_hash 与传入 token 匹配（防止已轮换的旧 token 被重用）
    5. P2-T8b / P1-08: Refresh Token 重放检测：
       - 如果家族 ID 不匹配 → 检测到跨家族重放，立即撤销
       - 如果家族 ID 匹配但 hash 不匹配 → 检测到家族内旧 token 重放（窃取攻击），立即撤销整个家族
    6. 签发新的 token 对（轮换，保持相同家族 ID）
    """
    # decode_token 对无效 token 抛 HTTPException，这里转为 ValueError
    # 让 service 层的异常类型保持一致（ValueError），由 API 层转为 HTTP 401
    try:
        payload = decode_token(refresh_token)
    except HTTPException as _decode_err:
        errors_total.labels(module=__name__, exception_type="HTTPException").inc()
        raise ValueError("无效的 refresh token") from _decode_err

    if payload.get("type") != "refresh":
        raise ValueError("无效的 refresh token")

    user_id = payload.get("sub")
    token_family_id = payload.get("fid")
    if not user_id:
        raise ValueError("无效的 refresh token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError("用户不存在")
    if not user.is_active:
        raise ValueError("账号已被禁用")

    # P2-T8b: 家族检测逻辑
    # 1. 如果用户没有家族 ID 或 refresh_token_hash，说明已登出
    if not user.refresh_token_hash or not user.refresh_token_family_id:
        raise ValueError("refresh token 已失效，请重新登录")

    # 2. 家族 ID 不匹配：跨家族重放（可能是伪造或其他用户的 token）
    if token_family_id != user.refresh_token_family_id:
        logger.warning(
            f"检测到 Refresh Token 跨家族重放攻击，用户 {user.id}，"
            f"预期家族 {user.refresh_token_family_id}，实际家族 {token_family_id}，"
            "已撤销所有 refresh token"
        )
        user.refresh_token_hash = None
        user.refresh_token_family_id = None
        await db.commit()
        errors_total.labels(module=__name__, exception_type="TokenFamilyMismatch").inc()
        raise ValueError("refresh token 已失效，请重新登录")

    # 3. 家族 ID 匹配但 token hash 不匹配：家族内旧 token 重放（典型的 token 窃取场景）
    # 攻击者窃取了旧的 refresh token 尝试使用，但用户已经正常刷新获取了新 token
    if not verify_token_hash(refresh_token, user.refresh_token_hash):
        logger.warning(
            f"检测到 Refresh Token 家族内重放攻击（token 已泄露），用户 {user.id}，"
            f"家族 {token_family_id}，已撤销整个 token 家族"
        )
        user.refresh_token_hash = None
        user.refresh_token_family_id = None
        await db.commit()
        errors_total.labels(module=__name__, exception_type="TokenReplayDetected").inc()
        raise ValueError("refresh token 已失效，请重新登录")

    # P0-S4: 校验请求指纹，环境显著变化时拒绝刷新并要求重新登录
    token_fingerprint = payload.get("fp")
    if token_fingerprint and fingerprint and token_fingerprint != fingerprint:
        logger.warning(
            f"检测到 Refresh Token 请求指纹不匹配，用户 {user.id}，"
            "可能为 token 窃取或代理环境变化，已撤销该家族"
        )
        user.refresh_token_hash = None
        user.refresh_token_family_id = None
        await db.commit()
        errors_total.labels(module=__name__, exception_type="TokenFingerprintMismatch").inc()
        raise ValueError("登录环境发生变化，请重新登录")

    # 签发新的 token 对（保持相同家族 ID，轮换 refresh token）
    token = _create_token(user, family_id=user.refresh_token_family_id, fingerprint=fingerprint)
    await db.commit()
    return user, token


async def logout(db: AsyncSession, user: User, access_token: Optional[str] = None) -> None:
    """登出：清除 refresh_token_hash 和家族 ID，使 refresh token 失效。

    P0-S3: 同时将当前 access token 加入黑名单，使其立即失效。
    """
    if access_token:
        revoke_access_token(access_token)
    user.refresh_token_hash = None
    user.refresh_token_family_id = None
    db.add(user)
    await db.commit()
