"""WT4 进化层服务包（PRD §5.4/§5.5 + 重构方案 §7.6 阶段3）。

包含：
- advisor: AI 优化顾问（4 类建议 + 一键应用）
- org_analytics: AI 组织分析（5 类指标 + L1-L5 成熟度，MVP 目标 L2）
- continuous_optimizer: 持续优化（复用 loop_engine + rag_evaluator）
"""
from app.services.evolution.advisor import AdvisorService, advisor_service
from app.services.evolution.org_analytics import OrgAnalyticsService, org_analytics
from app.services.evolution.continuous_optimizer import (
    ContinuousOptimizer,
    continuous_optimizer,
)

__all__ = [
    "AdvisorService",
    "advisor_service",
    "OrgAnalyticsService",
    "org_analytics",
    "ContinuousOptimizer",
    "continuous_optimizer",
]
