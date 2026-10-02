"""WT3 Workforce 模型 —— AI 数字员工生命周期管理。

依据：重构方案_v3.md §6.7 + spec.md §10.6。
表结构严格匹配 WT6 已创建的迁移：
  2026_07_29_0308-a5b6c7d8f1b5_add_workforce_tables.py
"""
import uuid

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Float
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.time import utcnow


class WorkforceLifecycle(Base):
    """Workforce 生命周期记录表。

    每次 Agent 阶段转换追加一行，完整记录生命周期轨迹。
    MVP 3 阶段：recruit → training → production
    （完整 9 阶段在 P1 迭代：certification/shadow/evaluation/continuous/promotion/retired）
    """
    __tablename__ = "workforce_lifecycle"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    # 生命周期阶段：recruit / training / production（MVP）/ evaluation / continuous_learning / promotion / retired（P1）
    stage = Column(String(32), nullable=False)
    stage_entered_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    transition_reason = Column(Text, nullable=True)
    # 注意：DB 列名为 metadata，Python 属性用 meta 避免与 Base.metadata 冲突
    meta = Column("metadata", JSON, nullable=True)

    # 关系（Enterprise 反向关系不定义：enterprise.py 不在本 WT 允许改动范围）
    agent = relationship("Agent", back_populates="workforce_lifecycle")

    def __repr__(self) -> str:
        return f"<WorkforceLifecycle agent_id={self.agent_id} stage={self.stage}>"


class WorkforceProfile(Base):
    """AutoTeams 4.0 数字员工岗位名牌与档案卡。

    定义具有真实组织编制的数字员工实体：工号、职务、所属部门、
    岗位职责边界（可做/禁止）、能力与工具授权清单以及动态绩效。
    """
    __tablename__ = "workforce_profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    employee_badge = Column(String(48), unique=True, nullable=False, index=True)
    display_name = Column(String(64), nullable=False)
    job_title = Column(String(64), nullable=False)
    department = Column(String(64), nullable=False, default="通用业务部")
    duty_boundaries = Column(JSON, nullable=False, default=lambda: {"allowed": [], "forbidden": []})
    tone_style = Column(String(64), nullable=False, default="professional")

    # 能力引用授权清单（最小特权原则）
    authorized_flows = Column(JSON, nullable=False, default=list)
    accessible_knowledge_buckets = Column(JSON, nullable=False, default=list)
    authorized_tools = Column(JSON, nullable=False, default=list)

    # 状态与考评
    employment_status = Column(String(32), nullable=False, default="shadow")  # shadow / active / suspended / retired
    performance_score = Column(Float, nullable=False, default=100.0)
    avatar_url = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<WorkforceProfile badge={self.employee_badge} name={self.display_name} job={self.job_title}>"

