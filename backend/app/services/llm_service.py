"""LLM 服务门面（AutoTeams 5.0）。

本文件**不再承载实现**。依据重构计划 §4.2.2「服务层重构 — 消灭巨型文件」
（原则：单个 .py ≤ 400 行），原 585 行的巨型类已拆分到 ``app/services/ai/``：

- ``ai/registry``    提供方注册表 + 决策四任务路由矩阵 + 4.0 国产别名/预设
- ``ai/credentials`` BYOK 凭证解析与运行时切换
- ``ai/circuit``     模型级熔断器
- ``ai/router``      统一多模型路由管理器

本模块只做两件事：
1. **保持公共 API 稳定**——全仓 21 个模块与十余个测试文件直接
   ``from app.services.llm_service import llm_service, ModelTier, LLMUsageStats``，
   门面把这些名字原样再导出，调用方零改动。
2. **把「选哪个模型」委托给路由器**——4.0 的候选链是
   ``[显式模型] + LLM_FALLBACK_MODELS``，与任务语义无关；5.0 改为
   按决策四的任务类型路由（日常对话备选 Kimi / 复杂分析备选 GLM-4）。

⚠️ 以下历史语义被既有测试锁定，重构**不得改变返回值形态**：
``_normalize_model`` 返回**带** provider 前缀的模型名；
``get_model_for_tier`` 返回**不带**前缀的原始模型名；
``get_preset_model`` 返回**带**前缀的预设模型名。
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncIterator, Optional

from app.config import settings  # noqa: F401  —— 既有测试以此路径 monkeypatch
from app.services.ai import registry
from app.services.ai.circuit import CircuitBreaker, LLMCircuitOpenError
from app.services.ai.credentials import ByokProfile, resolve_credentials
from app.services.ai.registry import ModelTier, Provider, TaskType
from app.services.ai.router import ModelRouter
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

__all__ = [
    "LLMService",
    "LLMUsageStats",
    "ModelTier",
    "LLMCircuitOpenError",
    "Provider",
    "TaskType",
    "llm_service",
]


class LLMUsageStats:
    """LLM 调用统计（单次调用的成本和 token 用量）。"""

    def __init__(self) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.cost_usd: float = 0.0
        self.model_used: str = ""
        self.fallback_used: bool = False

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "model_used": self.model_used,
            "fallback_used": self.fallback_used,
        }


class LLMService:
    """LLM 服务门面：模型选择委托给 ai.router，请求执行保留在类内。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        provider = registry.default_provider()
        if provider == "deepseek":
            self.api_key = api_key if api_key is not None else settings.DEEPSEEK_API_KEY
            self.api_base = api_base if api_base is not None else settings.DEEPSEEK_API_BASE
            self.model = model or settings.DEEPSEEK_MODEL
        else:
            self.api_key = api_key if api_key is not None else settings.OPENAI_API_KEY
            self.api_base = api_base if api_base is not None else settings.OPENAI_API_BASE
            self.model = model or settings.OPENAI_MODEL

        self._use_fallback = not bool(self.api_key) and settings.LLM_ALLOW_FALLBACK

        # 4.0 兼容属性：既有测试直接读取 service._fallback_models
        self._fallback_models: list[str] = [
            m.strip() for m in (settings.LLM_FALLBACK_MODELS or "").split(",") if m.strip()
        ]

        self._router = ModelRouter(CircuitBreaker())
        self._litellm = None
        self._litellm_available = False
        try:
            import litellm

            litellm.drop_params = True
            litellm.set_verbose = False
            self._litellm = litellm
            self._litellm_available = True
            logger.info(
                "LiteLLM 已加载，主模型=%s，默认主力路由=%s，决策四矩阵=%s",
                self.model,
                self._router.resolve_primary_model(TaskType.CONVERSATION),
                {t.value: [p.value for p in registry.routing_chain(t)] for t in TaskType},
            )
        except ImportError:
            logger.warning(
                "litellm 未安装，LLM 服务强制降级到演示模式。请运行: pip install litellm"
            )
            self._use_fallback = True

        if not self.api_key and not self._use_fallback and not settings.DEBUG:
            raise RuntimeError(
                "拒绝启动：未配置任何 LLM API Key，且 LLM_ALLOW_FALLBACK=false。"
                "请在 .env 中配置 DEEPSEEK_API_KEY / OPENAI_API_KEY，"
                "或仅在开发环境设置 LLM_ALLOW_FALLBACK=true 启用演示模式。"
            )

    # -- 4.0 兼容层（行为被既有测试锁定） ---------------------------------

    @classmethod
    def _normalize_model(cls, model: str) -> str:
        """规范化模型名（带 provider 前缀）。见 ai.registry.normalize_model。"""
        return registry.normalize_model(model)

    @classmethod
    def get_preset_model(cls, tier: str = ModelTier.DEFAULT) -> Optional[str]:
        """当前默认 provider 的预设模型（带前缀）。"""
        return registry.preset_model_for_tier(tier)

    def get_model_for_tier(self, tier: str) -> str:
        """按层级取**原始**模型名（不带前缀）。跨模块公开，供 agents.py 选模型。

        DEFAULT 恒返回 ``self.model``：构造期显式传入的 model 优先于 settings，
        这是 4.0 既有行为（``LLMService(model="gpt-4")`` 覆盖环境变量）。
        """
        if tier == ModelTier.DEFAULT:
            return self.model
        return registry.settings_model_for_tier(tier) or self.model

    # -- 熔断代理（4.0 曾内联在 LLMService） ----------------------------

    def _circuit_is_open(self, model: str) -> bool:
        return self._router.is_circuit_open(model)

    def _record_model_success(self, model: str) -> None:
        self._router.record_success(model)

    def _record_model_failure(self, model: str) -> None:
        self._router.record_failure(model)

    def _is_retryable_error(self, error: Exception) -> bool:
        """仅重试网络/超时/限流/服务端错误；参数与鉴权错误应立即故障转移。"""
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        if status is None:
            return True
        try:
            code = int(status)
        except (TypeError, ValueError):
            return True
        return code in (408, 409, 425, 429) or code >= 500

    async def _completion_with_resilience(self, *, model: str, **kwargs: Any) -> Any:
        """在单模型内执行有界重试；失败后由调用方切换备用模型。

        不可重试错误（参数/鉴权）与最后一次尝试失败时，**原样抛出**底层异常——
        调用方的故障转移与 4xx/5xx 判定都依赖异常类型，包一层 RuntimeError
        会让这些信号全部退化成同一种错误。
        """
        self._router.raise_if_circuit_open(model)
        attempts = max(0, settings.LLM_REQUEST_RETRY_ATTEMPTS) + 1
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                response = await self._litellm.acompletion(model=model, num_retries=0, **kwargs)
                self._router.record_success(model)
                return response
            except Exception as error:  # noqa: BLE001
                last_error = error
                if not self._is_retryable_error(error) or attempt == attempts - 1:
                    self._router.record_failure(model)
                    raise
                delay = min(
                    settings.LLM_RETRY_MAX_BACKOFF_SECONDS,
                    settings.LLM_RETRY_INITIAL_BACKOFF_SECONDS * (2**attempt),
                )
                # 加小幅抖动，避免多个并发请求在限流窗口同一时刻重试
                # 抖动只用于打散重试时刻，不涉及任何安全用途。
                delay *= 0.8 + random.random() * 0.4  # noqa: S311
                logger.warning(
                    "LLM 瞬态错误，准备重试: model=%s attempt=%s/%s delay=%.2fs error=%s",
                    model, attempt + 1, attempts, delay, error,
                )
                await asyncio.sleep(delay)
        self._router.record_failure(model)
        raise RuntimeError("LLM 调用重试结束") from last_error

    def _generate_fallback_response(self, messages: list[dict]) -> str:
        """演示模式响应（无 API Key 时使用）。"""
        user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_msg = msg.get("content", "")
                break

        if "你好" in user_msg or "介绍" in user_msg:
            return (
                "你好！我是 AutoTeams 智能助手。我基于企业知识库为您提供问答服务。\n\n"
                "目前系统运行在演示模式下（未配置OPENAI_API_KEY）。"
                "如需完整功能，请在后端配置OPENAI_API_KEY。\n\n"
                "您可以：\n1. 向我提问关于知识库的问题\n"
                "2. 使用Skills执行特定任务\n3. 查看Loop Engineering分析报告"
            )
        if "知识" in user_msg or "文件" in user_msg:
            return (
                "根据知识库中的信息，我已经检索到相关内容。"
                "请注意，当前为演示模式，实际回答需要配置 LLM API。\n\n"
                "如需启用完整功能，请在设置页配置模型 API Key 并切换为对应 provider。"
            )
        return (
            f"收到您的问题：「{user_msg[:50]}...」\n\n"
            "当前系统运行在演示模式下。如需获得真实的 AI 回答，请先配置模型 API Key。"
        )

    # -- 统一入口 ---------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tier: str = ModelTier.DEFAULT,
        stats: Optional[LLMUsageStats] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        task_type: Optional[TaskType] = None,
        byok: Optional[ByokProfile] = None,
    ) -> str:
        """同步聊天接口（多模型路由 + 故障转移 + 成本追踪）。

        Args:
            task_type: 显式指定任务类型（决策四矩阵）；不给则由 tier 推导
            byok: 调用点级企业私有凭证，优先级高于会话级 BYOK
        """
        if self._use_fallback or not self._litellm_available:
            logger.info("使用 fallback 模式（未配置 API 密钥或 litellm 不可用）")
            if stats:
                stats.model_used = "fallback"
            return self._generate_fallback_response(messages)

        effective_task = task_type or registry.task_type_for_tier(tier)
        candidates = self._router.build_candidates(
            task_type=effective_task,
            tier=tier,
            explicit_model=model,
            explicit_key=api_key,
            explicit_base=api_base,
            byok=byok,
            service_model=self.model,
            service_key=self.api_key,
        )
        # attempted 而非 idx：跳过「未配置凭证」的候选不算故障转移，
        # 只有真正发起过调用并失败才应置 fallback_used。
        attempted = 0

        last_error: Optional[Exception] = None
        for idx, candidate in enumerate(candidates):
            if not candidate.usable:
                logger.debug(
                    "跳过候选 %s（无可用凭证: %s）", candidate.model, candidate.reason
                )
                continue
            attempted += 1
            try:
                response = await self._completion_with_resilience(
                    model=candidate.model,
                    messages=messages,
                    api_key=candidate.api_key,
                    api_base=candidate.api_base,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=settings.LLM_REQUEST_TIMEOUT,
                )
                if stats:
                    self._fill_stats(stats, response, candidate.model, attempted > 1)
                if attempted > 1:
                    logger.warning("主模型失败，已故障转移到: %s", candidate.model)
                return response.choices[0].message.content
            except Exception as error:  # noqa: BLE001
                last_error = error
                errors_total.labels(module=__name__, exception_type=type(error).__name__).inc()
                logger.warning(
                    "模型 %s 调用失败 (attempt %s/%s): %s",
                    candidate.model, idx + 1, len(candidates), error,
                )
                continue

        logger.error("所有 LLM 模型调用失败，最后错误: %s", last_error)
        if not settings.LLM_ALLOW_FALLBACK:
            raise RuntimeError("LLM 服务暂时不可用，所有可用模型均调用失败") from last_error
        if stats:
            stats.model_used = "fallback"
        return self._generate_fallback_response(messages)

    def _fill_stats(
        self, stats: LLMUsageStats, response: Any, model: str, fell_back: bool
    ) -> None:
        """填充 token/成本统计；成本计算失败不阻断主流程。"""
        stats.model_used = model
        stats.fallback_used = fell_back
        usage = getattr(response, "usage", None)
        if usage:
            stats.prompt_tokens = usage.prompt_tokens or 0
            stats.completion_tokens = usage.completion_tokens or 0
            stats.total_tokens = usage.total_tokens or 0
        try:
            cost = self._litellm.completion_cost(completion_response=response)
            stats.cost_usd = float(cost) if cost else 0.0
        except Exception as cost_err:  # noqa: BLE001
            errors_total.labels(
                module=__name__, exception_type=type(cost_err).__name__
            ).inc()
            logger.warning("计算 LLM 调用成本失败", exc_info=True)

    async def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tier: str = ModelTier.DEFAULT,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        task_type: Optional[TaskType] = None,
        byok: Optional[ByokProfile] = None,
    ) -> AsyncIterator[str]:
        """流式聊天接口。

        流式**不做故障转移**：一旦已输出 token，切换模型会造成内容重复。
        仅在建立流之前允许在候选链内重试。
        """
        if self._use_fallback or not self._litellm_available:
            for char in self._generate_fallback_response(messages):
                yield char
            return

        effective_task = task_type or registry.task_type_for_tier(tier)
        candidates = [
            c for c in self._router.build_candidates(
                task_type=effective_task,
                tier=tier,
                explicit_model=model,
                explicit_key=api_key,
                explicit_base=api_base,
                byok=byok,
                service_model=self.model,
                service_key=self.api_key,
                service_base=self.api_base,
            )
            if c.usable
        ]
        if not candidates:
            if not settings.LLM_ALLOW_FALLBACK:
                raise RuntimeError("LLM 流式服务不可用：无可用模型的可用凭证")
            for char in self._generate_fallback_response(messages):
                yield char
            return

        last_error: Optional[Exception] = None
        for candidate in candidates:
            try:
                stream = await self._completion_with_resilience(
                    model=candidate.model,
                    messages=messages,
                    api_key=candidate.api_key,
                    api_base=candidate.api_base,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    timeout=settings.LLM_REQUEST_TIMEOUT,
                )
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    content = getattr(chunk.choices[0].delta, "content", None) or ""
                    if content:
                        yield content
                return
            except Exception as error:  # noqa: BLE001
                last_error = error
                logger.warning("流式候选 %s 建立失败，尝试下一个", candidate.model)
                continue

        errors_total.labels(module=__name__, exception_type=type(last_error).__name__).inc()
        if not settings.LLM_ALLOW_FALLBACK:
            raise RuntimeError("LLM 流式服务暂时不可用") from last_error
        for char in self._generate_fallback_response(messages):
            yield char

    async def chat_with_image(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> str:
        """多模态聊天（图片输入）。默认使用 settings.MULTIMODAL_MODEL。"""
        if self._use_fallback or not self._litellm_available:
            return "图片分析功能需要配置多模态 LLM API。当前为演示模式。"

        vision_model = model or settings.MULTIMODAL_MODEL
        key, base = resolve_credentials(
            vision_model, explicit_key=api_key, explicit_base=api_base
        )
        try:
            response = await self._completion_with_resilience(
                model=vision_model,
                messages=messages,
                api_key=key,
                api_base=base,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=settings.LLM_REQUEST_TIMEOUT,
            )
            return response.choices[0].message.content
        except Exception as error:  # noqa: BLE001
            errors_total.labels(module=__name__, exception_type=type(error).__name__).inc()
            logger.error("多模态 LLM 调用失败: %s", error)
            if not settings.LLM_ALLOW_FALLBACK:
                raise RuntimeError("多模态 LLM 服务暂时不可用") from error
            return "图片分析功能暂时不可用，请稍后重试。"

    # -- 可观测 -----------------------------------------------------------

    def describe_route(
        self, task_type: TaskType = TaskType.CONVERSATION, tier: str = ModelTier.DEFAULT
    ) -> list[dict]:
        """当前路由链快照（不含凭证），供设置页与排障使用。"""
        return self._router.describe_route(task_type=task_type, tier=tier)


# 全局单例
llm_service = LLMService()
