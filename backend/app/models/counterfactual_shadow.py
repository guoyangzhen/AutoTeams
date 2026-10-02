"""双盲反事实影子评估模型 —— AutoTeams 5.0 战役 4。

与既有影子模式（``app.models.shadow.ShadowTask``）的分工：

- ``ShadowTask``：单条任务的「真人答案 vs AI 答案」比对，产出 match / mismatch。
- ``ShadowEvaluationSession``：一次**反事实推演会话**。同一业务场景下，真人实际处置
  （对照组）与数字员工提案（实验组）都被完整快照下来，再交给差分引擎做因果对比。
  差分是**双盲**的：打分前剥离身份标记，评分函数对两侧完全对称，
  评估器无法从文本中推断哪一侧是人、哪一侧是 Agent。

- ``CounterfactualDiff``：会话的逐维度差分明细（语义 / 时效 / 成本 / 风险），
  构成前端「双盲反事实差分对比瀑布」的数据源。

免干预自晋升：``consecutive_pass_streak`` 记录该数字员工（employee_badge）
连续达标的样本数；连续 ``PROMOTION_STREAK_THRESHOLD``（50）笔达标后自动转正，
无需任何人工干预。
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base
from app.utils.time import utcnow

# 差分维度（瀑布图纵轴顺序）
DIFF_DIMENSIONS = ("semantics", "latency", "cost", "risk")
# 差分评估结论
DIFF_ASSESSMENTS = ("better", "worse", "parity")


class ShadowEvaluationSession(Base):
    """一次双盲反事实影子评估会话。"""
    __tablename__ = "shadow_evaluation_sessions"

    session_id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    enterprise_id = Column(
        String(36),
        ForeignKey("enterprises.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 数字员工名牌（评估对象；按此维度累计连续达标）
    employee_badge = Column(String(64), nullable=False, index=True)
    # 业务场景描述（双盲提示词，双方共享同一场景）
    scenario = Column(Text, nullable=False)

    # ---- 双盲快照 ----
    # 对照组：真人实际处置快照
    human_action_snapshot = Column(Text, nullable=False)
    # 实验组：数字员工反事实提案快照
    agent_proposal_snapshot = Column(Text, nullable=False)

    # ---- 客观度量 ----
    # 真人处置耗时（秒）
    human_duration_seconds = Column(Float, nullable=False, default=0.0)
    # 数字员工处置耗时（秒）
    agent_duration_seconds = Column(Float, nullable=False, default=0.0)
    # 真人处置成本（元）
    human_cost_yuan = Column(Float, nullable=False, default=0.0)
    # 数字员工处置成本（元）
    agent_cost_yuan = Column(Float, nullable=False, default=0.0)

    # ---- 差分结论（由 counterfactual_evaluator 写入）----
    # 语义对齐度（0~1）
    semantic_alignment_score = Column(Float, nullable=False, default=0.0)
    # 时间得失（秒，正数表示数字员工更快）
    time_saving_seconds = Column(Float, nullable=False, default=0.0)
    # 成本得失（元，正数表示数字员工更贵；负数表示更省）
    cost_delta_yuan = Column(Float, nullable=False, default=0.0)
    # 反事实预期净收益（元）
    expected_net_benefit_yuan = Column(Float, nullable=False, default=0.0)
    # 护栏突破次数
    guardrail_breach_count = Column(Integer, nullable=False, default=0)
    # 本次样本是否达标
    is_qualified = Column(Boolean, nullable=False, default=False, index=True)

    # ---- 免干预自晋升 ----
    # 连续达标笔数（未达标即归零）
    consecutive_pass_streak = Column(
        Integer, nullable=False, default=0, index=True
    )
    # 免干预自动转正标记
    is_auto_promoted = Column(Boolean, nullable=False, default=False)
    promoted_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<ShadowEvaluationSession session_id={self.session_id} "
            f"badge={self.employee_badge} qualified={self.is_qualified}>"
        )


class CounterfactualDiff(Base):
    """反事实差分明细（会话的逐维度对比记录）。"""
    __tablename__ = "counterfactual_diffs"

    diff_id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id = Column(
        String(36),
        ForeignKey("shadow_evaluation_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 差分维度：semantics / latency / cost / risk
    dimension = Column(String(32), nullable=False)
    # 对照组读数（已脱敏，展示用）
    human_value = Column(Text, nullable=False, default="")
    # 实验组读数（已脱敏，展示用）
    agent_value = Column(Text, nullable=False, default="")
    # 归一化差值（正数表示数字员工占优）
    delta_score = Column(Float, nullable=False, default=0.0)
    # 结论：better / worse / parity
    assessment = Column(String(16), nullable=False, default="parity")
    # 人类可读说明
    note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return (
            f"<CounterfactualDiff session={self.session_id} "
            f"dim={self.dimension} delta={self.delta_score}>"
        )
