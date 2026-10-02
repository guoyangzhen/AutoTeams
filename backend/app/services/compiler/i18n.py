"""编译器中文标签映射（#11 编译时间线中文化）。

Knowledge Compiler 的 discovered_summary 中的实体类型 / 关系类型，以及
Process Compiler 的流程类型，均以英文枚举值呈现（如 Role / Department /
sop / decision）。本模块提供统一的中文映射，供各级编译器 _generate_summary
与前端展示使用，实现「角色→岗位、SOP→SOP（标准操作流程）」等中文化。
"""

# 实体类型 → 中文标签（对应 knowledge_graph.EntityType）
ENTITY_TYPE_LABELS: dict[str, str] = {
    "Department": "部门",
    "Role": "岗位",
    "Employee": "员工",
    "Process": "流程",
    "Product": "产品",
    "Customer": "客户",
    "KPI": "指标",
    "System": "业务系统",
    "Permission": "权限",
    "Knowledge": "知识条目",
    "Opportunity": "商机",
    "Order": "订单",
    "Tool": "工具",
}

# 关系类型 → 中文标签（对应 knowledge_graph.RelationType）
RELATION_TYPE_LABELS: dict[str, str] = {
    "belongs_to": "归属",
    "reports_to": "汇报",
    "executes": "执行",
    "owes": "负责指标",
    "has_permission": "拥有权限",
    "uses": "使用系统",
    "produces": "产出知识",
    "serves": "服务",
}

# 流程类型 → 中文标签（对应 ProcessDefinition.process_type）
# SOP 特殊处理：SOP → SOP（标准操作流程）
PROCESS_TYPE_LABELS: dict[str, str] = {
    "sop": "SOP（标准操作流程）",
    "decision": "决策流程",
    "automated": "自动化流程",
    "manual": "人工流程",
    "approval": "审批流程",
    "collaboration": "协作流程",
    "business": "业务流程",
}


def entity_label(entity_type: str) -> str:
    """实体类型 → 中文标签，未知类型原样返回。"""
    return ENTITY_TYPE_LABELS.get(entity_type, entity_type)


def relation_label(relation: str) -> str:
    """关系类型 → 中文标签，未知类型原样返回。"""
    return RELATION_TYPE_LABELS.get(relation, relation)


def process_label(process_type: str) -> str:
    """流程类型 → 中文标签，未知类型原样返回。"""
    return PROCESS_TYPE_LABELS.get(process_type, process_type)
