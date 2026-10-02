"""JWT token 与请求指纹工具。

3.3.4: 从 utils/security.py 拆分而来，专注"身份凭据"职责——
access/refresh token 的签发、解码、哈希校验，以及 refresh token 绑定的
请求环境指纹计算。

历史上这些函数与 CSRF / 密码 / 依赖注入混在 security.py 中，导致单文件
承担 4 类职责。本模块仅保留 token 相关逻辑，使模块边界清晰。

向后兼容：utils/security.py 仍作为 re-export shim 保留所有公开符号，
现有 `from app.utils.security import create_access_token` 等导入不受影响。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import hashlib
import hmac
import uuid

import jwt
from jwt import PyJWTError as JWTError
from fastapi import HTTPException, Request, status

from app.config import settings


def compute_request_fingerprint(request: Request) -> str:
    """P0-S4: 计算请求指纹（IP + User-Agent 哈希）。

    用于 refresh token 与请求环境绑定，IP 或 UA 显著变化时要求重新登录。

    IP 解析策略（修复反向代理部署下 IP 检测失效问题，见架构审计 3.1.1）：
    - 配置 TRUSTED_PROXY_COUNT > 0 时，取 X-Forwarded-For 链中倒数第
      (TRUSTED_PROXY_COUNT+1) 位，即"第一个受信代理"记录的上游客户端 IP。
    - TRUSTED_PROXY_COUNT = 0（默认）时，XFF 视为可伪造并忽略，回落到
      request.client.host（直连客户端 IP），避免无代理场景下客户端伪造 XFF。
    - XFF 缺失、链长度不足或解析为空时，同样回落到 request.client.host。

    旧实现取 XFF 链最右侧值（forwarded.split(",")[-1]），在反向代理部署下
    恒为最近一跳代理 IP，导致所有客户端 IP 指纹相同、IP 检测退化为仅靠 UA。
    """
    ip = ""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        trusted = settings.TRUSTED_PROXY_COUNT
        if trusted > 0 and len(parts) > trusted:
            # 取倒数第 (trusted+1) 位：第一个受信代理记录的原始客户端 IP
            ip = parts[-(trusted + 1)]
        # 其余情况（trusted=0 或链长度 <= trusted）：不信任 XFF，回落到直连 IP
    if not ip:
        ip = request.client.host if request.client else ""
    ua = request.headers.get("User-Agent", "")
    raw = f"{ip}|{ua}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    )
    # P2-3: 加入 jti（JWT ID）确保每次签发的 token 唯一，防止同一秒生成的 token 相同
    # P2-09: 可选添加 iss/aud 声明
    to_encode.update({"exp": expire, "type": "access", "jti": str(uuid.uuid4())})
    if settings.JWT_ISSUER:
        to_encode["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        to_encode["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    family_id: Optional[str] = None,
) -> str:
    """创建 refresh token（长期有效，用于获取新的 access token）。

    与 access token 的区别：
    - type 字段为 "refresh"
    - 过期时间更长（默认 7 天）
    - 存储哈希值到 User.refresh_token_hash，登出时清除
    - 包含 jti 确保唯一性
    - P2-T8b: 支持 family_id（登录会话家族）用于重放检测
    - P2-09: 可选添加 iss/aud 声明
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=settings.JWT_REFRESH_EXPIRATION_DAYS)
    )
    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    })
    # P2-T8b: 家族 ID（同一登录会话链中所有 refresh token 共享）
    if family_id:
        to_encode["fid"] = family_id
    if settings.JWT_ISSUER:
        to_encode["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        to_encode["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def hash_token(token: str) -> str:
    """对 refresh token 做哈希（用于存储 refresh_token_hash）。

    使用 HMAC-SHA256（而非 bcrypt）：
    - JWT token 字符串通常远超 72 字节，bcrypt 会截断导致不同 token 的哈希碰撞
    - token 本身已是高熵随机串（JWT + jti），不需要 bcrypt 的慢哈希防暴力破解
    - HMAC-SHA256 无长度限制，且用 JWT_SECRET_KEY 作为密钥，数据库泄露也无法直接伪造
    """
    return hmac.new(
        settings.JWT_SECRET_KEY.encode('utf-8'),
        token.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def verify_token_hash(token: str, hashed: str) -> bool:
    """验证 token 与存储的哈希是否匹配（恒定时间比较防时序攻击）。"""
    computed = hash_token(token)
    return hmac.compare_digest(computed, hashed)


def _strict_decode_options() -> dict:
    """P2-09：仅在配置了 iss/aud 时才要求对应声明。"""
    options: dict = {}
    if settings.JWT_ISSUER or settings.JWT_AUDIENCE:
        options["verify_iss"] = bool(settings.JWT_ISSUER)
        options["verify_aud"] = bool(settings.JWT_AUDIENCE)
    return options


def decode_token_payload(token: str) -> dict:
    """按认证规则严格解码 token，失败时抛 `HTTPException(401)`。

    token 撤销等非 API 路径必须共用这一套解码选项：历史上撤销侧没有传
    audience/issuer，导致配置 `JWT_AUDIENCE` 后带 aud 的 access token
    解码失败并被当作"未撤销"，登出无法立即生效（AUD-09）。
    """
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER or None,
            audience=settings.JWT_AUDIENCE or None,
            options=_strict_decode_options(),
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无法验证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def decode_token(token: str) -> dict:
    """API 认证路径使用的严格解码。"""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER or None,
            audience=settings.JWT_AUDIENCE or None,
            options=_strict_decode_options(),
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无法验证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_token_max_age(token: str, fallback: timedelta) -> int:
    """3.4.7: 从 token 的 exp 声明反推 cookie max_age（秒）。

    避免 cookie max_age 基于配置默认值计算、而 token 实际 expires_delta
    被服务层覆盖时产生的不一致（cookie 仍有效但 token 已过期，或反之）。

    - 解析成功：返回 max(0, exp - now)
    - 解析失败或无 exp 声明：回退到传入的 fallback（基于配置默认值），
      不阻塞登录/注册主流程
    """
    try:
        # token 由本服务刚签发，无需再次校验签名，仅读取 exp 声明
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        if exp is None:
            return int(fallback.total_seconds())
        now = datetime.now(timezone.utc).timestamp()
        return max(0, int(exp) - int(now))
    except Exception:
        return int(fallback.total_seconds())
