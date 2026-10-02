"""WT3 Workforce 服务包（PRD §5.3/§5.6/§5.11）。

包含：
- position_matcher: 6 维度岗位匹配
- generator: AI 员工 6 步生成流程
- lifecycle_manager: MVP 3 阶段生命周期管理
- orchestrator: MVP 简化版 Workforce Orchestrator
"""
from app.services.workforce.position_matcher import PositionMatcher
from app.services.workforce.generator import WorkforceGenerator
from app.services.workforce.lifecycle_manager import LifecycleManager
from app.services.workforce.orchestrator import WorkforceOrchestrator

__all__ = [
    "PositionMatcher",
    "WorkforceGenerator",
    "LifecycleManager",
    "WorkforceOrchestrator",
]
