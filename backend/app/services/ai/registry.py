"""多模型提供方注册表与任务路由矩阵。

依据 AutoTeams 5.0 重构计划 §决策四「LLM 策略 — DeepSeek 优先 + 多模型 + BYOK」。

决策四规定的默认路由矩阵：

    ┌─────────────────┬───────────────────────┬─────────┐
    │ 任务类型        │ 默认模型              │ 备选     │
    ├─────────────────┼───────────────────────┼─────────┤
    │ 日常对话/客服   │ DeepSeek V4.1 Flash  │ Kimi    │
    │ 复杂推理/分析   │ DeepSeek V4.1 Flash  │ GLM-4   │
    │ 代码相关        │ 外接 Codex/Claude    │ DeepSeek│
    │ 文档总结/摘要   │ DeepSeek V4.1 Flash  │ Kimi    │
    │ Embedding       │ BAAI/bge-large-zh    │ -       │
    └─────────────────┴───────────────────────┴─────────┘

⚠️ **本模块同时承载 4.0 的国产模型别名/预设表**，原因是既有测试
（``test_llm_service.py`` / ``test_litellm_integration.py``）直接断言
``_normalize_model`` / ``get_preset_model`` / ``get_model_for_tier`` 的行为，
这些是已发布的公共契约，重构不得改变其返回值形态：

- ``_normalize_model("gpt-4")`` → ``"openai/gpt-4"``（**带** provider 前缀）
- ``get_model_for_tier(ModelTier.STRONG)`` → ``"gpt-4"``（**不带**前缀）
- ``get_preset_model(ModelTier.STRONG)`` → ``"doubao/doubao-pro-128k"``（带前缀）

模型 id 一律来自 ``settings``，不在本模块硬编码供应商模型名。
决策四要求的主力模型 DeepSeek-V4.1-Flash 对应 ``settings.DEEPSEEK_MODEL``，
由运维在 .env 配置，代码不假定该 id 一定可用。
"""
from __future__ import annotations

import enum
from typing import Optional

from app.config import settings


class TaskType(str, enum.Enum):
    """任务类型 —— 路由矩阵的第一维。"""

    CONVERSATION = "conversation"      # 日常对话 / 客服
    REASONING = "reasoning"            # 复杂推理 / 分析
    CODE = "code"                      # 代码相关
    SUMMARIZATION = "summarization"    # 文档总结 / 摘要
    EMBEDDING = "embedding"            # 向量化


class Provider(str, enum.Enum):
    """已注册提供方。BYOK 不在此列 —— 它是运行时的凭证覆盖，不是固定供应商。"""

    DEEPSEEK = "deepseek"
    MOONSHOT = "moonshot"   # Kimi
    ZHIPU = "zhipu"         # GLM-4
    OPENAI = "openai"
    EXTERNAL = "external"   # 外接本地 Agent（Codex / Claude Code）


class ModelTier:
    """模型层级（4.0 语义，全仓 21 个模块依赖这三个常量）。"""

    STRONG = "strong"      # 复杂分析、Agentic RAG 推理
    CHEAP = "cheap"        # 简单 FAQ、查询分类、Self-RAG 评估
    DEFAULT = "default"    # 默认


# ---------------------------------------------------------------------------
# 4.0 兼容层：国产模型别名与三层预设（行为受既有测试锁定，不可改）
# ---------------------------------------------------------------------------

