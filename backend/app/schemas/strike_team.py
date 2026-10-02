"""AutoTeams 5.0 动态敏捷特遣队 schema —— 战役 1 接口契约。

对应 docs/AutoTeams-5.0演进蓝图与全自主蜂群架构设计.md §战役 1.3 核心 API 端点契约：
- POST /api/v1/strike_teams                 发起特遣队招募
- GET  /api/v1/strike_teams                 查询活动特遣队列表与成员状态
- POST /api/v1/strike_teams/{id}/bid        带资竞标响应
- POST /api/v1/strike_teams/{id}/dissolve   交付验收并清算解散

校验意图：
- HP 抵押是「带资」入场券，必须为正数且有上限，避免单员工掏空整个抵押池；
- 算力配额是租约硬上限，成员抵押总额不得超过配额，防止超卖；
- 子任务 DAG 必须是 {nodes, edges} 结构，锁入前校验依赖已完成。
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrikeTeamStatus(str, enum.Enum):
    """特遣队生命周期状态机：forming → active → reviewing → dissolved。

    按仓内约定，Python Enum 只在 schemas 层定义，ORM 列存 String 取值。
    """

    FORMING = "forming"      # 组建与竞标中
    ACTIVE = "active"        # 执行中（资源租约生效）
    REVIEWING = "reviewing"  # 成果验收与对齐
    DISSOLVED = "dissolved"  # 任务闭环，特遣队注销

# 单个成员允许抵押的 HP 上限（满分 100，防止单点掏空抵押池）
MAX_MEMBER_STAKE_HP = 100.0
# 单个特遣队允许抵押的 HP 总量上限
MAX_TOTAL_STAKE_HP = 400.0

StrikeTeamStatusLiteral = Literal["forming", "active", "reviewing", "dissolved"]
SubtaskStatusLiteral = Literal["pending", "locked", "delivered", "accepted"]


class StrikeTeamMember(BaseModel):
    """特遣队成员条目（存于 StrikeTeam.members JSON 列）。"""

    model_config = ConfigDict(from_attributes=True)

    badge: str = Field(..., min_length=1, max_length=48, description="数字员工工号")
    role: str = Field(..., min_length=1, max_length=64, description="队内角色分工")
    stake_hp: float = Field(
        default=0.0, ge=0.0, le=MAX_MEMBER_STAKE_HP, description="入队时抵押的 HP（对赌资金）"
    )
    joined_at: Optional[datetime] = None


class SubtaskNode(BaseModel):
    """DAG 子任务节点。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=256)
    status: SubtaskStatusLiteral = "pending"
    assignee_badge: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list, description="前置子任务 id 列表")
    deliverable: Optional[Dict[str, Any]] = None


class SubtaskGraph(BaseModel):
    """动态 DAG 子任务依赖图。"""

    model_config = ConfigDict(from_attributes=True)

    nodes: List[SubtaskNode] = Field(default_factory=list)
    edges: List[Dict[str, str]] = Field(default_factory=list, description="[{from, to}] 依赖边")

    @model_validator(mode="after")
    def _validate_acyclic(self) -> "SubtaskGraph":
        """拒绝存在环的依赖图：带资竞标要求任务可串行推进，环会让子任务永远无法锁入。"""
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("子任务 id 存在重复")
        for node in self.nodes:
            unknown = [d for d in node.depends_on if d not in ids]
            if unknown:
                raise ValueError(f"子任务 {node.id} 依赖了不存在的节点: {unknown}")

        # 拓扑排序：若无法排完全部节点即存在环
        indegree = {n.id: len(set(n.depends_on)) for n in self.nodes}
        dependents: Dict[str, List[str]] = {i: [] for i in ids}
        for node in self.nodes:
            for dep in set(node.depends_on):
                dependents[dep].append(node.id)
        queue = [i for i, d in indegree.items() if d == 0]
        visited = 0
        while queue:
            cur = queue.pop()
            visited += 1
            for nxt in dependents[cur]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if visited != len(ids):
            raise ValueError("子任务依赖图存在环，无法拓扑排序")
        return self


class StrikeTeamCreateRequest(BaseModel):
    """发起特遣队招募请求。"""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=128, description="特遣队代号")
    mission_statement: str = Field(..., min_length=1, description="核心使命与验收标准")
    initiator_badge: str = Field(..., min_length=1, max_length=48, description="发起员工工号")
    initiator_role: str = Field(default="队长", max_length=64, description="发起人在队内角色")
    initiator_stake_hp: float = Field(
        default=0.0, ge=0.0, le=MAX_MEMBER_STAKE_HP, description="发起人抵押 HP"
    )
    allocated_compute_budget: float = Field(
        default=100.0, gt=0.0, le=10000.0, description="算力配额（租约上限）"
    )
    subtask_graph: SubtaskGraph = Field(default_factory=SubtaskGraph)
    expires_at: Optional[datetime] = None

    @field_validator("expires_at")
    @classmethod
    def _expires_in_future(cls, v: Optional[datetime]) -> Optional[datetime]:
        """租约到期时间必须晚于当前时间，否则特遣队一落地就已过期。"""
        if v is None:
            return None
        from app.utils.time import utcnow

        now = utcnow()
        if v.tzinfo is None:
            v = v.replace(tzinfo=now.tzinfo)
        if v <= now:
            raise ValueError("expires_at 必须晚于当前时间")
        return v


class StrikeTeamBidRequest(BaseModel):
    """带资竞标响应请求（Contract Net Protocol 2.0）。"""

    model_config = ConfigDict(from_attributes=True)

    badge: str = Field(..., min_length=1, max_length=48, description="竞标员工工号")
    role: str = Field(..., min_length=1, max_length=64, description="申请队内角色")
    stake_hp: float = Field(
        ..., gt=0.0, le=MAX_MEMBER_STAKE_HP, description="带资抵押 HP，必须为正"
    )
    rationale: str = Field(default="", max_length=1024, description="胜任理由与方案陈述")

    @field_validator("stake_hp")
    @classmethod
    def _stake_is_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("带资竞标必须抵押正数 HP")
        return v


class StrikeTeamDissolveRequest(BaseModel):
    """交付验收并清算解散请求。"""

    model_config = ConfigDict(from_attributes=True)

    accepted: bool = Field(..., description="验收是否通过")
    settlement_note: str = Field(default="", max_length=1024, description="清算说明")
    deliverable: Optional[Dict[str, Any]] = Field(default=None, description="交付成果三件套")


class StrikeTeamView(BaseModel):
    """特遣队视图（列表与详情共用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    enterprise_id: str
    name: str
    mission_statement: str
    initiator_badge: str
    status: StrikeTeamStatusLiteral
    allocated_compute_budget: float
    total_hp_stake: float
    shared_blackboard_id: str
    members: List[StrikeTeamMember] = Field(default_factory=list)
    subtask_graph: SubtaskGraph = Field(default_factory=SubtaskGraph)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
