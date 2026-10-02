"""共享模型基类与 Mixin。

之前版本定义了 TimestampMixin 但用 PostgreSQL UUID，且所有模型都没继承它，
导致死代码。这里用 String(36) UUID 保持与现有模型一致，供新模型复用。
"""
import uuid
from sqlalchemy import Column, String, DateTime
from app.utils.time import utcnow


class TimestampMixin:
    """通用主键与时间戳字段，供新模型继承。"""
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
