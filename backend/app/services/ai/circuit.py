"""模型级熔断器（从 4.0 的 LLMService 内联逻辑抽出）。

行为契约（保持 4.0 语义不变）：
- 同一模型连续失败达到 ``LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD`` 次后进入冷却；
- 冷却期内对该模型**快速失败**（``LLMCircuitOpenError``），由调用方切换备用模型；
- 冷却结束或任意一次成功后立即恢复。

作用域：进程内。跨实例的集中式限流仍由网关/供应商侧负责。
"""
from __future__ import annotations

import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)


class LLMCircuitOpenError(RuntimeError):
    """候选模型在冷却窗口内已熔断。"""


class CircuitBreaker:
    """按模型名维护失败计数与冷却截止时间。"""

    def __init__(self) -> None:
        self._failure_streak: dict[str, int] = {}
        self._circuit_open_until: dict[str, float] = {}

    def is_open(self, model: str) -> bool:
        """该模型当前是否处于冷却期。"""
        return self._circuit_open_until.get(model, 0.0) > time.monotonic()

    def record_success(self, model: str) -> None:
        """成功即清零该模型的所有失败状态。"""
        self._failure_streak.pop(model, None)
        self._circuit_open_until.pop(model, None)

    def record_failure(self, model: str) -> None:
        """累计失败；达到阈值则开启冷却。"""
        failures = self._failure_streak.get(model, 0) + 1
        self._failure_streak[model] = failures
        if failures >= settings.LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            self._circuit_open_until[model] = (
                time.monotonic() + settings.LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS
            )
            logger.warning(
                "LLM 模型熔断: model=%s cooldown=%ss",
                model,
                settings.LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
            )

    def streak(self, model: str) -> int:
        """当前连续失败次数（供测试与监控读取）。"""
        return self._failure_streak.get(model, 0)

    def reset(self) -> None:
        """清空全部熔断状态（供测试隔离用）。"""
        self._failure_streak.clear()
        self._circuit_open_until.clear()
