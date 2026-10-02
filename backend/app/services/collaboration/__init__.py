"""WT4 协作服务包（PRD §5.8/§5.9/§5.10）。

包含：
- event_bus: 事件驱动协作总线（7 步演示案例）
- human_ai_collaboration: 人机协作模式（MVP 2 种）
- error_handler: 错误处理与降级（MVP 2 类 + 三级恢复）
- rollback: 回滚机制（操作前快照 + 流程级回滚）
"""
from app.services.collaboration.event_bus import EventBus, event_bus
from app.services.collaboration.human_ai_collaboration import (
    HumanAICollaboration,
    human_ai_collaboration,
)
from app.services.collaboration.error_handler import ErrorHandler, error_handler
from app.services.collaboration.rollback import RollbackManager, rollback_manager

__all__ = [
    "EventBus",
    "event_bus",
    "HumanAICollaboration",
    "human_ai_collaboration",
    "ErrorHandler",
    "error_handler",
    "RollbackManager",
    "rollback_manager",
]
