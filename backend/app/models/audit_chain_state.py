"""审计日志哈希链的全局状态。

该表固定保留一行（``id='global'``）。写入审计日志时，事务先对该行加
``FOR UPDATE`` 锁，再读取/更新 ``last_signature``。这样多个 API/Worker 进程共享
同一个链尾，避免进程内缓存导致的分叉链。
"""
from sqlalchemy import Column, String

from app.database import Base
from app.models.base import TimestampMixin


class AuditChainState(Base, TimestampMixin):
    """全局审计链尾指针，必须由迁移创建并初始化。"""

    __tablename__ = "audit_chain_state"

    # 固定单例主键，便于在所有支持行锁的数据库中锁定同一逻辑资源。
    id = Column(String(36), primary_key=True, default="global")
    last_signature = Column(String(128), nullable=False)
