"""WT3 记忆模型 —— 三层记忆架构（长期记忆 + 实体记忆）。

依据：重构方案_v3.md §6.7 + PRD §5.12。
表结构严格匹配 WT6 已创建的迁移：
  2026_07_29_0310-a6b7c8d9f2c6_add_memory_tables.py

注：短期记忆使用内存/Redis，不需要独立表。
"""
import uuid

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.time import utcnow


class LongTermMemory(Base):
    """长期记忆：交互摘要（向量化后写入 ChromaDB，本表记录摘要元数据）。

    流程：任务结束后 LLM 生成交互摘要 → 写入向量库 → 本表记录元数据 + vector_id。
    检索时通过 vector_id 从向量库语义检索。
    """
    __tablename__ = "long_term_memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    summary = Column(Text, nullable=False)
    # 向量库中的 ID（ChromaDB vector_id）
    vector_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # 关系（Enterprise 反向关系不定义：enterprise.py 不在本 WT 允许改动范围）
    agent = relationship("Agent", back_populates="long_term_memories")


class EntityMemory(Base):
    """实体记忆：客户/商机/产品关键属性（关联知识图谱）。

    存储每个 Agent 关联的实体关键属性（JSON），同时通过 GraphStore
    与知识图谱中的实体节点关联，支持实体 ID 查询和关系追溯。
    """
    __tablename__ = "entity_memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    # 实体类型：customer / opportunity / product / order / ...
    entity_type = Column(String(32), nullable=False)
    # 实体 ID（如 C-001 / OPP-001 / SL-T100）
    entity_id = Column(String(64), nullable=False)
    # 实体属性（JSON，关键属性键值对）
    attributes = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("agent_id", "entity_type", "entity_id", name="uq_entity_memory_agent_entity"),
    )

    # 关系（Enterprise 反向关系不定义：enterprise.py 不在本 WT 允许改动范围）
    agent = relationship("Agent", back_populates="entity_memories")
