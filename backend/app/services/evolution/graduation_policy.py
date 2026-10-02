"""影子模式转正门槛（服务器政策，AUD-30）。

历史问题：`POST /evolution/workforce/{id}/graduate` 允许任意已登录成员自带
`min_samples` / `pass_rate_threshold`，且判定逻辑在"样本存在但都没有评估结果"
时把通过率算成 0.0 —— 提交 `min_samples=0, pass_rate_threshold=0` 就能让
没有任何考核证据的数字员工直接转正为 `production`。

本模块把门槛收敛为**服务器侧政策**：

* 门槛值来自部署配置（`Settings`），请求体只能表达"希望"，不能决定门槛；
* 每条政策带 `policy_version`，转正时写入生命周期轨迹，可追溯"按哪版政策放的"；
* 样本必须是**已完成评估且归属目标 Agent** 的影子任务，数量达标才算有效。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings

#: 政策版本号：门槛语义或取值发生变化时必须递增，并同步更新发布说明。
GRADUATION_POLICY_VERSION = "2026-09-28.1"

#: 判定为"有效考核样本"的评估结果。
VALID_EVAL_RESULTS = ("match", "mismatch")


@dataclass(frozen=True)
class GraduationPolicy:
    """影子转正门槛。"""

    min_samples: int
    pass_rate_threshold: float
    policy_version: str = GRADUATION_POLICY_VERSION

    def describe(self) -> str:
        return (
            f"政策 {self.policy_version}：至少 {self.min_samples} 个有效评估样本，"
            f"通过率 ≥ {self.pass_rate_threshold * 100:.0f}%"
        )


def load_graduation_policy() -> GraduationPolicy:
    """读取当前生效的转正政策。

    取值来自服务器配置；配置缺失或越界时回退到保守默认值，
    绝不因为请求参数而放宽。
    """
    try:
        min_samples = int(getattr(settings, "SHADOW_GRADUATION_MIN_SAMPLES", 5))
    except (TypeError, ValueError):
        min_samples = 5
    try:
        threshold = float(getattr(settings, "SHADOW_GRADUATION_PASS_RATE", 0.85))
    except (TypeError, ValueError):
        threshold = 0.85

    # 政策只允许收紧：门槛为 0 会让"零样本"变成"自动达标"。
    min_samples = max(1, min_samples)
    threshold = min(max(threshold, 0.01), 1.0)
    return GraduationPolicy(min_samples=min_samples, pass_rate_threshold=threshold)


__all__ = [
    "GRADUATION_POLICY_VERSION",
    "VALID_EVAL_RESULTS",
    "GraduationPolicy",
    "load_graduation_policy",
]
