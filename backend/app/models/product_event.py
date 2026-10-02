"""隐私优先的产品行为事件。

该表用于分析产品路径和转化，不替代安全审计日志：
- 不保存提示词、文件名、路径、业务正文、IP、User-Agent 或密钥；
- 仅保存企业/用户归属、匿名会话标识、受限事件名和无敏感属性；
- 安全关键操作仍必须写入 audit_logs。
"""
from sqlalchemy import Column, ForeignKey, Index, JSON, String

from app.database import Base
from app.models.base import TimestampMixin


class ProductEvent(Base, TimestampMixin):
    """企业级产品交互事件，用于漏斗和体验质量的聚合分析。"""

    __tablename__ = "product_events"
    __table_args__ = (
        Index("ix_product_events_enterprise_created", "enterprise_id", "created_at"),
        Index("ix_product_events_enterprise_name_created", "enterprise_id", "event_name", "created_at"),
        Index("ix_product_events_user_created", "user_id", "created_at"),
        Index("ix_product_events_session_created", "session_id", "created_at"),
    )

    enterprise_id = Column(
        String(36),
        ForeignKey("enterprises.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 用户删除后保留企业级聚合，但无法再还原至已删除账户。
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 浏览器会话随机标识，仅用于计算同一会话内的漏斗；不使用设备指纹。
    session_id = Column(String(64), nullable=False, index=True)
    event_name = Column(String(80), nullable=False, index=True)
    surface = Column(String(64), nullable=False, default="company_overview")
    journey_state = Column(String(64), nullable=True, index=True)
    action = Column(String(64), nullable=True, index=True)
    # 仅允许无敏感、低基数的枚举/布尔/数字属性；由 API schema 和 allow-list 校验。
    properties = Column(JSON, nullable=False, default=dict)
