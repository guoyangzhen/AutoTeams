"""统一多模型路由管理器（AutoTeams 5.0 决策四）。

职责：把「任务类型」翻译成「按优先级尝试的候选模型链」，
并对每条链上的候选做凭证解析、熔断检查与故障转移。

与 4.0 的差异：
- 4.0 候选链 = ``[显式模型/层级模型] + settings.LLM_FALLBACK_MODELS``，
  与任务语义无关，DeepSeek/Kimi/GLM 只是并列的字符串。
- 5.0 候选链由 **TaskType → Provider 链**决定（见 ``registry._ROUTING_MATRIX``），
  决策四中「日常对话备选 Kimi、复杂分析备选 GLM-4」成为一等语义。

链路装配优先级（前者覆盖后者）：
    1. 调用点显式 model（``model="..."``）——完全接管，不再追加矩阵候选
    2. 会话级 BYOK —— 换凭证但仍走矩阵（决策四要求切换即时生效）
    3. 矩阵链 —— 按任务类型装配
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import settings
from app.services.ai import registry
from app.services.ai.circuit import CircuitBreaker, LLMCircuitOpenError
from app.services.ai.credentials import ByokProfile, get_active_byok, resolve_credentials
from app.services.ai.registry import ModelTier, TaskType

logger = logging.getLogger(__name__)


class RouteCandidate:
    """一个候选模型及其解析出的凭证。"""

    __slots__ = ("model", "provider", "api_key", "api_base", "reason")

    def __init__(
        self,
        model: str,
        provider: str,
        api_key: Optional[str],
        api_base: Optional[str],
        reason: str = "",
    ) -> None:
        self.model = model
        self.provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.reason = reason

    @property
    def usable(self) -> bool:
        """有模型名且有凭证才可发起调用。"""
        return bool(self.model) and bool(self.api_key)

    def __repr__(self) -> str:
        # 凭证一律不进入 repr，防止日志/异常回显泄露
        return f"<RouteCandidate {self.model} provider={self.provider} usable={self.usable}>"


class ModelRouter:
    """多模型路由管理器（无状态计算 + 进程内熔断状态）。"""

    def __init__(self, breaker: Optional[CircuitBreaker] = None) -> None:
        self.breaker = breaker or CircuitBreaker()

    # -- 链路装配 ---------------------------------------------------------

    def build_candidates(
        self,
        task_type: TaskType = TaskType.CONVERSATION,
        tier: str = ModelTier.DEFAULT,
        explicit_model: Optional[str] = None,
        explicit_key: Optional[str] = None,
        explicit_base: Optional[str] = None,
        byok: Optional[ByokProfile] = None,
        service_model: Optional[str] = None,
        service_key: Optional[str] = None,
        service_base: Optional[str] = None,
    ) -> list[RouteCandidate]:
        """装配候选链，顺序即尝试顺序。

        Args:
            task_type: 任务类型，决定矩阵链
            tier: 4.0 层级语义（strong/cheap/default），映射到任务类型
            explicit_model: 调用点显式模型；一旦给出即完全接管
            explicit_key / explicit_base: 旧接口兼容的明文凭证
            byok: 调用点级 BYOK，优先级高于会话级

        Returns:
            候选列表；``usable=False`` 的候选表示该 provider 未配置凭证，
            调用方应跳过（不发起注定 401 的调用）。
        """
        tier = tier or ModelTier.DEFAULT
        effective_byok = byok or get_active_byok()

        # 1) 显式模型：调用方已明确指定，直接单候选，不追加矩阵
        if explicit_model:
            key, base = resolve_credentials(
                explicit_model, effective_byok, explicit_key, explicit_base
            )
            return [
                RouteCandidate(
                    explicit_model,
                    registry.provider_of(explicit_model),
                    key,
                    base,
                    reason="explicit",
                )
            ]

        candidates: list[RouteCandidate] = []
        # 2) BYOK 接管：换凭证不换矩阵（决策四：切换即时生效）
        if effective_byok is not None:
            model = effective_byok.model or registry.resolve_model_by_provider_name(
                effective_byok.provider, tier
            )
            if not model:
                # 自建 OpenAI 兼容端点未给 model 时无法推断，交给调用方显式指定
                logger.warning(
                    "BYOK provider=%s 未提供 model 且无内置预设，跳过",
                    effective_byok.provider,
                )
            else:
                key, base = resolve_credentials(
                    model, explicit_byok=effective_byok, explicit_base=explicit_base
                )
                candidates.append(
                    RouteCandidate(
                        model, effective_byok.provider, key, base, reason="byok"
                    )
                )

        # 3) 矩阵链：按任务类型逐个 provider 装配
        for provider in registry.routing_chain(task_type):
            model = registry.qualified_model_for_provider(provider, tier)
            if not model:
                # EXTERNAL 等无 LiteLLM 模型 id 的 provider 交由外接桥接，
                # 本路由器不代为调用，跳过。
                logger.debug("provider %s 无内建模型 id，跳过", provider.value)
                continue
            if any(c.model == model for c in candidates):
                continue
            if not registry.provider_has_credentials(provider):
                candidates.append(
                    RouteCandidate(
                        model, provider.value, None, None, reason="no_credentials"
                    )
                )
                continue
            key, base = resolve_credentials(
                model, explicit_key=explicit_key, explicit_base=explicit_base
            )
            candidates.append(RouteCandidate(model, provider.value, key, base))

        # 4) 追加运维显式配置的故障转移链（矩阵之外的兜底）
        for extra in self._configured_fallbacks():
            if not any(c.model == extra for c in candidates):
                key, base = resolve_credentials(
                    extra, explicit_key=explicit_key, explicit_base=explicit_base
                )
                candidates.append(
                    RouteCandidate(extra, registry.provider_of(extra), key, base)
                )

        # 5) 末位兜底：矩阵与 fallback 都不可用时，回到服务实例自带的模型/凭证。
        #    这是 4.0 的既有语义——构造 LLMService(api_key=..., model=...) 后即使
        #    矩阵 provider 全未配 Key，该实例仍应能调用。
        if service_model and not any(c.usable for c in candidates):
            normalized = registry.normalize_model(service_model)
            candidates.append(
                RouteCandidate(
                    normalized,
                    registry.provider_of(normalized),
                    service_key,
                    service_base,
                    reason="service_default",
                )
            )

        return candidates

    @staticmethod
    def _configured_fallbacks() -> list[str]:
        """解析 settings.LLM_FALLBACK_MODELS（逗号分隔）。"""
        if not settings.LLM_FALLBACK_MODELS:
            return []
        return [m.strip() for m in settings.LLM_FALLBACK_MODELS.split(",") if m.strip()]

    # -- 熔断 -------------------------------------------------------------

    def is_circuit_open(self, model: str) -> bool:
        return self.breaker.is_open(model)

    def record_success(self, model: str) -> None:
        self.breaker.record_success(model)

    def record_failure(self, model: str) -> None:
        self.breaker.record_failure(model)

    def raise_if_circuit_open(self, model: str) -> None:
        """候选处于冷却期时快速失败，交由调用方换下一个候选。"""
        if self.breaker.is_open(model):
            raise LLMCircuitOpenError(f"LLM 模型 {model} 正在熔断冷却")

    # -- 解析入口 ---------------------------------------------------------

    def resolve_primary_model(
        self, task_type: TaskType = TaskType.CONVERSATION, tier: str = ModelTier.DEFAULT
    ) -> Optional[str]:
        """决策四的「默认主力模型」：返回该任务类型的首个可用候选。"""
        for candidate in self.build_candidates(task_type=task_type, tier=tier):
            if candidate.usable:
                return candidate.model
        return None

    def describe_route(
        self, task_type: TaskType = TaskType.CONVERSATION, tier: str = ModelTier.DEFAULT
    ) -> list[dict]:
        """可观测的路由快照（不含任何凭证），供设置页展示与排障。"""
        return [
            {
                "model": c.model,
                "provider": c.provider,
                "usable": c.usable,
                "reason": c.reason,
            }
            for c in self.build_candidates(task_type=task_type, tier=tier)
        ]
