"""影子模式 ShadowTask 模型 —— 数字员工信任建立过程。

依据：愿景蓝图 §4.1.1 + 产品完善方案_v3.2 §8.3 补1。
状态机：shadowing → evaluating → qualified → autonomous
- shadowing   ：AI 静默观察，记录 (问题, 真人答案) 基线，不对外输出
- evaluating  ：AI 输出建议并记录置信度，与基线比对，产生 eval_result
- qualified   ：达到准确率/置信度阈值，等待人类授权晋升
- autonomous  ：AI 自主处理，5% 抽样审计，异常可降级回 evaluating
"""
import uuid

from sqlalchemy import Column, String, Text, DateTime, Float, ForeignKey

from app.database import Base
from app.utils.time import utcnow


# 影子任务状态（状态机）
ShadowStatusLiteral = ("shadowing", "evaluating", "qualified", "autonomous")
# 评估结果
ShadowEvalLiteral = ("pending", "match", "mismatch")


class ShadowTask(Base):
    """影子模式任务：一次 (问题, AI 回答, 真人回答) 的信任建立样本。"""
    __tablename__ = "shadow_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id = Column(
        String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 任务类型：inquiry / quotation / customer_service / after_sales / ...
    task_type = Column(String(64), nullable=False)
    # 输入问题（业务场景描述）
    question = Column(Text, nullable=False)
    # 真人回答（基线，影子模式阶段记录）
    human_answer = Column(Text, nullable=True)
    # AI 回答（评估/自主阶段生成）
    ai_answer = Column(Text, nullable=True)
    # AI 置信度（0~1）
    confidence = Column(Float, nullable=True)
    # 状态机：shadowing / evaluating / qualified / autonomous
    status = Column(String(16), nullable=False, server_default="shadowing")
    # 评估结果：pending / match / mismatch
    eval_result = Column(String(16), nullable=True, server_default="pending")
    # 晋升/降级时间
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<ShadowTask id={self.id} type={self.task_type} status={self.status}>"
