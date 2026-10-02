import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON
from app.database import Base
from app.utils.time import utcnow


class Skill(Base):
    __tablename__ = "skills"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    skill_type = Column(String(50), nullable=False)
    input_type = Column(String(50), nullable=True)
    output_type = Column(String(50), nullable=True)
    config = Column(JSON, nullable=True, default=dict)

    # P1-SKILL: 自主 Skill 生态字段
    # 来源：manual（手动创建）/ generated（AI 生成）/ imported（外部导入）
    source = Column(String(32), default="manual", nullable=False)
    # 状态：draft / pending / approved / rejected
    # 手动创建默认 approved；生成/导入默认 pending，需经安全筛查与人工审批
    status = Column(String(32), default="approved", nullable=False)
    # 生成依据（仅 generated）：触发原因、样本查询、知识缺口等
    generation_context = Column(JSON, nullable=True)
    # 安全筛查结果（pending 时写入）
    review_result = Column(JSON, nullable=True)
    # 技能声明的权限列表（用于运行时授权校验）
    permissions = Column(JSON, nullable=True, default=list)

    # B6 数字员工模板：加性迁移字段（不破坏现有数据，均可空）
    # 来源模板（SkillTemplate.code），套用 AgentTemplate 时写入；手动/生成技能为空
    template_id = Column(String(64), nullable=True, index=True)
    # 链式编排：下一个技能的 ID（可空，链尾为 None）
    # 上一步输出作为下一步输入，聚合为最终结构化输出
    next_skill_id = Column(String(36), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
