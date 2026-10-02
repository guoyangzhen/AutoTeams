"""数字员工能力模板（AgentTemplate）模型。

预置垂直岗位模板（客服/销售/HR/运营），企业「填空」即用：
- system_prompt：中文专业 prompt，符合演示要求
- skill_ids：关联的 SkillTemplate 标识列表（JSON）
- knowledge_structure：推荐的知识库结构（JSON）
- sample_dialogues：示例对话（JSON）

支持两类模板：
- is_preset=True：平台预置，所有企业可见（enterprise_id 为空）
- is_preset=False：企业私有模板（enterprise_id 指向所属企业）
"""
import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, JSON
from app.database import Base
from app.utils.time import utcnow


class AgentTemplate(Base):
    __tablename__ = "agent_templates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # 岗位角色：customer_service / sales / hr / ops / finance / medical / education / legal
    role = Column(String(32), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # 中文专业 system_prompt（预置）
    system_prompt = Column(Text, nullable=False)
    # 关联的 SkillTemplate 标识列表（JSON: ["skill_tpl_xxx", ...]）
    skill_ids = Column(JSON, nullable=True, default=list)
    # 推荐知识库结构（JSON: {"categories": [...], "file_types": [...]}）
    knowledge_structure = Column(JSON, nullable=True, default=dict)
    # 示例对话（JSON: [{"user": "...", "assistant": "..."}]）
    sample_dialogues = Column(JSON, nullable=True, default=list)
    # 平台预置 / 企业私有
    is_preset = Column(Boolean, default=False, nullable=False, index=True)
    # 企业私有模板时指向所属企业；预置模板为空
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "skill_ids": self.skill_ids or [],
            "knowledge_structure": self.knowledge_structure or {},
            "sample_dialogues": self.sample_dialogues or [],
            "is_preset": self.is_preset,
            "enterprise_id": self.enterprise_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
