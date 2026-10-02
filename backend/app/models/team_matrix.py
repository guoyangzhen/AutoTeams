"""AutoTeams 4.0 矩阵团队协同与共享黑板模型（Team-Matrix）。

定义项目制数字员工工作组、任务生命周期、3轮血条竞聘选拔与团队共享黑板活文档。
"""
from __future__ import annotations

import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Integer, Float, Boolean
from app.database import Base
from app.utils.time import utcnow


class WorkgroupTeam(Base):
    """协同工作组表。"""
    __tablename__ = "workgroup_teams"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    leader_profile_id = Column(String(36), nullable=False)  # 项目领导/协调人数字员工 ID
    member_profile_ids = Column(JSON, nullable=False, default=list)  # 团队成员数字员工 ID 清单
    config = Column(JSON, nullable=False, default=lambda: {
        "concurrency_limit": 3,
        "bid_rounds": 3,
        "timeout_seconds": 1800,
    })
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<WorkgroupTeam id={self.id} name={self.name}>"


class MatrixTask(Base):
    """工作组协同拆解任务表。"""
    __tablename__ = "matrix_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String(36), ForeignKey("workgroup_teams.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_task_id = Column(String(36), nullable=True, index=True)  # 递归任务拆解层级
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=False)
    priority = Column(Integer, nullable=False, default=1)
    # 任务状态机：pending -> bidding -> in_progress -> review -> done / rework / escalated
    status = Column(String(32), nullable=False, default="pending")
    suggested_profile_id = Column(String(36), nullable=True)
    assignee_profile_id = Column(String(36), nullable=True)  # 中标执行数字员工 ID
    deliverable_report = Column(JSON, nullable=True)         # 交付三件套报告
    review_feedback = Column(JSON, nullable=True)            # Leader 验收意见
    version = Column(Integer, nullable=False, default=1)     # 乐观锁
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<MatrixTask id={self.id} title={self.title} status={self.status}>"


class TaskSelectionBid(Base):
    """3 轮血条竞聘选拔记录表。"""
    __tablename__ = "task_selection_bids"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String(36), ForeignKey("matrix_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_profile_id = Column(String(36), nullable=False, index=True)
    bid_round = Column(Integer, nullable=False, default=1)  # 1=方案陈述, 2-3=反驳与细化
    statement = Column(Text, nullable=False)                # 胜任理由与方案陈述
    score = Column(Float, nullable=True)                    # Leader 轻量打分 (0-10)
    score_rationale = Column(Text, nullable=True)           # 打分理由
    current_hp = Column(Float, nullable=False, default=100.0) # 扣减后的剩余生命值
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return f"<TaskSelectionBid task={self.task_id} candidate={self.candidate_profile_id} round={self.bid_round} hp={self.current_hp}>"


class SharedBlackboardEntry(Base):
    """团队共享黑板（Blackboard）活文档表。"""
    __tablename__ = "shared_blackboard_entries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(String(36), ForeignKey("workgroup_teams.id", ondelete="CASCADE"), nullable=False, index=True)
    topic = Column(String(128), nullable=False, index=True)
    content = Column(Text, nullable=False)
    source_profile_id = Column(String(36), nullable=False)
    source_task_id = Column(String(36), nullable=True)
    citations = Column(JSON, nullable=False, default=list)  # 回链任务交付物与知识依据
    is_pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<SharedBlackboardEntry topic={self.topic} team={self.team_id}>"
