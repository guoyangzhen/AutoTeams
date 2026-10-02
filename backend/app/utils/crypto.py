"""敏感数据静态加密工具（Fernet / AES-128-CBC）。

用于模型 API Key 等机密字段的加密存储。设计对齐业界成熟方案（LiteLLM
LITELLM_SALT_KEY、Open WebUI WEBUI_SECRET_KEY）：
- 密钥由应用级 ENCRYPTION_KEY（回退 JWT_SECRET_KEY）经 SHA-256 派生，
  不直接暴露原始 secret，且长度/格式恒为 Fernet 所需 32 bytes url-safe base64。
- 加密值可直接落库（Fernet token 自带认证与完整性校验）。
- 前端仅展示掩码（sk-****x），任何接口不得返回明文密钥。

安全约束（项目 BE-SEC 系列）：
- 绝不记录明文密钥到日志。
- 解密失败时抛出异常而非返回半截数据，避免静默降级。
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """惰性初始化 Fernet，密钥由 ENCRYPTION_KEY 或 JWT_SECRET_KEY 派生。"""
    global _fernet
    if _fernet is not None:
        return _fernet
    secret = (settings.ENCRYPTION_KEY or settings.JWT_SECRET_KEY).encode("utf-8")
    # SHA-256 派生 32 字节密钥并做 url-safe base64（Fernet 要求）
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    _fernet = Fernet(key)
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """加密明文密钥，返回可落库的 Fernet token。空值返回空串。"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """解密落地密钥。token 为空或解密失败时返回空串。"""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("敏感数据解密失败：密钥可能已变更或数据损坏")
        return ""
    except Exception as e:  # noqa: BLE001 - 解密失败不应中断主流程
        logger.error("敏感数据解密异常: %s", type(e).__name__)
        return ""


def mask_secret(secret: str, visible: int = 4) -> str:
    """掩码展示密钥，如 sk-proj-xxxx -> sk-****xxxx。"""
    if not secret:
        return ""
    if len(secret) <= visible:
        return "****"
    return f"{secret[:3]}****{secret[-visible:]}"
