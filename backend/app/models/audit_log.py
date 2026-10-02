"""审计日志模型。

满足企业级安全合规要求：全操作留痕（谁、何时、调用了什么、输入输出）。
5.3.7: 审计日志 HMAC 链式防篡改 — 每条日志包含 prev_hash（上一条签名摘要）和
signature（本条内容的 HMAC-SHA256 签名），形成不可篡改的哈希链。
L3: User-Agent 字段在写入时截断至 255 字符，防止超长 UA 导致日志膨胀或注入。
"""
from sqlalchemy import Column, String, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base import TimestampMixin

# L3: User-Agent 最大长度限制（截断防止日志膨胀）
MAX_USER_AGENT_LENGTH = 255


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    # 3.2.2: created_at 索引，加速 _get_last_signature 的 ORDER BY DESC LIMIT 1 热路径
    __table_args__ = (
        Index('ix_audit_logs_created_at', 'created_at'),
    )

    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    # create / update / delete / login / logout / export / invite
    action = Column(String(64), nullable=False, index=True)
    # agent / enterprise / file / skill / user / conversation
    resource_type = Column(String(64), nullable=True, index=True)
    # 登录失败/账户锁定会把邮箱写进 resource_id（最长 254 字符），列宽必须容得下，
    # 否则这条安全事件会因为截断/超长而写不进去。
    resource_id = Column(String(255), nullable=True)
    # 客户端信息
    ip_address = Column(String(64), nullable=True)
    # L3: User-Agent 在 log_audit 中截断至 MAX_USER_AGENT_LENGTH
    user_agent = Column(String(255), nullable=True)
    # 详细信息（变更前后对比等）
    details = Column(JSON, nullable=True)
    # 5.3.7: HMAC 链式防篡改字段
    # prev_hash: 上一条审计日志的 signature（首条为固定 GENESIS 常量）
    prev_hash = Column(String(128), nullable=True)
    # signature: 本条日志内容的 HMAC-SHA256 签名（hex 摘要）
    signature = Column(String(128), nullable=True)

    user = relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "details": self.details,
            "prev_hash": self.prev_hash,
            "signature": self.signature,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