DOMESTIC_MODEL_ALIASES: dict[str, dict[str, str]] = {
    "doubao": {
        "doubao-lite-4k": "doubao-lite-4k",
        "doubao-lite-32k": "doubao-lite-32k",
        "doubao-pro-128k": "doubao-pro-128k",
        "doubao-pro-32k": "doubao-pro-32k",
        "doubao-vision-pro-32k": "doubao-vision-pro-32k",
    },
    "qwen": {
        "qwen-turbo": "qwen-turbo",
        "qwen-plus": "qwen-plus",
        "qwen-max": "qwen-max",
        "qwen-long": "qwen-long",
    },
    "zhipu": {
        "glm-4": "glm-4",
        "glm-4-flash": "glm-4-flash",
        "glm-4-plus": "glm-4-plus",
        "glm-4-air": "glm-4-air",
        "glm-4v": "glm-4v",
    },
    "deepseek": {
        "deepseek-chat": "deepseek-chat",
        "deepseek-coder": "deepseek-coder",
        "deepseek-reasoner": "deepseek-reasoner",
    },
    "moonshot": {
        "moonshot-v1-8k": "moonshot-v1-8k",
        "moonshot-v1-32k": "moonshot-v1-32k",
        "moonshot-v1-128k": "moonshot-v1-128k",
    },
}

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "doubao": {
        "strong": "doubao/doubao-pro-128k",
        "cheap": "doubao/doubao-lite-32k",
        "default": "doubao/doubao-pro-32k",
    },
    "qwen": {
        "strong": "qwen/qwen-max",
        "cheap": "qwen/qwen-turbo",
        "default": "qwen/qwen-plus",
    },
    "zhipu": {
        "strong": "zhipu/glm-4",
        "cheap": "zhipu/glm-4-flash",
        "default": "zhipu/glm-4-air",
    },
    "deepseek": {
        "strong": "deepseek/deepseek-chat",
        "cheap": "deepseek/deepseek-chat",
        "default": "deepseek/deepseek-chat",
    },
    "moonshot": {
        "strong": "moonshot/moonshot-v1-128k",
        "cheap": "moonshot/moonshot-v1-8k",
        "default": "moonshot/moonshot-v1-32k",
    },
}


def default_provider() -> str:
    """当前配置的默认 provider（无前缀模型名的归属方）。"""
    return (settings.DEFAULT_LLM_PROVIDER or "openai").strip().lower()


def normalize_model(model: str) -> str:
    """规范化模型名：纯模型名按默认 provider 拼前缀，已带前缀的做别名映射。

    行为与 4.0 ``LLMService._normalize_model`` 完全一致（既有测试锁定）。
    """
    if not model:
        return model
    provider = default_provider()

    if "/" in model:
        p, m = model.split("/", 1)
        p = p.lower()
        return f"{p}/{DOMESTIC_MODEL_ALIASES.get(p, {}).get(m, m)}"

    alias_map = DOMESTIC_MODEL_ALIASES.get(provider, {})
    return f"{provider}/{alias_map.get(model, model)}"


def preset_model_for_tier(tier: str = ModelTier.DEFAULT) -> Optional[str]:
    """当前默认 provider 的三层预设模型（**带**前缀）。

    行为与 4.0 ``LLMService.get_preset_model`` 一致。
    """
    return PROVIDER_PRESETS.get(default_provider(), {}).get(tier)


def provider_of(model: str) -> str:
    """从 LiteLLM 模型名取 provider 前缀；无前缀时回落默认 provider。"""
    if not model:
        return default_provider()
    if "/" in model:
        return model.split("/", 1)[0].strip().lower()
    return default_provider()


# ---------------------------------------------------------------------------
# 5.0 层：决策四的任务路由矩阵
# ---------------------------------------------------------------------------

# 决策四路由矩阵：任务类型 → provider 尝试链（顺序即优先级）
ROUTING_MATRIX: dict[TaskType, tuple[Provider, ...]] = {
    TaskType.CONVERSATION: (Provider.DEEPSEEK, Provider.MOONSHOT),
    TaskType.REASONING: (Provider.DEEPSEEK, Provider.ZHIPU),
    TaskType.CODE: (Provider.EXTERNAL, Provider.DEEPSEEK),
    TaskType.SUMMARIZATION: (Provider.DEEPSEEK, Provider.MOONSHOT),
    TaskType.EMBEDDING: (Provider.DEEPSEEK,),
}

# tier → 任务类型：把 4.0 的 strong/cheap 语义对齐到 5.0 任务类型
TIER_TO_TASK: dict[str, TaskType] = {
    ModelTier.STRONG: TaskType.REASONING,
    ModelTier.CHEAP: TaskType.SUMMARIZATION,
    ModelTier.DEFAULT: TaskType.CONVERSATION,
}


def task_type_for_tier(tier: str) -> TaskType:
    """tier（strong/cheap/default）→ 任务类型；未识别回落 CONVERSATION。"""
    return TIER_TO_TASK.get(tier or ModelTier.DEFAULT, TaskType.CONVERSATION)


def routing_chain(task_type: TaskType) -> tuple[Provider, ...]:
    """该任务类型的完整 provider 尝试链。"""
    return ROUTING_MATRIX.get(task_type, (Provider.DEEPSEEK,))


