"""智能体版本模型。

支持 Agent 的版本管理与 A/B 测试，保存配置快照便于回滚。
"""
from sqlalchemy import Column, String, Text, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base import TimestampMixin


class AgentVersion(Base, TimestampMixin):
    __tablename__ = "agent_versions"

    # BE-REL-02: Agent 删除时级联删除版本快照
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    # 语义化版本号
    version = Column(String(32), nullable=False)
    # 配置快照：{system_prompt, skills, params}
    config_snapshot = Column(JSON, nullable=False)
    # 6.5: 知识库快照（D4 新增）
    # 结构：{file_list: [...], vector_collection: str, chunk_strategy: {...}, rag_config: {...}}
    # nullable=True 以兼容历史版本记录（D4 迁移前的数据无此字段）
    knowledge_snapshot = Column(JSON, nullable=True)
    # 变更说明
    changelog = Column(Text, nullable=True)
    # 是否当前激活版本
    is_active = Column(Boolean, default=False, nullable=False, index=True)
    # 创建者
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)

    agent = relationship("Agent", back_populates="versions")
    user = relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "version": self.version,
            "config_snapshot": self.config_snapshot,
            "knowledge_snapshot": self.knowledge_snapshot,
            "changelog": self.changelog,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
