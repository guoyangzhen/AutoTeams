"""Enterprise Runtime 数据模型（WT2）。

实现 PRD §4.6（Enterprise Runtime 数据结构）与 §4.6.3（版本管理）的持久层。

两张表的职责划分（避免冗余）：
- ``EnterpriseRuntime``（``enterprise_runtimes``）：Runtime 的**数据存储 + 版本存储**。
  每行 = 某企业的一个 Runtime 版本，持有完整的 ``runtime_data``（JSONB，含 PRD §4.6.1
  的 9 字段块）。``is_active`` 标记当前激活版本（每企业至多一个，由 service 层保证）。
  WT3/WT4 通过 ``runtime_query`` 读取本表。
- ``RuntimeVersion``（``runtime_versions``）：版本操作的**审计日志**。
  每行 = 一次版本事件（save/snapshot/rollback），记录 changelog、操作者、事件类型，
  通过 ``runtime_id`` 外键关联到具体 Runtime 版本。支撑 PRD §4.6.3 的回滚追溯。

存储说明：
- ``runtime_data`` 用 SQLAlchemy ``JSON`` 类型，在 PostgreSQL 上自动映射为 JSONB
  （spec §1.3 图存储选型：PG JSONB 为主），在 SQLite（开发/测试）上为 TEXT JSON，
  与现有 ``agent_version.config_snapshot`` 保持一致。
- ``enterprise_id`` 外键引用已存在的 ``enterprises`` 表（加性，不破坏现有表）。
- 迁移由 WT6 独占创建（见 ``.migration_request_wt2.md``），本文件仅定义模型。
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin
from app.utils.time import utcnow


class EnterpriseRuntime(Base, TimestampMixin):
    """Enterprise Runtime 版本（运行态可执行实例）。

    每行对应 PRD §4.6.1 的一个 Runtime 版本快照。``runtime_data`` JSONB 结构遵循
    spec.md §10.2 的 ``RuntimeCompileResult`` 契约（不含 ``audit_trail``，审计信息
    单独写入 ``runtime_versions``）。
    """

    __tablename__ = "enterprise_runtimes"
    __table_args__ = (
        # 每企业内版本号唯一
        UniqueConstraint("enterprise_id", "version", name="uq_runtime_enterprise_version"),
        # 按企业查询激活版本的索引
        Index("ix_runtime_enterprise_active", "enterprise_id", "is_active"),
    )

    # 所属企业（加性外键，引用现有 enterprises 表；企业删除时级联删除其 Runtime）
    enterprise_id = Column(
        String(36),
        ForeignKey("enterprises.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 语义化版本号，带 "v" 前缀（v1.0.0 / v1.1.0 / v2.0.0）
    version = Column(String(32), nullable=False)
    # 对应的企业运行模型版本（PRD §4.6.1 model_version，spec §10.2 契约非空）
    model_version = Column(String(32), nullable=False)
    # 编译时间
    compiled_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    # 编译时完成度（0-100）
    completeness = Column(Float, default=0.0, nullable=False)
    # Runtime 完整数据（JSONB，含 9 字段块：organization/agents/process_engines/
    # collaboration_graph/knowledge_index/tool_registry 等）
    runtime_data = Column(JSON, nullable=False)
    # 是否当前激活版本（每企业至多一个 is_active=True，由 service 层保证）
    is_active = Column(Boolean, default=False, nullable=False, index=True)
    # 创建者（触发编译/保存的用户；用户删除时置空以保留 Runtime 历史）
    created_by = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    enterprise = relationship("Enterprise")
    versions = relationship(
        "RuntimeVersion", back_populates="runtime", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "enterprise_id": self.enterprise_id,
            "version": self.version,
            "model_version": self.model_version,
            "compiled_at": self.compiled_at.isoformat() if self.compiled_at else None,
            "completeness": self.completeness,
            "runtime_data": self.runtime_data,
            "is_active": self.is_active,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RuntimeVersion(Base, TimestampMixin):
    """Runtime 版本操作审计日志。

    记录每次版本事件（创建快照 / 回滚 / 保存），支撑 PRD §4.6.3 的版本管理追溯。
    通过 ``runtime_id`` 关联到具体 Runtime 版本；``event_type`` 区分事件性质。
    """

    __tablename__ = "runtime_versions"
    __table_args__ = (
        Index("ix_runtime_version_enterprise", "enterprise_id", "created_at"),
    )

    # 关联的 Runtime 版本（BE-REL：Runtime 删除时级联删除版本事件日志）
    runtime_id = Column(
        String(36),
        ForeignKey("enterprise_runtimes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 冗余 enterprise_id 便于按企业直接查询事件流（避免每次 JOIN；
    # 企业删除时级联删除其版本事件日志）
    enterprise_id = Column(
        String(36),
        ForeignKey("enterprises.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 事件涉及的版本号（冗余，便于事件流展示）
    version = Column(String(32), nullable=False)
    # 事件类型：save（新版本保存）/ snapshot（快照）/ rollback（回滚）
    event_type = Column(String(32), nullable=False, default="save")
    # 变更说明
    changelog = Column(Text, nullable=True)
    # 该版本在事件发生时是否为激活版本（审计快照语义）
    is_active = Column(Boolean, default=False, nullable=False)
    # 操作者（用户删除时置空以保留版本事件历史）
    created_by = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # 附加元数据（如回滚的源/目标版本、受影响 Agent 数量等）
    metadata_json = Column("metadata", JSON, nullable=True)

    runtime = relationship("EnterpriseRuntime", back_populates="versions")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "runtime_id": self.runtime_id,
            "enterprise_id": self.enterprise_id,
            "version": self.version,
            "event_type": self.event_type,
            "changelog": self.changelog,
            "is_active": self.is_active,
            "created_by": self.created_by,
            "metadata": self.metadata_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