def _settings_model_for(provider: Provider, tier: str) -> Optional[str]:
    """provider + tier → settings 中的原始模型名（**不带**前缀）。"""
    if provider is Provider.DEEPSEEK:
        if tier == ModelTier.STRONG:
            return settings.DEEPSEEK_MODEL_STRONG or settings.DEEPSEEK_MODEL
        if tier == ModelTier.CHEAP:
            return settings.DEEPSEEK_MODEL_CHEAP or settings.DEEPSEEK_MODEL
        return settings.DEEPSEEK_MODEL
    if provider is Provider.MOONSHOT:
        return settings.MOONSHOT_MODEL
    if provider is Provider.ZHIPU:
        return settings.ZHIPU_MODEL
    if provider is Provider.OPENAI:
        if tier == ModelTier.STRONG:
            return settings.OPENAI_MODEL_STRONG or settings.OPENAI_MODEL
        if tier == ModelTier.CHEAP:
            return settings.OPENAI_MODEL_CHEAP or settings.OPENAI_MODEL
        return settings.OPENAI_MODEL
    return None  # EXTERNAL 无内建模型 id


def settings_model_for_tier(tier: str) -> Optional[str]:
    """按 tier 取「当前默认 provider」的原始模型名（**不带**前缀）。

    行为与 4.0 ``LLMService.get_model_for_tier`` 一致（既有测试锁定）：
    国产 provider 且未显式配置 STRONG/CHEAP 时回落到 provider 预设。
    """
    provider = default_provider()
    if provider == "deepseek":
        if tier == ModelTier.STRONG:
            explicit = settings.DEEPSEEK_MODEL_STRONG
            if explicit:
                return explicit
        elif tier == ModelTier.CHEAP:
            explicit = settings.DEEPSEEK_MODEL_CHEAP
            if explicit:
                return explicit
    elif provider == "openai":
        if tier == ModelTier.STRONG:
            explicit = settings.OPENAI_MODEL_STRONG
            if explicit:
                return explicit
        elif tier == ModelTier.CHEAP:
            explicit = settings.OPENAI_MODEL_CHEAP
            if explicit:
                return explicit
    preset = PROVIDER_PRESETS.get(provider, {}).get(tier)
    if preset:
        return preset
    return settings_model_for_provider(Provider.OPENAI, tier)


def settings_model_for_provider(provider: Provider, tier: str) -> Optional[str]:
    """provider + tier → 原始模型名（不带前缀）。"""
    return _settings_model_for(provider, tier)


def qualified_model_for_provider(provider: Provider, tier: str = ModelTier.DEFAULT) -> Optional[str]:
    """provider + tier → 带前缀的 LiteLLM 模型名。"""
    model = _settings_model_for(provider, tier)
    if not model:
        return None
    return model if "/" in model else f"{provider.value}/{model}"


def resolve_model_by_provider_name(provider: str, tier: str = ModelTier.DEFAULT) -> Optional[str]:
    """按 provider **字符串**解析带前缀模型名，供 BYOK 使用。

    BYOK 的 provider 可能是任意 OpenAI 兼容自建标识（ollama / vllm / 自建网关），
    不在 ``Provider`` 枚举内；此类无 settings 预设，返回 None 由调用方提供 model。
    """
    for known in Provider:
        if known.value == (provider or "").strip().lower():
            return qualified_model_for_provider(known, tier)
    return None

def default_primary_model(tier: str = ModelTier.DEFAULT) -> Optional[str]:
    """决策四的「默认主力模型」解析入口。

    优先 DeepSeek（决策四指定的主力模型）；未配置凭证时按矩阵回落到下一个已配置
    provider；全部未配置时返回 None，由调用方决定走演示模式还是抛错——
    本函数**不编造**凭证或模型。
    """
    for provider in routing_chain(task_type_for_tier(tier)):
        if provider_has_credentials(provider):
            model = qualified_model_for_provider(provider, tier)
            if model:
                return model
    return None


def provider_has_credentials(provider: Provider) -> bool:
    """该 provider 是否已配置凭证。EXTERNAL 由本地桥接决定，不依赖 LLM Key。"""
    if provider is Provider.EXTERNAL:
        return True
    return bool(
        {
            Provider.DEEPSEEK: settings.DEEPSEEK_API_KEY,
            Provider.MOONSHOT: settings.MOONSHOT_API_KEY,
            Provider.ZHIPU: settings.ZHIPU_API_KEY,
            Provider.OPENAI: settings.OPENAI_API_KEY,
        }.get(provider, "")
    )


def available_providers() -> list[Provider]:
    """当前已配置凭证的 provider（不含 EXTERNAL）。"""
    return [p for p in Provider if p is not Provider.EXTERNAL and provider_has_credentials(p)]
