"""AgentKPI 绩效评估模型。

按天/周/月聚合每个智能体的业务绩效指标，支撑「业务效果仪表盘」
向企业决策者呈现 ROI / 成本 / 覆盖 / 效率等业务价值数据。

设计说明：
- 聚合结果以「物化思路」落库，避免实时聚合在大数据量下产生压力；
  当前先由 metrics_service 实时聚合接口提供数据，本表为后台定时聚合
  落库的载体（后续优化项），二者数据口径保持一致。
- enterprise_id 建索引，支持多租户按企业维度的绩效汇总查询。
- unique(agent_id, period_type, period_start) 防止同一周期重复落库覆盖。
"""
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, JSON, Index, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin


class AgentKPI(Base, TimestampMixin):
    __tablename__ = "agent_kpis"

    # 归属：智能体 + 企业（企业隔离查询）
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)

    # 周期：daily / weekly / monthly
    period_type = Column(String(16), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)

    # 效率指标
    task_count = Column(Integer, default=0, nullable=False)
    avg_response_time = Column(Float, default=0.0, nullable=False)          # 毫秒
    first_resolution_rate = Column(Float, default=0.0, nullable=False)      # 0-1，首次解决率
    escalation_rate = Column(Float, default=0.0, nullable=False)            # 0-1，转人工率

    # 质量指标
    accuracy = Column(Float, default=0.0, nullable=False)                   # 0-1，RAG 准确率
    satisfaction_score = Column(Float, default=0.0, nullable=False)         # 0-5，满意度评分

    # 成本指标
    token_usage = Column(Integer, default=0, nullable=False)                # token 总消耗
    estimated_cost = Column(Float, default=0.0, nullable=False)             # 估算成本（元）

    # 自定义业务指标：工单关闭率 / 线索转化率等，按业务场景扩展
    business_metrics = Column(JSON, nullable=True, default=dict)

    agent = relationship("Agent", backref="kpi_records")

    __table_args__ = (
        # 同一智能体同一周期类型同一起始日唯一，防止重复落库覆盖
        UniqueConstraint(
            "agent_id", "period_type", "period_start",
            name="uq_agent_kpi_agent_period",
        ),
        # 按企业 + 周期类型聚合查询的常用模式
        Index("idx_agent_kpi_enterprise_period", "enterprise_id", "period_type", "period_start"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "enterprise_id": self.enterprise_id,
            "period_type": self.period_type,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "task_count": self.task_count,
            "avg_response_time": round(self.avg_response_time, 2),
            "first_resolution_rate": round(self.first_resolution_rate, 4),
            "escalation_rate": round(self.escalation_rate, 4),
            "accuracy": round(self.accuracy, 4),
            "satisfaction_score": round(self.satisfaction_score, 2),
            "token_usage": self.token_usage,
            "estimated_cost": round(self.estimated_cost, 4),
            "business_metrics": self.business_metrics,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
