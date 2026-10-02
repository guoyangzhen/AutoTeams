"""AutoTeams 4.0 业务 SOP 规程卡（FlowCard）与状态机 Schema 规范。

定义强类型有向图状态机：节点类型、条件跳转边、三项黄金履约铁律守则与执行上下文。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


NodeType = Literal[
    "collect_info",      # 槽位收集 / 信息采集节点
    "action_tool",       # 工具/MCP执行动作节点
    "branch_condition",  # 业务条件判定与分支分流节点
    "approval_human",    # 人工审核审批介入节点 (HITL)
    "sub_flow",          # 嵌套子规程节点
]


class FlowNode(BaseModel):
    """SOP 状态机节点定义。"""
    node_id: str = Field(..., description="节点唯一标识符 (如 step-1-collect)")
    name: str = Field(..., description="节点业务名称")
    node_type: NodeType = Field(default="collect_info", description="节点类型")
    instruction: str = Field(default="", description="该步骤的大模型指引或动作指令")
    expected_slots: List[str] = Field(default_factory=list, description="本节点需要填充收集的关键槽位")
    bound_tools: List[str] = Field(default_factory=list, description="本节点允许调用的工具/MCP清单")
    scoped_knowledge_buckets: List[str] = Field(default_factory=list, description="本节点绑定的知识桶清单(最小必要暴露)")
    timeout_seconds: int = Field(default=120, ge=1, le=3600, description="单步超时时间(秒)")
    assignee_role: Optional[str] = Field(default=None, description="人工审批节点的指定审核角色")


class FlowEdge(BaseModel):
    """SOP 状态机有向跳转边定义。"""
    source_node_id: str = Field(..., description="源节点 ID")
    target_node_id: str = Field(..., description="目标跳转节点 ID")
    condition_expression: Optional[str] = Field(default=None, description="跳转判定表达式 (如 amount > 1000)")
    priority: int = Field(default=0, description="多分支判定优先级，数字越大约先计算")
    # AUD-15：显式默认边。同一源节点最多一条，且不得携带判定条件。
    # 无任何条件命中时只能走这条边；没有它就必须显式失败，禁止回退到"第一条边"。
    is_default: bool = Field(default=False, description="显式默认边：无条件命中时走此边")
    label: Optional[str] = Field(default=None, description="连线展示文案")


class FlowGuardrails(BaseModel):
    """三大黄金履约铁律守则配置。"""
    closed_loop_required: bool = Field(default=True, description="闭环原则：禁止以'请稍候/正在处理'作为最终回复")
    adaptive_slot_filling: bool = Field(default=True, description="自适应推进原则：已提供信息禁止重复追问")
    high_risk_confirmation: bool = Field(default=True, description="关键确认原则：高风险写入操作强制请求用户二次确认")



class FlowRunLimits(BaseModel):
    """一次规程执行的硬性上限（AUD-15：步骤数 / 墙钟时长）。"""

    max_steps: int = Field(default=200, ge=1, le=10000, description="单次执行允许的最大推进步数")
    max_wall_clock_seconds: int = Field(default=300, ge=1, le=86400, description="单次执行允许的最大墙钟时长(秒)")


class FlowCard(BaseModel):
    """AutoTeams 4.0 业务标准作业规程卡 (FlowCard)。"""
    flow_id: str = Field(..., description="规程唯一编号 (如 flow-aftersale-refund)")
    name: str = Field(..., description="规程名称 (如 大客户售后退款标准SOP)")
    version: str = Field(default="1.0.0", description="规程版本号")
    description: str = Field(default="", description="规程业务背景与适用场景")
    guardrails: FlowGuardrails = Field(default_factory=FlowGuardrails, description="履约铁律守则")
    start_node_id: str = Field(..., description="起始节点 ID")
    nodes: List[FlowNode] = Field(..., min_length=1, description="状态机节点集合")
    edges: List[FlowEdge] = Field(default_factory=list, description="状态机连线集合")
    terminal_node_ids: List[str] = Field(default_factory=list, description="终态节点 ID 清单")

    @model_validator(mode="after")
    def validate_graph_integrity(self) -> "FlowCard":
        node_ids = {n.node_id for n in self.nodes}
        if self.start_node_id not in node_ids:
            raise ValueError(f"起始节点 '{self.start_node_id}' 不在节点集合中")
        for edge in self.edges:
            if edge.source_node_id not in node_ids:
                raise ValueError(f"连线源节点 '{edge.source_node_id}' 不存在")
            if edge.target_node_id not in node_ids:
                raise ValueError(f"连线目标节点 '{edge.target_node_id}' 不存在")

        defaults_per_source: Dict[str, int] = {}
        for edge in self.edges:
            if not edge.is_default:
                continue
            if edge.condition_expression:
                raise ValueError(
                    f"连线 {edge.source_node_id} → {edge.target_node_id} 同时是默认边又带判定条件，语义冲突"
                )
            defaults_per_source[edge.source_node_id] = defaults_per_source.get(edge.source_node_id, 0) + 1
            if defaults_per_source[edge.source_node_id] > 1:
                raise ValueError(f"节点 '{edge.source_node_id}' 存在多条默认边，无法确定唯一回落路径")
        for node_id in self.terminal_node_ids:
            if node_id not in node_ids:
                raise ValueError(f"终态节点 '{node_id}' 不在节点集合中")
        return self


class FlowExecutionState(BaseModel):
    """SOP 状态机执行中动态状态（由服务器持久化，客户端不得提供权威副本）。"""
    flow_id: str
    current_node_id: str
    accumulated_slots: Dict[str, Any] = Field(default_factory=dict, description="已提取沉淀的上下文槽位")
    status: Literal["running", "waiting_user_input", "waiting_approval", "completed", "failed"] = "running"
    history_trace: List[Dict[str, Any]] = Field(default_factory=list, description="状态迁移执行流水")
    last_output: Optional[str] = None
    error_message: Optional[str] = None
    # AUD-15：调度硬上限与执行计数，随状态一起落库，进程重启后仍然生效。
    step_count: int = Field(default=0, ge=0, description="已推进的节点步数")
    started_at: Optional[str] = Field(default=None, description="本次执行的起始时间(ISO8601)")
    limits: FlowRunLimits = Field(default_factory=FlowRunLimits, description="本次执行的硬性上限")
    pending_approval_node_id: Optional[str] = Field(default=None, description="当前等待审批的节点 ID")
    # AUD-14：工具节点的真实执行产物；模拟结果永远不写入这里，只写 simulations。
    tool_results: Dict[str, Any] = Field(default_factory=dict, description="按节点 ID 记录的真实工具执行结果")
    simulations: List[Dict[str, Any]] = Field(default_factory=list, description="演示模式下被显式标记为模拟的动作")


class FlowApprovalDecision(BaseModel):
    """由服务器写入数据库、人工审批人身份与决定（AUD-15：审批不可由客户端自证）。"""

    node_id: str
    granted: bool
    approver_user_id: str
    approver_email: str
    approver_role: str
    decision_id: str
    decided_at: str
    comment: Optional[str] = None
