"""统一的时间戳工具函数。

BE-MAINT-02: 替换散落在各模型中的 `lambda: datetime.now(timezone.utc)` 模式，
集中到单一函数，便于后续切换到 `server_default=func.now()` 或调整时区策略。

使用方式：
    from app.utils.time import utcnow
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回当前 UTC 时间（带时区信息）。

    作为 SQLAlchemy Column default/onupdate 的可调用对象，
    替代 `lambda: datetime.now(timezone.utc)`，避免在每个模型重复定义 lambda。
    """
    return datetime.now(timezone.utc)
