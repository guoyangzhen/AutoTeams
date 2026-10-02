"""AutoTeams 5.0 战役 2：分层长程认知记忆中枢模型（battle 2 §2 三层记忆存储架构）。

三层结构：
- Layer 1 EpisodicTrace（情景剧集记忆）：协作时间序列快照 + 因果链条（Caused-By）
  + 反思与修正（reflection_notes）。
- Layer 2 语义实体记忆复用 cognition.knowledge_graph（KnowledgeGraph），不重复建模。
- Layer 3 ProceduralGene（规程记忆）：由高绩效情景轨迹蒸馏出的自愈规程经验基因卡。

表结构严格对齐迁移
  2026_09_27_1000-b5c6d7e8f9a0_add_cognitive_memory_tables.py
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import backref, relationship

from app.database import Base
from app.utils.time import utcnow


class EpisodicTrace(Base):
    """情景因果链轨迹（Layer 1 Episodic Memory）。

    一行 = 一次协作执行的时间序列快照。caused_by_event_id 指向触发本次执行的
    上游事件，构成可多跳回放的因果图（caused_by 边）。
    """
    __tablename__ = "episodic_traces"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 执行该次协作的数字员工工号（如 ATE-F-0001）
    badge = Column(String(64), nullable=True, index=True)
    # 因果父事件：触发本次执行的根因 trace id
    caused_by_event_id = Column(
        String(36), ForeignKey("episodic_traces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_summary = Column(Text, nullable=False)
    # 时间序列快照：[{step, action, actor, note}, ...]
    causal_chain_json = Column(JSON, nullable=False, default=list)
    # 反思与修正笔记（事后复盘写入）
    reflection_notes = Column(Text, nullable=True)
    # 绩效评分 0..1，作为经验蒸馏的筛选依据
    outcome_score = Column(Float, nullable=False, default=0.0)
    # 检索用上下文标签（流程域 / 客户 / 合规条款）
    context_tags = Column(JSON, nullable=False, default=list)
    # 完整步骤明细（可含耗时、工具调用等，检索时只索引摘要）
    steps_detail = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_episodic_traces_enterprise_created", "enterprise_id", "created_at"),
        Index("ix_episodic_traces_enterprise_badge", "enterprise_id", "badge"),
    )

    children = relationship(
        "EpisodicTrace",
        backref=backref("parent", remote_side="EpisodicTrace.id"),
        cascade="all, delete-orphan",
        single_parent=True,
    )

    def __repr__(self) -> str:
        return f"<EpisodicTrace id={self.id} badge={self.badge} score={self.outcome_score}>"


class ProceduralGene(Base):
    """自愈规程经验基因卡（Layer 3 Procedural Memory）。

    由 consolidate_procedural_memory 从 N 次高绩效情景轨迹中蒸馏：
    trigger_pattern 是可复用的触发条件，successful_sop_patch 是被验证有效的规程补丁，
    confidence_rating 随支持样本数与平均绩效收敛。
    """
    __tablename__ = "procedural_genes"

    gene_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 归属工号：None 表示企业级通用规程
    badge = Column(String(64), nullable=True, index=True)
    trigger_pattern = Column(Text, nullable=False)
    successful_sop_patch = Column(Text, nullable=False)
    confidence_rating = Column(Float, nullable=False, default=0.0)
    # 支撑该基因的高绩效轨迹数（置信度随此增长）
    support_count = Column(Integer, nullable=False, default=0)
    # 蒸馏来源轨迹 id（caused_by 之外的 distilled_into 边来源）
    source_trace_ids = Column(JSON, nullable=False, default=list)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_procedural_genes_enterprise_badge", "enterprise_id", "badge"),
    )

    def __repr__(self) -> str:
        return f"<ProceduralGene gene_id={self.gene_id} conf={self.confidence_rating}>"
