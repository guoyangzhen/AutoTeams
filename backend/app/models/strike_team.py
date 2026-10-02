"""AutoTeams 5.0 动态敏捷特遣队模型（Dynamic Strike Teams）。

对应 docs/AutoTeams-5.0演进蓝图与全自主蜂群架构设计.md 战役 1：
打破静态组织结构，数字员工自主发起组队、拆解子任务，通过 Contract Net Protocol 2.0
向其他在岗/空闲员工发起带资竞标，临时锁定资源租约并在共享黑板协作，任务闭环后
自动清算 HP 抵押并解散。

与 4.0 Team-Matrix 的区别：
- 4.0 `WorkgroupTeam` 是人先建组、再派活；5.0 特遣队是员工感知到复杂任务后自主发起，
  成员靠「带资竞标」（抵押 HP 对赌）而非指派加入。
- HP 抵押池 `total_hp_stake` 是全队对赌资金，验收通过返还、验收失败罚没。

约定：状态列沿用仓内「String + 注释枚举取值」写法（模型层不放 Python Enum，
枚举真源在 app/schemas/strike_team.py 的 StrikeTeamStatusLiteral）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, Column, DateTime, Float, String, Text

from app.database import Base
from app.utils.time import utcnow


class StrikeTeam(Base):
    """动态敏捷特遣队表。状态机：forming → active → reviewing → dissolved。"""

    __tablename__ = "strike_teams"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(String(36), nullable=False, index=True)
    name = Column(String(128), nullable=False)              # 特遣队代号（如：Alpha-01-海关报关突击队）
    mission_statement = Column(Text, nullable=False)        # 核心使命与验收标准
    initiator_badge = Column(String(48), nullable=False)    # 发起员工工号（如：ATE-2026-SALES-001）
    # forming(组建与竞标中) / active(执行中,资源租约生效) / reviewing(成果验收) / dissolved(已注销)
    status = Column(String(24), nullable=False, default="forming", index=True)

    # 资源租约与经济模型
    allocated_compute_budget = Column(Float, nullable=False, default=100.0)  # 算力配额（点数）
    total_hp_stake = Column(Float, nullable=False, default=0.0)              # 全队 HP 抵押池（对赌机制）
    shared_blackboard_id = Column(String(36), nullable=False)               # 关联黑板 ID

    # 成员清单与角色分工
    members = Column(JSON, nullable=False, default=list)  # List[{badge, role, stake_hp, joined_at}]
    subtask_graph = Column(JSON, nullable=False, default=dict)  # DAG 子任务依赖图 {nodes, edges}

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # 租约到期强制解散时间

    def __repr__(self) -> str:
        return f"<StrikeTeam id={self.id} name={self.name} status={self.status}>"
