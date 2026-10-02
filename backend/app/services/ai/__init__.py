"""AutoTeams 5.0 AI 能力包（多模型路由 + BYOK）。

依据重构计划 §4.2.2「服务层重构 — 消灭巨型文件」：单个 .py ≤ 400 行，
按职责拆分为 package。``llm_service.py`` 已从 585 行巨型类瘦身为兼容门面，
实际实现分布在本包：

- ``registry``    提供方注册表 + 决策四的任务路由矩阵 + 4.0 国产别名/预设（纯数据，无 IO）
- ``credentials`` BYOK 凭证解析与运行时切换（加密存储复用 credential_crypto）
- ``circuit``     模型级熔断器
- ``router``      统一多模型路由管理器（装配候选链 + 故障转移编排）
"""
from app.services.ai import circuit, credentials, registry, router
from app.services.ai.circuit import CircuitBreaker, LLMCircuitOpenError
from app.services.ai.credentials import (
    ByokProfile,
    get_active_byok,
    resolve_credentials,
    set_active_byok,
)
from app.services.ai.registry import (
    ModelTier,
    Provider,
    TaskType,
    available_providers,
    normalize_model,
    preset_model_for_tier,
    provider_has_credentials,
    provider_of,
    qualified_model_for_provider,
    resolve_model_by_provider_name,
    routing_chain,
    settings_model_for_tier,
    task_type_for_tier,
)
from app.services.ai.router import ModelRouter, RouteCandidate

__all__ = [
    "ByokProfile",
    "CircuitBreaker",
    # 子模块本身也是 re-export：调用方写 `from app.services.ai import registry`
    "circuit",
    "credentials",
    "registry",
    "router",
    "LLMCircuitOpenError",
    "ModelRouter",
    "ModelTier",
    "Provider",
    "RouteCandidate",
    "TaskType",
    "available_providers",
    "get_active_byok",
    "normalize_model",
    "preset_model_for_tier",
    "provider_has_credentials",
    "provider_of",
    "qualified_model_for_provider",
    "resolve_credentials",
    "resolve_model_by_provider_name",
    "routing_chain",
    "set_active_byok",
    "settings_model_for_tier",
    "task_type_for_tier",
]
