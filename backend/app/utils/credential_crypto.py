"""AutoTeams 凭证加密工具（基于 Fernet 对称加密）。

用于对企业各渠道凭证（AppSecret、Bot Token、Webhook Secret）
以及模型 API Key 进行落地存储强加密，防止数据库泄露导致凭据被盗。
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)


class CredentialDecryptError(RuntimeError):
    """凭证解密失败（密文无效 / 密钥不匹配 / 历史明文未迁移）。"""


def _derive_fernet_key(secret: str) -> bytes:
    """基于密钥种子派生固定 32 字节并进行 URL-safe Base64 编码的 Fernet 密钥。"""
    if not secret:
        # 未配置密钥时的固定兜底种子：仅用于让本地开发能跑通，
        # 生产环境 config.py 会强制要求显式配置密钥。
        secret = "autoteams-default-secure-credential-seed-key-2026"  # noqa: S105
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    key_material = settings.ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    fernet_key = _derive_fernet_key(key_material)
    return Fernet(fernet_key)


def encrypt_credential(plain_text: Optional[str]) -> str:
    """加密敏感凭据字符串。若输入为空则返回空字符串。"""
    if not plain_text:
        return ""
    try:
        f = _get_fernet()
        encrypted_bytes = f.encrypt(plain_text.encode("utf-8"))
        return encrypted_bytes.decode("utf-8")
    except Exception as e:
        logger.error(f"凭证加密失败: {e}", exc_info=True)
        raise RuntimeError("Credential encryption failed") from e


def decrypt_credential(cipher_text: Optional[str]) -> str:
    """解密敏感凭据字符串（严格模式）。

    P2-11 安全修复：移除「解密失败回退明文」逻辑。原容错设计使加密形同虚设——
    数据库泄露时攻击者写入的明文凭证会被原样采用，历史明文也永远无需迁移。
    现改为：
    - 解密失败抛出 CredentialDecryptError，由调用方决定降级策略（禁用该凭证并告警）；
    - 历史明文数据应通过一次性迁移脚本（读取旧值 → encrypt_credential → 回写）完成升级。
    """
    if not cipher_text:
        return ""
    try:
        f = _get_fernet()
        decrypted_bytes = f.decrypt(cipher_text.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except InvalidToken as e:
        logger.error("凭证解密失败：密文无效或密钥已变更（可能为未迁移的历史明文）")
        raise CredentialDecryptError("credential decrypt failed: invalid token or key mismatch") from e
    except CredentialDecryptError:
        raise
    except Exception as e:
        logger.error("凭证解密异常: %s", type(e).__name__)
        raise CredentialDecryptError("credential decrypt failed") from e
