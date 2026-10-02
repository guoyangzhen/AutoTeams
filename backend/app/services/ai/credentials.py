"""凭证解析层 —— 支持企业私有 Key (BYOK) 的运行时即时切换。

依据决策四：
    BYOK (Bring Your Own Key) 支持：
    - 用户在设置页配置自己的 API Key
    - 支持 OpenAI / DeepSeek / Kimi (Moonshot) / GLM (Zhipu) / 任意 OpenAI 兼容接口
    - 密钥加密存储（复用现有 credential_crypto 模块）
    - 模型切换即时生效，无需重启

优先级链（自高而低）：
    1. 显式传入的 BYOK（调用点级，最高优先级）
    2. 会话级 BYOK 覆盖（``set_active_byok`` 设置，进程内即时生效）
    3. 目标模型 provider 前缀对应的 settings 凭证
    4. 全局 OPENAI_* 凭证（OpenAI 兼容端点的兜底）

安全约束：
- BYOK 的 api_key 一律视为**密文或明文皆可**，解密失败抛
  ``CredentialDecryptError`` 而**不静默回退明文**（与 P2-11 的既有结论一致）。
- 本模块不落库、不打印、不记录 api_key；日志只出现 provider 与模型名。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.utils.credential_crypto import CredentialDecryptError, decrypt_credential

logger = logging.getLogger(__name__)
# 全局默认（OpenAI 兼容）base 兜底
_DEFAULT_BASE = "https://api.openai.com/v1"


@dataclass(frozen=True)
class ByokProfile:
    """一份企业私有模型凭证。

    Attributes:
        provider: provider 前缀（deepseek / moonshot / zhipu / openai 或任意
            OpenAI 兼容自建标识，如 ``ollama`` / ``vllm``）
        api_key: **明文** Key（已在内存中）。落库加密由设置层负责；
        model: 私有模型名；为空时回落到该 provider 的 settings 模型。
        api_base: 自建端点（vLLM / Ollama / 代理网关），为空时用官方端点。
    """

    provider: str
    api_key: str
    model: Optional[str] = None
    api_base: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("BYOK provider 不能为空")
        if not self.api_key or not self.api_key.strip():
            raise ValueError("BYOK api_key 不能为空")

    @property
    def key(self) -> str:
        """明文 Key（内存态）。

        本属性**不做**解密，也不做「解密失败回退明文」——那是 P2-11 明确
        删除的逻辑：一旦允许回退，攻击者往库里写明文凭证即可被原样采用，
        加密形同虚设。需要从库中读取密文时走 :meth:`from_encrypted`，
        由它承担严格解密职责。
        """
        return self.api_key

    @classmethod
    def from_encrypted(
        cls,
        provider: str,
        api_key_cipher: str,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> "ByokProfile":
        """从**密文**构造 BYOK，解密失败直接抛错，绝不回退明文。

        这是决策四「密钥加密存储（复用 credential_crypto）」的落地点：
        设置页读库 → 严格解密 → 得到内存态 profile。
        """
        plain = decrypt_credential(api_key_cipher)
        if not plain:
            raise CredentialDecryptError("BYOK 凭证解密结果为空")
        return cls(provider=provider, api_key=plain, model=model, api_base=api_base)

def _settings_credentials(provider: str) -> tuple[Optional[str], Optional[str]]:
    """按 provider 前缀取 settings 中的全局凭证。"""
    p = (provider or "").strip().lower()
    if p == "deepseek":
        return settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_API_BASE
    if p == "moonshot":
        return settings.MOONSHOT_API_KEY, settings.MOONSHOT_API_BASE
    if p == "zhipu":
        return settings.ZHIPU_API_KEY, settings.ZHIPU_API_BASE
    # openai 及任意 OpenAI 兼容 provider（AgnesAI / 自建网关 / vLLM / Ollama）
    return settings.OPENAI_API_KEY, settings.OPENAI_API_BASE


def provider_of(model: str) -> str:
    """从 LiteLLM 模型名中取 provider 前缀。

    ``deepseek/deepseek-chat`` → ``deepseek``；``gpt-4`` → ``openai``。
    """
    if not model:
        return "openai"
    if "/" in model:
        return model.split("/", 1)[0].strip().lower()
    return (settings.DEFAULT_LLM_PROVIDER or "openai").strip().lower()


# ---------------------------------------------------------------------------
# 会话级 BYOK 覆盖（进程内即时切换，无需重启）
# ---------------------------------------------------------------------------

_active_byok: Optional[ByokProfile] = None


def set_active_byok(profile: Optional[ByokProfile]) -> None:
    """设置/清除会话级 BYOK 覆盖。

    决策四要求「模型切换即时生效，无需重启」——这里是切换点：
    设置页保存后调用本函数，下一次 LLM 调用立即走新凭证，无需重启进程。
    传 None 表示回落到全局 settings 凭证。
    """
    global _active_byok
    if profile is not None:
        logger.info(
            "BYOK 已切换: provider=%s model=%s（凭证不落日志）",
            profile.provider,
            profile.model or "<默认>",
        )
    else:
        logger.info("BYOK 覆盖已清除，回落到全局凭证")
    _active_byok = profile


def get_active_byok() -> Optional[ByokProfile]:
    """读取当前会话级 BYOK 覆盖。"""
    return _active_byok


def resolve_credentials(
    model: str,
    explicit_byok: Optional[ByokProfile] = None,
    explicit_key: Optional[str] = None,
    explicit_base: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """解析某次调用的 (api_key, api_base)。

    Args:
        model: LiteLLM 模型名（用于取 provider 前缀）
        explicit_byok: 调用点级 BYOK，优先级最高
        explicit_key: 调用点级明文 Key（旧接口 ``api_key=`` 兼容路径）
        explicit_base: 调用点级 api_base

    Returns:
        ``(api_key, api_base)``。两者都可能为 None，由上层决定是走演示模式还是抛错。
    """
    # 1) 显式 BYOK：连同其 model/base 一起接管
    byok = explicit_byok or _active_byok
    if byok is not None:
        try:
            return byok.key, (byok.api_base or _settings_credentials(byok.provider)[1])
        except CredentialDecryptError:
            # P2-11：解密失败必须显式失败，不得回退明文或静默降级
            logger.error("BYOK 凭证解密失败，拒绝继续调用")
            raise

    # 2) 旧接口兼容：调用点显式传入明文 Key（历史 API 契约）
    if explicit_key is not None:
        return explicit_key, explicit_base or self_base_for(model)

    # 3) provider 前缀对应的 settings 凭证
    key, base = _settings_credentials(provider_of(model))
    if key:
        return key, (explicit_base or base)
    return None, (explicit_base or base)


def self_base_for(model: str) -> str:
    """取该 provider 的默认端点。"""
    return _settings_credentials(provider_of(model))[1] or _DEFAULT_BASE
