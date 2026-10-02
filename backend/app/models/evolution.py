"""WT4 进化层模型 —— AI Advisor 建议 + AI 组织分析指标。

依据：重构方案_v3.md §7.7 + spec.md §10.4/§10.6/§10.7。
表结构严格匹配 WT6 已创建的迁移：
  2026_07_29_0314-a8b9c0d1f4e8_add_evolution_tables.py
"""
import uuid

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON

from app.database import Base
from app.utils.time import utcnow


# 建议类型字面量（与 schema 一致）
SuggestionTypeLiteral = ("knowledge", "process", "capability", "organization")
# 建议状态
SuggestionStatusLiteral = ("pending", "applied", "rejected")
# 指标类型（5 类 + 成熟度）
MetricTypeLiteral = (
    "agent_workload",
    "process_efficiency",
    "tool_usage",
    "business_impact",
    "maturity",
)


class AdvisorSuggestion(Base):
    """AI 优化顾问建议（PRD §5.4）。

    4 类建议：knowledge（知识补充）/ process（流程优化）/
    capability（能力增强）/ organization（组织调整）。
    状态机：pending → applied / rejected（MVP 仅"建议→确认→应用"）。
    """
    __tablename__ = "advisor_suggestions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 建议类型：knowledge / process / capability / organization
    type = Column(String(32), nullable=False)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=False)
    # 预期效果/影响（自由文本）
    impact = Column(Text, nullable=True)
    # 状态：pending / applied / rejected
    status = Column(String(16), nullable=False, server_default="pending")
    applied_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return f"<AdvisorSuggestion type={self.type} status={self.status}>"


class OrgMetrics(Base):
    """AI 组织分析指标记录（PRD §5.5）。

    每次指标计算追加一行，保留历史轨迹便于趋势分析。
    metric_value 为 JSON：{value, unit, details, ...}（5 类指标 + 成熟度评级数据）。
    """
    __tablename__ = "org_metrics"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 指标类型：agent_workload / process_efficiency / tool_usage / business_impact / maturity
    metric_type = Column(String(32), nullable=False)
    # 指标值（JSON：{value, unit, details, ...}）
    metric_value = Column(JSON, nullable=False, default=dict)
    period = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return f"<OrgMetrics type={self.metric_type} period={self.period}>"
