"""RAG 自动评估模型。

每次对话后基于检索结果与生成回答，计算 faithfulness、answer_relevancy、
context_precision 等标准化指标，用于 LoopDashboard 质量趋势展示。
"""
import uuid
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from app.database import Base
from app.utils.time import utcnow


class RAGEvaluation(Base):
    __tablename__ = "rag_evaluations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id = Column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)

    # 标准化 RAG 指标（0-1）
    faithfulness = Column(Float, default=0.0, nullable=False)        # 回答对检索内容的忠实度
    answer_relevancy = Column(Float, default=0.0, nullable=False)    # 回答与用户问题的相关性
    context_precision = Column(Float, default=0.0, nullable=False)   # 检索结果中相关片段的比例
    context_recall = Column(Float, default=0.0, nullable=False)      # 回答问题所需信息被召回的比例

    # 原始评分细节：检索结果数、相关片段数、使用模型等
    details = Column(JSON, nullable=True, default=dict)
    evaluated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    agent = relationship("Agent", backref="rag_evaluations")
    conversation = relationship("Conversation", backref="rag_evaluations")
    message = relationship("Message", backref="rag_evaluation")

    __table_args__ = (
        Index("idx_rag_eval_agent_evaluated", "agent_id", "evaluated_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "faithfulness": round(self.faithfulness, 4),
            "answer_relevancy": round(self.answer_relevancy, 4),
            "context_precision": round(self.context_precision, 4),
            "context_recall": round(self.context_recall, 4),
            "details": self.details,
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
        }
