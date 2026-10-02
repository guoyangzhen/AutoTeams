import uuid
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.database import Base
from app.utils.time import utcnow


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(String(36), ForeignKey("enterprises.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    # BE-REL-04: 构建时持久化 folder_path，避免依赖 File 记录推导
    folder_path = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    file_count = Column(Integer, default=0)
    knowledge_count = Column(Integer, default=0)
    status = Column(String, default="processing")
    # 新增：版本管理
    version = Column(String(32), default="1.0.0", nullable=False)
    # 新增：可调参数（temperature, top_k, max_tokens 等）
    config = Column(JSON, nullable=True, default=dict)
    # WT3 加性追加：生命周期阶段 + 记忆配置 + KPI 关联 + 岗位 ID
    # lifecycle_stage: recruit / training / production（MVP 3 阶段）
    lifecycle_stage = Column(String(32), nullable=True, server_default="recruit", index=True)
    # memory_config: JSON {short_term:{max_turns:20}, long_term:{enabled:true}, entity_memory:{enabled:true}}
    memory_config = Column(JSON, nullable=True)
    # kpi_ids: 关联的 KPI ID 列表（JSON array of strings）
    kpi_ids = Column(JSON, nullable=True)
    # position_id: 关联的岗位 ID（来自 Runtime 能力矩阵的 position_id）
    position_id = Column(String(64), nullable=True, index=True)
    # 3.4.3: 移除未使用的 metrics 字段（死代码，无任何读写点）
    # 历史快照保留在数据库中（由 migration 安全 drop）
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    enterprise = relationship("Enterprise", back_populates="agents")
    # BE-REL-02: 统一配置级联删除，避免 Agent 删除后留下孤儿数据
    files = relationship("File", back_populates="agent", cascade="all, delete-orphan")
    skills = relationship("Skill", backref="agent", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="agent", cascade="all, delete-orphan")
    versions = relationship("AgentVersion", back_populates="agent", foreign_keys="AgentVersion.agent_id", cascade="all, delete-orphan")
    optimization_history = relationship("OptimizationHistory", back_populates="agent", cascade="all, delete-orphan")
    processing_tasks = relationship("ProcessingTask", back_populates="agent")
    skill_executions = relationship("SkillExecution", back_populates="agent")
    task_plans = relationship("TaskPlan", back_populates="agent")
    # WT3 加性追加：Workforce 生命周期 + 记忆关系
    workforce_lifecycle = relationship("WorkforceLifecycle", back_populates="agent", cascade="all, delete-orphan")
    long_term_memories = relationship("LongTermMemory", back_populates="agent", cascade="all, delete-orphan")
    entity_memories = relationship("EntityMemory", back_populates="agent", cascade="all, delete-orphan")
