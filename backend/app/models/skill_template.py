"""技能模板（SkillTemplate）模型。

预置常见企业技能模板（合同提取/工单分类/周报生成/规格查询等），
配合 AgentTemplate 实现「填空即用」的数字员工能力组合。

SkillTemplate 是「模板」而非「实例」：
- apply_agent_template 时按 template_id 实例化为 Skill（写入 agents 关联）
- output_schema：声明该技能的输出结构，供链式编排时校验
"""
import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, JSON
from app.database import Base
from app.utils.time import utcnow


class SkillTemplate(Base):
    __tablename__ = "skill_templates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # 模板标识（语义化，供 AgentTemplate.skill_ids 引用，如 "ticket_classification"）
    code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    # 技能类型，对齐 Skill.skill_type（text_generation/text_summarization/text_classification/...）
    skill_type = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    # 实例化时写入 Skill.config 的模板配置（prompt_template/categories/fields 等）
    config = Column(JSON, nullable=True, default=dict)
    # 输出结构声明（JSON Schema 风格，供链式编排校验上下游兼容性）
    output_schema = Column(JSON, nullable=True, default=dict)
    # 平台预置 / 企业私有（与企业模板一致的设计）
    is_preset = Column(Boolean, default=False, nullable=False, index=True)
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
            "code": self.code,
            "name": self.name,
            "skill_type": self.skill_type,
            "description": self.description,
            "config": self.config or {},
            "output_schema": self.output_schema or {},
            "is_preset": self.is_preset,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
