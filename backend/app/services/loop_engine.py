"""Loop Engine 反馈循环引擎 —— 稳定入口（薄门面）。

实现已按职责拆分到 ``app/services/loop/`` 包，本文件只负责两件事：

1. 维持历史导入路径 ``from app.services.loop_engine import loop_engine``
   （``api/loop.py``、``services/scheduler_service.py``、
   ``services/evolution/continuous_optimizer.py`` 与多个测试都依赖它）；
2. 创建全局单例 ``loop_engine``。

公开方法一览（拆分后全部保留，签名与行为不变）：

===============================  =========================================
方法                              所属模块
===============================  =========================================
``analyze_feedback``              ``loop/insights.py``
``analyze_knowledge_gaps``        ``loop/insights.py``
``optimize_retrieval``            ``loop/engine.py``
``apply_optimization``            ``loop/engine.py``
``auto_rollback_if_degraded``     ``loop/evaluation.py``
``get_loop_stats``                ``loop/analytics.py``
``get_loop_status``               ``loop/analytics.py``
===============================  =========================================

.. note::
   ``llm_service`` / ``VectorStoreService`` 在此显式再导出：测试通过
   ``patch("app.services.loop_engine.llm_service.chat")`` 与
   ``patch("app.services.loop_engine.VectorStoreService.create")`` 打桩。
   两者是 Python 模块单例，再导出的即实现模块所持有的同一对象，patch 依然生效。
"""
from app.services.llm_service import llm_service
from app.services.loop.alerts import LoopAlertsMixin
from app.services.loop.analytics import LoopAnalyticsMixin, _to_utc
from app.services.loop.engine import LoopCoreMixin, LoopEngine
from app.services.loop.evaluation import (
    AUTO_ROLLBACK_SCORE_THRESHOLD,
    AUTO_ROLLBACK_WINDOW_DAYS,
    LoopEvaluationMixin,
)
from app.services.loop.insights import (
    ANALYSIS_PROMPT,
    KNOWLEDGE_GAP_PROMPT,
    LoopInsightsMixin,
)
from app.services.vector_store import VectorStoreService

# 全局单例：全仓唯一（api/loop.py、scheduler_service、continuous_optimizer 共用）
loop_engine = LoopEngine()

__all__ = [
    "LoopEngine",
    "loop_engine",
    # 兼容导出：Prompt 模板与回滚阈值常量
    "ANALYSIS_PROMPT",
    "KNOWLEDGE_GAP_PROMPT",
    "AUTO_ROLLBACK_WINDOW_DAYS",
    "AUTO_ROLLBACK_SCORE_THRESHOLD",
    # 兼容导出：测试打桩目标（见上方 note）
    "llm_service",
    "VectorStoreService",
    # 工具函数
    "_to_utc",
    # 组合用 mixin（供 isinstance/扩展判断）
    "LoopInsightsMixin",
    "LoopCoreMixin",
    "LoopEvaluationMixin",
    "LoopAnalyticsMixin",
    "LoopAlertsMixin",
]
