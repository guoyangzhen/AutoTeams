"""AutoTeams 4.0 业务 SOP 流程状态机驱动引擎（FlowEngine）。

AUD-14 / AUD-15 修复要点：

1. **不再递归**：调度是迭代循环，每个循环体处理一个节点，并受 ``max_steps`` 与
   ``max_wall_clock_seconds`` 硬上限约束。自环、重复调用都得到确定的终止结果，
   不会再出现 RecursionError（此前"合法 Schema 的自环"就能打爆调用栈）。
2. **分支必须显式**：条件全部不命中时，只允许走显式 ``is_default`` 边；没有默认边
   就显式失败，绝不回退到"第一条边"（此前 amount=0 也会跳到大额分支）。
3. **工具节点必须真执行**：``action_tool`` 节点调用 ``tool_executor`` 里注册的
   真实执行器；没有绑定工具或没有注册执行器就失败，不再写"已执行动作"。
4. **审批不可自证**：``approval_granted`` 布尔位已移除，推进审批节点必须传入服务器
   从数据库读出的 :class:`FlowApprovalDecision`（含审批人身份与决定 ID）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.services.flow_core.safe_eval import SafeASTEvaluator

from app.services.flow_core.schema import (
    FlowApprovalDecision,
    FlowCard,
    FlowExecutionState,
    FlowNode,
    FlowRunLimits,
)
from app.services.flow_core.tool_executor import (
    FlowToolError,
    execute_bound_tools,
)

logger = logging.getLogger(__name__)

# 默认高风险动作关键字集合（触发 high_risk_confirmation）
HIGH_RISK_KEYWORDS = {"退款", "转账", "支付", "删除", "清空", "重置", "修改权限", "refund", "transfer", "delete"}

_TERMINAL_STATUSES = ("completed", "failed")


class FlowBranchError(RuntimeError):
    """没有条件命中且没有显式默认边 —— 分支无法确定，必须显式失败。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class FlowEngine:
    """SOP 状态机执行引擎（迭代调度 + 硬上限）。"""

    @classmethod
    async def start_flow(
        cls,
        flow: FlowCard,
        initial_slots: Optional[Dict[str, Any]] = None,
        limits: Optional[FlowRunLimits] = None,
    ) -> FlowExecutionState:
        """初始化启动一个 FlowCard 规程状态机（执行状态由调用方持久化到数据库）。"""
        slots = dict(initial_slots) if initial_slots else {}
        start_node = cls.get_node(flow, flow.start_node_id)
        if not start_node:
            raise ValueError(f"FlowCard 起始节点不存在: {flow.start_node_id}")

        state = FlowExecutionState(
            flow_id=flow.flow_id,
            current_node_id=start_node.node_id,
            accumulated_slots=slots,
            status="running",
            history_trace=[{
                "timestamp": _iso(_now()),
                "from_node": None,
                "to_node": start_node.node_id,
                "action": "start_flow",
            }],
            last_output=f"已启动业务规程: {flow.name}。当前步骤: {start_node.name}。",
            started_at=_iso(_now()),
            limits=limits or FlowRunLimits(),
        )
        return await cls._drive(flow, state)

    @classmethod
    async def step(
        cls,
        flow: FlowCard,
        state: FlowExecutionState,
        user_input: Optional[str] = None,
        approval: Optional[FlowApprovalDecision] = None,
    ) -> FlowExecutionState:
        """推进状态机，直到需要人工输入/审批、到达终态或触发硬上限。

        :param user_input: 本次调用的用户输入，只会被 ``collect_info`` 节点消费。
        :param approval: 服务器从 ``flow_approvals`` 读出的审批决定；客户端自报的
            布尔值不再被接受。
        """
        if state.status in _TERMINAL_STATUSES:
            return state
        return await cls._drive(flow, state, user_input=user_input, approval=approval)

    # ------------------------------------------------------------------
    # 迭代调度
    # ------------------------------------------------------------------
    @classmethod
    async def _drive(
        cls,
        flow: FlowCard,
        state: FlowExecutionState,
        user_input: Optional[str] = None,
        approval: Optional[FlowApprovalDecision] = None,
    ) -> FlowExecutionState:
        pending_input = user_input
        pending_approval = approval

        while True:
            if state.status in _TERMINAL_STATUSES:
                return state

            limit_hit = cls._check_limits(state)
            if limit_hit is not None:
                return cls._fail(state, limit_hit)

            node = cls.get_node(flow, state.current_node_id)
            if node is None:
                return cls._fail(state, f"未知节点: {state.current_node_id}")

            # 1. 人工审批节点：必须由服务器读出的审批记录驱动
            if node.node_type == "approval_human" or cls._requires_risk_confirmation(flow, node):
                decided, state, _ = cls._apply_approval(state, node, pending_approval)
                if not decided:
                    return state

            # 2. 信息收集节点
            if node.node_type == "collect_info":
                if pending_input:
                    cls._extract_slots(node, pending_input, state.accumulated_slots)
                    pending_input = None
                # 自适应槽位：已沉淀的槽位不会再次追问，只补齐仍然缺失的部分。
                missing = [s for s in node.expected_slots if s not in state.accumulated_slots]
                if missing:
                    state.status = "waiting_user_input"
                    state.last_output = f"[{node.name}] 仍需补充信息: {', '.join(missing)}"
                    return state

            # 3. 工具执行节点：必须真正调用 bound_tools
            if node.node_type == "action_tool":
                try:
                    results = await execute_bound_tools(node, state)
                except FlowToolError as exc:
                    return cls._fail(state, str(exc))
                if results:
                    state.tool_results[node.node_id] = results
                    state.last_output = f"[{node.name}] 已调用工具 {', '.join(sorted(results))} 并返回结果。"
                else:
                    state.simulations.append({
                        "node_id": node.node_id,
                        "node_name": node.name,
                        "mode": "demo",
                        "simulated_tools": list(node.bound_tools),
                        "at": _iso(_now()),
                    })
                    state.last_output = (
                        f"[DEMO 模拟 · 非真实执行] 节点 [{node.name}] 未配置真实工具执行器，"
                        "本步骤不产生任何已执行结论。"
                    )

            # 4. 子规程节点尚未实现：显式失败，不做静默跳过
            if node.node_type == "sub_flow":
                return cls._fail(state, f"子规程节点 [{node.node_id}] 尚未实现，拒绝跳过该业务步骤")

            # 5. 跳转
            try:
                next_node_id = cls._resolve_next_node(flow, node, state.accumulated_slots)
            except FlowBranchError as exc:
                return cls._fail(state, str(exc))

            if next_node_id is None:
                if flow.terminal_node_ids and node.node_id in flow.terminal_node_ids:
                    state.status = "completed"
                    state.last_output = f"业务规程 [{flow.name}] 执行完毕。"
                else:
                    state.status = "completed"
                    state.last_output = f"流程在节点 [{node.name}] 完成最终履约。"
                state.pending_approval_node_id = None
                cls._trace(state, "completed")
                return state

            cls._enter_node(state, node.node_id, next_node_id)
            state.status = "running"

    # ------------------------------------------------------------------
    # 审批
    # ------------------------------------------------------------------
    @classmethod
    def _requires_risk_confirmation(cls, flow: FlowCard, node: FlowNode) -> bool:
        """高风险确认铁律：动作节点命中风险关键字时也要人工二次确认。"""
        if not flow.guardrails.high_risk_confirmation or node.node_type != "action_tool":
            return False
        haystack = f"{node.name} {node.instruction}".lower()
        return any(k in haystack for k in HIGH_RISK_KEYWORDS)

    @classmethod
    def _apply_approval(
        cls,
        state: FlowExecutionState,
        node: FlowNode,
        approval: Optional[FlowApprovalDecision],
    ) -> Tuple[bool, FlowExecutionState, Optional[FlowApprovalDecision]]:
        """处理审批需求。返回 (是否已获批可继续, 状态, 剩余审批)。"""
        state.pending_approval_node_id = node.node_id
        if approval is None:
            state.status = "waiting_approval"
            state.last_output = (
                f"步骤 [{node.name}] 需要人工审批确认；"
                "审批必须由授权审批人通过审批接口提交，客户端无法直接授权。"
            )
            return False, state, None

        if approval.node_id != node.node_id:
            return (
                False,
                cls._fail(state, f"审批记录属于节点 {approval.node_id}，与当前节点 {node.node_id} 不一致"),
                None,
            )

        if not approval.granted:
            state.pending_approval_node_id = None
            state.last_output = f"步骤 [{node.name}] 人工审批未通过，流程终止。"
            state.status = "failed"
            state.error_message = f"审批被拒绝 (approver={approval.approver_email})"
            cls._trace(state, "approval_rejected", detail={"approver": approval.approver_email})
            return False, state, None

        cls._trace(state, "approval_granted", detail={
            "approver": approval.approver_email,
            "approver_role": approval.approver_role,
            "decision_id": approval.decision_id,
        })
        state.pending_approval_node_id = None
        return True, state, None

    # ------------------------------------------------------------------
    # 硬上限 / 分支 / 工具函数
    # ------------------------------------------------------------------
    @classmethod
    def _check_limits(cls, state: FlowExecutionState) -> Optional[str]:
        if state.step_count >= state.limits.max_steps:
            return (
                f"已达最大推进步数上限 {state.limits.max_steps}，为安全终止本次执行"
                f"（当前节点 {state.current_node_id}）"
            )
        if state.started_at:
            try:
                started = datetime.fromisoformat(state.started_at)
            except ValueError:
                started = _now()
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            deadline = started + timedelta(seconds=state.limits.max_wall_clock_seconds)
            if _now() > deadline:
                return (
                    f"已超过最大执行时长 {state.limits.max_wall_clock_seconds}s，"
                    f"为安全终止本次执行（当前节点 {state.current_node_id}）"
                )
        return None

    @classmethod
    def _resolve_next_node(
        cls,
        flow: FlowCard,
        current_node: FlowNode,
        slots: Dict[str, Any],
    ) -> Optional[str]:
        """根据有向边与判定表达式确定下一节点。

        无任何条件命中时，只能走显式 ``is_default`` 边；否则抛出
        :class:`FlowBranchError`，由调用方显式失败。**不再**回退到第一条边。
        """
        outgoing_edges = [e for e in flow.edges if e.source_node_id == current_node.node_id]
        if not outgoing_edges:
            return None

        # 按优先级降序排序
        outgoing_edges.sort(key=lambda x: x.priority, reverse=True)

        for edge in outgoing_edges:
            if edge.is_default:
                continue
            if not edge.condition_expression:
                # 显式无条件边直接命中
                return edge.target_node_id
            if cls._eval_condition(edge.condition_expression, slots):
                return edge.target_node_id

        for edge in outgoing_edges:
            if edge.is_default:
                return edge.target_node_id

        raise FlowBranchError(
            f"节点 [{current_node.node_id} {current_node.name}] 的全部分支条件均不成立，"
            "且未配置显式默认边（is_default=true），执行中止以避免走错业务分支"
        )

    @classmethod
    def _eval_condition(cls, expr: str, slots: Dict[str, Any]) -> bool:
        """安全 AST 评估条件表达式（如 'amount > 500' 或 'type == "vip"'），绝不执行 eval。"""
        return SafeASTEvaluator.evaluate(expr, slots)

    @classmethod
    def _enter_node(cls, state: FlowExecutionState, from_node_id: str, to_node_id: str) -> None:
        state.current_node_id = to_node_id
        state.step_count += 1
        cls._trace(state, "transition", from_node=from_node_id, to_node=to_node_id)

    @classmethod
    def _trace(
        cls,
        state: FlowExecutionState,
        action: str,
        from_node: Optional[str] = None,
        to_node: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry: Dict[str, Any] = {
            "timestamp": _iso(_now()),
            "action": action,
            "from_node": from_node,
            "to_node": to_node,
            "step": state.step_count,
        }
        if detail:
            entry["detail"] = detail
        state.history_trace.append(entry)

    @classmethod
    def _fail(cls, state: FlowExecutionState, message: str) -> FlowExecutionState:
        state.status = "failed"
        state.error_message = message
        state.last_output = message
        state.pending_approval_node_id = None
        cls._trace(state, "failed", detail={"error": message})
        logger.warning("Flow 执行失败 [%s @ %s]: %s", state.flow_id, state.current_node_id, message)
        return state

    @classmethod
    def _extract_slots(cls, node: FlowNode, user_input: str, slots: Dict[str, Any]) -> None:
        """从用户回复中提取关键槽位（已沉淀的槽位不会被覆盖）。"""
        for expected in node.expected_slots:
            if expected in slots:
                continue
            # 1. 匹配 "key: value" / "订单号：value" 这类标注
            pattern = rf"(?:{re.escape(expected)}|订单号|手机号|姓名|金额|原因)[:：\s]+([^\s,，。]+)"
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                slots[expected] = match.group(1).strip()
                continue
            # 2. 退而求其次：任意冒号后的值
            colon_match = re.search(r"[:：]\s*([^\s,，。]+)", user_input)
            if colon_match:
                slots[expected] = colon_match.group(1).strip()
            elif len(node.expected_slots) == 1:
                # 3. 单槽位直接接收用户输入
                slots[expected] = user_input.strip()

    @classmethod
    def get_node(cls, flow: FlowCard, node_id: str) -> Optional[FlowNode]:
        for n in flow.nodes:
            if n.node_id == node_id:
                return n
        return None
