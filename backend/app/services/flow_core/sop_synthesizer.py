"""AutoTeams 4.0 业务经验提炼与 SOP 生成器（SOPSynthesizer）。

将员工的自然语言工作日志、对话问答或企业既有流程文档，
自动提炼编译为符合工业级规范的 FlowCard 状态机有向图。
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from app.services.flow_core.schema import (
    FlowCard,
    FlowNode,
    FlowEdge,
    FlowGuardrails,
)

logger = logging.getLogger(__name__)


class SOPSynthesizer:
    """业务经验提炼器。"""

    @classmethod
    def synthesize_from_text(
        cls,
        text_sop: str,
        name: Optional[str] = None,
        flow_id: Optional[str] = None,
    ) -> FlowCard:
        """从非结构化文本中提取并构建 FlowCard 状态机。"""
        flow_id = flow_id or f"flow-{uuid.uuid4().hex[:8]}"
        name = name or "标准业务作业SOP"

        # 按照换行、编号列表拆解步骤
        lines = [line.strip() for line in text_sop.splitlines() if line.strip()]
        steps: List[str] = []
        for line in lines:
            # 过滤标题，保留步骤行
            cleaned = re.sub(r"^(\d+[\.\、]|[-*•]|\b步骤\s*\d+[:：]?)\s*", "", line).strip()
            if cleaned and len(cleaned) > 2:
                steps.append(cleaned)

        if not steps:
            steps = ["收集业务基本需求", "执行系统操作处理", "向用户汇报交付结果"]

        nodes: List[FlowNode] = []
        edges: List[FlowEdge] = []

        for i, step_text in enumerate(steps):
            node_id = f"step-{i+1}"
            node_type = cls._infer_node_type(step_text)

            # 提取可能槽位
            expected_slots = cls._infer_slots(step_text, node_type)

            node = FlowNode(
                node_id=node_id,
                name=f"步骤 {i+1}: {step_text[:20]}",
                node_type=node_type,
                instruction=step_text,
                expected_slots=expected_slots,
                bound_tools=cls._infer_tools(step_text),
                timeout_seconds=120,
            )
            nodes.append(node)

            # 连接前序节点
            if i > 0:
                edges.append(FlowEdge(
                    source_node_id=f"step-{i}",
                    target_node_id=node_id,
                    priority=0,
                ))

        start_node_id = nodes[0].node_id
        terminal_node_ids = [nodes[-1].node_id]

        flow_card = FlowCard(
            flow_id=flow_id,
            name=name,
            version="1.0.0",
            description=f"基于业务经验自动提炼的规范规程，共包含 {len(nodes)} 个执行节点。",
            guardrails=FlowGuardrails(
                closed_loop_required=True,
                adaptive_slot_filling=True,
                high_risk_confirmation=True,
            ),
            start_node_id=start_node_id,
            nodes=nodes,
            edges=edges,
            terminal_node_ids=terminal_node_ids,
        )
        logger.info(f"成功提炼并生成 FlowCard: {flow_card.flow_id} ({flow_card.name}), 节点数: {len(nodes)}")
        return flow_card

    @classmethod
    def compile_l4_process_to_flow_card(cls, l4_data: Dict[str, Any]) -> FlowCard:
        """将 AutoTeams L4 流程编译器成果无损转接为 FlowCard 状态机。"""
        flow_id = l4_data.get("process_id") or f"flow-{uuid.uuid4().hex[:8]}"
        name = l4_data.get("process_name") or "编译器自动导出SOP"
        description = l4_data.get("description") or "由 AutoTeams 五级资产编译器自动生成的业务规程"

        raw_nodes = l4_data.get("nodes") or []
        nodes: List[FlowNode] = []
        edges: List[FlowEdge] = []

        if not raw_nodes:
            # 容错兜底
            return cls.synthesize_from_text(f"1. {name}\n2. 执行履约\n3. 完成闭环", name=name, flow_id=flow_id)

        for i, n in enumerate(raw_nodes):
            nid = str(n.get("id") or f"step-{i+1}")
            ntype = n.get("type", "collect_info")
            if ntype not in ("collect_info", "action_tool", "branch_condition", "approval_human", "sub_flow"):
                ntype = "collect_info"

            node = FlowNode(
                node_id=nid,
                name=n.get("name") or f"节点 {i+1}",
                node_type=ntype,
                instruction=n.get("description") or n.get("instruction") or "",
                expected_slots=n.get("expected_slots") or [],
                bound_tools=n.get("tools") or [],
            )
            nodes.append(node)

        raw_edges = l4_data.get("edges") or []
        for e in raw_edges:
            edges.append(FlowEdge(
                source_node_id=str(e.get("source")),
                target_node_id=str(e.get("target")),
                condition_expression=e.get("condition"),
            ))

        # 若原无 edge 则串行连接
        if not edges and len(nodes) > 1:
            for i in range(len(nodes) - 1):
                edges.append(FlowEdge(
                    source_node_id=nodes[i].node_id,
                    target_node_id=nodes[i+1].node_id,
                ))

        start_node_id = nodes[0].node_id
        return FlowCard(
            flow_id=flow_id,
            name=name,
            version="1.0.0",
            description=description,
            guardrails=FlowGuardrails(closed_loop_required=True, adaptive_slot_filling=True),
            start_node_id=start_node_id,
            nodes=nodes,
            edges=edges,
            terminal_node_ids=[nodes[-1].node_id],
        )

    @classmethod
    def _infer_node_type(cls, text: str) -> str:
        t = text.lower()
        if any(w in t for w in ("审批", "确认", "授权", "审核", "批准", "approval", "review")):
            return "approval_human"
        elif any(w in t for w in ("调用", "执行", "发送", "查询系统", "写入", "call", "execute", "tool")):
            return "action_tool"
        elif any(w in t for w in ("如果", "若", "判断", "分支", "是否", "if", "branch")):
            return "branch_condition"
        return "collect_info"

    @classmethod
    def _infer_slots(cls, text: str, node_type: str) -> List[str]:
        if node_type != "collect_info":
            return []
        slots = []
        for kw in ("订单号", "手机号", "姓名", "金额", "地址", "原因", "用户ID", "邮箱"):
            if kw in text:
                slots.append(kw)
        return slots

    @classmethod
    def _infer_tools(cls, text: str) -> List[str]:
        tools = []
        if "查询" in text:
            tools.append("tool_query")
        if "邮件" in text or "通知" in text:
            tools.append("tool_send_notification")
        if "退款" in text:
            tools.append("tool_refund_gateway")
        return tools
