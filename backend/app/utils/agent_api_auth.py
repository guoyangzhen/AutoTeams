"""外部 Agent REST API 的机器凭证认证与授权。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import logging
import secrets
from typing import Iterable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.utils.branding import LEGACY_AGENT_KEY_PREFIX
from app.database import get_db
from app.models.agent_api_credential import AgentApiCredential
from app.models.enterprise import Enterprise
from app.models.user import User

logger = logging.getLogger(__name__)

AGENT_API_KEY_HEADER = "X-AutoTeams-Agent-Key"

# 品牌更名兼容期（历史请求头 → AutoTeams）：旧名请求头继续受理，
# 避免已发布的外部 Agent 程序在切换窗口内被拒。过渡期结束后可移除。
AGENT_API_KEY_HEADER_LEGACY = "X-AutoFDE-Agent-Key"

def extract_agent_api_key(request: Request, key: str | None) -> str | None:
    """按「新名优先、旧名兜底」取出请求头中的机器密钥。"""
    candidate = (key if key is not None else request.headers.get(AGENT_API_KEY_HEADER_LEGACY) or "").strip()
    return candidate or None

AGENT_API_SCOPES = frozenset({
    "agent:read",
    "agent:chat",
    "build:read",
    "compiler:read",
})


def _key_signing_secret() -> bytes:
    """返回 API key HMAC 密钥，并在旧配置回退时留下明确告警。"""
    secret = settings.AGENT_API_KEY_HMAC_SECRET or settings.AUDIT_SIGNING_KEY
    if not secret:
        secret = settings.JWT_SECRET_KEY
        if not settings.DEBUG:
            logger.warning(
                "AGENT_API_KEY_HMAC_SECRET 与 AUDIT_SIGNING_KEY 均未配置；"
                "Agent API 密钥暂回退 JWT_SECRET_KEY。生产环境应配置独立密钥。"
            )
    return secret.encode("utf-8")


def hash_agent_api_key(raw_key: str) -> str:
    """对完整机器密钥做 HMAC-SHA256；数据库绝不保存明文。"""
    return hmac.new(_key_signing_secret(), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_agent_api_key() -> str:
    """生成仅返回一次的高熵 Agent API key。"""
    return f"{settings.AGENT_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class AgentApiPrincipal:
    """经验证的机器调用主体，企业与调用者由服务端固定，不能来自请求体。"""

    credential_id: str
    enterprise_id: str
    owner_user_id: str
    scopes: frozenset[str]
    allowed_agent_ids: frozenset[str]
    owner: User

    def require_scopes(self, *required: str) -> None:
        missing = [scope for scope in required if scope not in self.scopes]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"机器凭证缺少所需 scope: {', '.join(missing)}",
            )

    def assert_agent_allowed(self, agent_id: str) -> None:
        if self.allowed_agent_ids and agent_id not in self.allowed_agent_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="该机器凭证未获授权访问此 Agent",
            )


def normalize_scopes(scopes: Iterable[str]) -> list[str]:
    """标准化并严格校验可发行的 scope 集合。"""
    normalized = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    unknown = sorted(set(normalized) - AGENT_API_SCOPES)
    if unknown:
        raise ValueError(f"不支持的 Agent API scope: {', '.join(unknown)}")
    if not normalized:
        raise ValueError("至少需要授予一个 Agent API scope")
    return normalized


async def get_agent_api_principal(
    request: Request,
    x_autoteams_agent_key: str | None = Header(default=None, alias=AGENT_API_KEY_HEADER),
    db: AsyncSession = Depends(get_db),
) -> AgentApiPrincipal:
    """验证机器密钥、租户与所有者状态，并更新最后使用时间。"""
    raw_key = extract_agent_api_key(request, x_autoteams_agent_key) or ""
    if not raw_key or len(raw_key) > 512 or not raw_key.startswith((settings.AGENT_API_KEY_PREFIX, LEGACY_AGENT_KEY_PREFIX)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或无效的 Agent API 密钥",
            headers={"WWW-Authenticate": "AgentKey"},
        )

    result = await db.execute(
        select(AgentApiCredential, Enterprise, User)
        .join(Enterprise, Enterprise.id == AgentApiCredential.enterprise_id)
        .join(User, User.id == AgentApiCredential.owner_user_id)
        .where(AgentApiCredential.key_hash == hash_agent_api_key(raw_key))
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或无效的 Agent API 密钥",
            headers={"WWW-Authenticate": "AgentKey"},
        )

    credential, enterprise, owner = row
    expired = (_as_utc(credential.expires_at) or datetime.max.replace(tzinfo=timezone.utc)) <= datetime.now(timezone.utc)
    if not credential.is_active or credential.revoked_at is not None or expired or not enterprise.is_active or not owner.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent API 凭证已失效",
            headers={"WWW-Authenticate": "AgentKey"},
        )

    try:
        scopes = frozenset(normalize_scopes(credential.scopes or []))
    except ValueError:
        logger.error("Agent API 凭证 %s 存在非法 scope，已拒绝使用", credential.id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent API 凭证配置无效") from None

    allowed_agent_ids = frozenset(str(value) for value in (credential.allowed_agent_ids or []) if value)
    credential.last_used_at = datetime.now(timezone.utc)
    # 使用记录是安全审计的一部分；认证完成后即提交，避免只读端点回滚此信息。
    await db.commit()

    request.state.agent_api_credential_id = credential.id
    request.state.user_id = str(owner.id)
    return AgentApiPrincipal(
        credential_id=credential.id,
        enterprise_id=credential.enterprise_id,
        owner_user_id=str(owner.id),
        scopes=scopes,
        allowed_agent_ids=allowed_agent_ids,
        owner=owner,
    )
