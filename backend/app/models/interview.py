"""WT4 交互式企业访谈模型 —— 会话 + 问题记录。

依据：重构方案_v3.md §7.7 + PRD §5.7。
表结构严格匹配 WT6 已创建的迁移：
  2026_07_29_0316-a9b0c1d2f5e9_add_interview_tables.py

7 大类问题库：sales / customer_service / procurement / finance / hr / data / kpi
（详见 services/interview/question_bank.py）
"""
import uuid

from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.time import utcnow


# 会话状态
SessionStatusLiteral = ("active", "completed")
# 问题优先级
QuestionPriorityLiteral = ("P0", "P1", "P2")


class InterviewSession(Base):
    """交互式企业访谈会话（PRD §5.7）。

    一次访谈对应一个会话，渐进式追问直至运行模型完成度达标。
    回答触发运行模型更新 + 增量重编译（由 interview_engine 调用 WT1）。
    """
    __tablename__ = "interview_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 状态：active / completed
    status = Column(String(16), nullable=False, server_default="active")
    answered_count = Column(Integer, nullable=False, server_default="0")
    total_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    questions = relationship(
        "InterviewQuestion",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="InterviewQuestion.priority, InterviewQuestion.created_at",
    )

    def __repr__(self) -> str:
        return (
            f"<InterviewSession status={self.status} "
            f"answered={self.answered_count}/{self.total_count}>"
        )


class InterviewQuestion(Base):
    """访谈问题与回答记录（PRD §5.7）。

    每条问题含 category / question / expected_output / affected_field / priority。
    affected_field 指向运行模型中受回答影响的字段（用于触发增量重编译）。
    """
    __tablename__ = "interview_questions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(
        String(36), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 问题分类（7 大类）：sales / customer_service / procurement / finance / hr / data / kpi
    category = Column(String(32), nullable=False)
    question = Column(Text, nullable=False)
    expected_output = Column(Text, nullable=True)
    # 受影响的运行模型字段（点分路径，如 organization.departments / kpi.targets）
    affected_field = Column(String(128), nullable=True)
    # 优先级：P0 / P1 / P2
    priority = Column(String(4), nullable=False, server_default="P1")
    answer = Column(Text, nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    session = relationship("InterviewSession", back_populates="questions")

    def __repr__(self) -> str:
        return f"<InterviewQuestion category={self.category} priority={self.priority}>"
