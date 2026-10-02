"""企业模型 API 配置的读写与解析服务。

职责：
1. 读写企业的 LLMApiConfig（密钥加密落库，读取时解密仅存于内存）。
2. resolve_enterprise_llm  根据配置解析出一次 LLM 调用所需的
   (provider, api_key, api_base, model)，供 llm_service / agents 使用。

安全：密钥只在此 decrypt 后返回给调用方，绝不进入日志或响应体。
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_config import LLMApiConfig
from app.schemas.llm_config import LLMApiConfigUpdate, LLMApiConfigView
from app.utils.crypto import decrypt_secret, encrypt_secret, mask_secret

logger = logging.getLogger(__name__)


async def get_config(db: AsyncSession, enterprise_id: str) -> Optional[LLMApiConfig]:
    result = await db.execute(
        select(LLMApiConfig).where(LLMApiConfig.enterprise_id == enterprise_id)
    )
    return result.scalar_one_or_none()


async def upsert_config(
    db: AsyncSession,
    enterprise_id: str,
    data: LLMApiConfigUpdate,
) -> LLMApiConfig:
    """创建或更新企业的模型 API 配置。

    api_key 字段：非空则重新加密；留空保留原密钥（避免前端每次提交被迫重填）。
    """
    config = await get_config(db, enterprise_id)
    if config is None:
        config = LLMApiConfig(enterprise_id=enterprise_id)
        db.add(config)

    config.openai_enabled = data.openai_enabled
    config.openai_api_base = data.openai_api_base
    config.openai_model = data.openai_model
    if data.openai_api_key:
        config.openai_api_key = encrypt_secret(data.openai_api_key)

    config.anthropic_enabled = data.anthropic_enabled
    config.anthropic_api_base = data.anthropic_api_base
    config.anthropic_model = data.anthropic_model
    if data.anthropic_api_key:
        config.anthropic_api_key = encrypt_secret(data.anthropic_api_key)

    await db.flush()
    await db.commit()
    await db.refresh(config)
    return config


def to_view(config: Optional[LLMApiConfig]) -> Optional[LLMApiConfigView]:
    """构造掩码视图（密钥绝不返回明文）。"""
    if config is None:
        return None
    openai_key = decrypt_secret(config.openai_api_key or "")
    anthropic_key = decrypt_secret(config.anthropic_api_key or "")
    return LLMApiConfigView(
        enterprise_id=config.enterprise_id,
        openai_enabled=config.openai_enabled,
        openai_api_base=config.openai_api_base,
        openai_model=config.openai_model,
        openai_has_key=bool(openai_key),
        openai_api_key_masked=mask_secret(openai_key),
        anthropic_enabled=config.anthropic_enabled,
        anthropic_api_base=config.anthropic_api_base,
        anthropic_model=config.anthropic_model,
        anthropic_has_key=bool(anthropic_key),
        anthropic_api_key_masked=mask_secret(anthropic_key),
        updated_at=config.updated_at.isoformat() if config.updated_at else None,
    )


def resolve_enterprise_llm(config: Optional[LLMApiConfig]) -> dict:
    """解析企业当前应生效的 LLM 调用参数。

    返回：{"provider", "api_key", "api_base", "model"} 或全部为空（回退全局配置）。
    provider: "openai" | "anthropic" | None
    """
    if config is None:
        return {"provider": None, "api_key": None, "api_base": None, "model": None}

    if config.openai_enabled:
        key = decrypt_secret(config.openai_api_key or "")
        if key:
            return {
                "provider": "openai",
                "api_key": key,
                "api_base": config.openai_api_base,
                "model": _qualify_model("openai", config.openai_model),
            }
    if config.anthropic_enabled:
        key = decrypt_secret(config.anthropic_api_key or "")
        if key:
            return {
                "provider": "anthropic",
                "api_key": key,
                "api_base": config.anthropic_api_base,
                "model": _qualify_model("anthropic", config.anthropic_model),
            }
    return {"provider": None, "api_key": None, "api_base": None, "model": None}


def _qualify_model(provider: str, model: Optional[str]) -> Optional[str]:
    """为模型名补充 litellm 所需的 provider 前缀（如 claude-3 -> anthropic/claude-3）。"""
    if not model:
        return model
    if "/" in model:
        return model
    return f"{provider}/{model}"
