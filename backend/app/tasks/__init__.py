"""Celery 异步任务包（AutoTeams 5.0 §4.2.4）。

把原先跑在 API 进程内的 CPU 密集任务（文档解析/切块/向量化）迁到独立 worker，
使处理时长与 API 进程解耦，并支持跨 Worker 的重试与状态共享。

模块划分：
- ``celery_app``          Celery 实例、Redis broker/backend、队列路由、beat 计划
- ``document_processing`` 文档解析 → 切块 → 向量化（@shared_task）
- ``scheduled_metrics``   定时日报汇总与平台健康度检查（@shared_task）

启动 worker：
    celery -A app.tasks.celery_app:celery_app worker -Q documents,scheduled -l info
    celery -A app.tasks.celery_app:celery_app beat -l info
"""
from app.tasks.celery_app import celery_app  # noqa: F401  —— 供 -A app.tasks.celery_app 使用

__all__ = ["celery_app"]
