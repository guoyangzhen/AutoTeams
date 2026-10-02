"""WT4 交互式企业访谈问题库（PRD §5.7）。

7 大类问题库：销售流程 / 客户服务 / 采购与供应链 / 财务与费用 /
人事与组织 / 数据与权限 / KPI 与目标。

每类问题含：
- question: 问题文本
- expected_output: 预期产出
- affected_field: 受影响的运行模型字段（点分路径，用于触发增量重编译）
- priority: 优先级（P0 关键 / P1 重要 / P2 补充）

问题来源于 PRD §5.7 访谈问题示例，是渐进式对话的起点。
随着运行模型完善，系统会提出越来越精准的问题（MVP：静态问题库 +
基于当前完成度的排序；动态生成 P1 迭代）。
"""
from dataclasses import dataclass
from typing import Optional



# 7 大类分类标识
CATEGORY_SALES = "sales"
CATEGORY_CUSTOMER_SERVICE = "customer_service"
CATEGORY_PROCUREMENT = "procurement"
CATEGORY_FINANCE = "finance"
CATEGORY_HR = "hr"
CATEGORY_DATA = "data"
CATEGORY_KPI = "kpi"

ALL_CATEGORIES = (
    CATEGORY_SALES,
    CATEGORY_CUSTOMER_SERVICE,
    CATEGORY_PROCUREMENT,
    CATEGORY_FINANCE,
    CATEGORY_HR,
    CATEGORY_DATA,
    CATEGORY_KPI,
)

# 分类中文名（便于展示与日志）
CATEGORY_LABELS = {
    CATEGORY_SALES: "销售流程",
    CATEGORY_CUSTOMER_SERVICE: "客户服务",
    CATEGORY_PROCUREMENT: "采购与供应链",
    CATEGORY_FINANCE: "财务与费用",
    CATEGORY_HR: "人事与组织",
    CATEGORY_DATA: "数据与权限",
    CATEGORY_KPI: "KPI 与目标",
}


@dataclass(frozen=True)
class QuestionTemplate:
    """问题模板。

    affected_field 指向运行模型中的字段路径：
    - organization.*       → 影响 Organization Runtime（触发 Process/Runtime 重编译）
    - kpi.*                → 影响 KPI 体系（触发 Capability 重编译）
    - process.*            → 影响流程定义（触发 Process/Capability 重编译）
    - data.*               → 影响数据模型（触发 Information/Knowledge 重编译）
    - role.*               → 影响岗位（触发 Capability 重编译）
    """

    category: str
    question: str
    expected_output: str
    affected_field: str
    priority: str = "P1"


# ============================================================================
# 问题库（7 大类）—— 内容来自 PRD §5.7 访谈问题示例
# ============================================================================

_QUESTION_BANK: list[QuestionTemplate] = [
    # ---- 销售流程 ----
    QuestionTemplate(
        CATEGORY_SALES,
        "销售成功后，订单交给谁处理？",
        "订单处理责任人/部门",
        "process.order_handling",
        "P0",
    ),
    QuestionTemplate(
        CATEGORY_SALES,
        "报价审批的权限分级是怎样的？谁有权批准多大金额？",
        "审批权限分级表（金额→审批人）",
        "process.approval_rules",
        "P0",
    ),
    QuestionTemplate(
        CATEGORY_SALES,
        "客户线索从哪里来？分配规则是什么？",
        "线索来源与分配规则",
        "process.lead_allocation",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_SALES,
        "丢单后是否需要复盘？复盘流程是什么？",
        "丢单复盘流程",
        "process.loss_review",
        "P2",
    ),
    # ---- 客户服务 ----
    QuestionTemplate(
        CATEGORY_CUSTOMER_SERVICE,
        "客服满意度目标要达到百分之多少？",
        "满意度目标值",
        "kpi.customer_service.satisfaction",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_CUSTOMER_SERVICE,
        "客户投诉的升级流程是什么？多久内必须响应？",
        "投诉升级流程 + 响应时效",
        "process.complaint_escalation",
        "P0",
    ),
    QuestionTemplate(
        CATEGORY_CUSTOMER_SERVICE,
        "哪类问题可以自动处理，哪类必须转人工？",
        "自动/人工分类标准",
        "process.auto_vs_manual",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_CUSTOMER_SERVICE,
        "客户分级标准是什么？不同等级的服务标准差异？",
        "客户分级 + 服务标准",
        "organization.customer_tiers",
        "P1",
    ),
    # ---- 采购与供应链 ----
    QuestionTemplate(
        CATEGORY_PROCUREMENT,
        "采购审批权限是怎么分级的？",
        "采购审批权限分级",
        "process.procurement_approval",
        "P0",
    ),
    QuestionTemplate(
        CATEGORY_PROCUREMENT,
        "供应商准入标准是什么？准入流程是怎样的？",
        "供应商准入标准与流程",
        "process.supplier_onboarding",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_PROCUREMENT,
        "库存预警阈值是多少？触发预警后的流程？",
        "库存预警阈值 + 响应流程",
        "process.inventory_alert",
        "P1",
    ),
    # ---- 财务与费用 ----
    QuestionTemplate(
        CATEGORY_FINANCE,
        "费用报销的审批链是怎样的？各层级审批额度是多少？",
        "报销审批链 + 额度",
        "process.expense_approval",
        "P0",
    ),
    QuestionTemplate(
        CATEGORY_FINANCE,
        "付款条件是什么？账期多久？",
        "付款条件与账期",
        "process.payment_terms",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_FINANCE,
        "发票审核的关键校验点有哪些？",
        "发票校验点清单",
        "process.invoice_audit",
        "P1",
    ),
    # ---- 人事与组织 ----
    QuestionTemplate(
        CATEGORY_HR,
        "新员工入职流程包含哪些步骤？需要开通哪些系统权限？",
        "入职步骤 + 系统权限清单",
        "process.onboarding",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_HR,
        "离职流程是怎样的？交接需要完成哪些事项？",
        "离职流程 + 交接清单",
        "process.offboarding",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_HR,
        "绩效考核周期和标准是什么？",
        "考核周期与标准",
        "kpi.performance_review",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_HR,
        "组织架构是怎样的？有哪些部门与汇报关系？",
        "组织架构 + 汇报关系",
        "organization.departments",
        "P0",
    ),
    # ---- 数据与权限 ----
    QuestionTemplate(
        CATEGORY_DATA,
        "CRM 中哪些字段是必填的？各自代表什么业务含义？",
        "必填字段清单 + 业务含义",
        "data.crm_fields",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_DATA,
        "不同岗位的数据访问权限范围是什么？",
        "岗位数据权限矩阵",
        "role.data_permissions",
        "P0",
    ),
    QuestionTemplate(
        CATEGORY_DATA,
        "哪些数据是敏感数据？敏感数据的处理规则是什么？",
        "敏感数据分类 + 处理规则",
        "data.sensitive_policy",
        "P0",
    ),
    # ---- KPI 与目标 ----
    QuestionTemplate(
        CATEGORY_KPI,
        "各部门的核心 KPI 是什么？目标值是多少？",
        "部门 KPI 与目标值",
        "kpi.targets",
        "P0",
    ),
    QuestionTemplate(
        CATEGORY_KPI,
        "KPI 的考核周期是怎样的？",
        "KPI 考核周期",
        "kpi.review_cycle",
        "P1",
    ),
    QuestionTemplate(
        CATEGORY_KPI,
        "哪些指标是领先指标，哪些是滞后指标？",
        "领先/滞后指标分类",
        "kpi.leading_vs_lagging",
        "P2",
    ),
]


class QuestionBank:
    """访谈问题库。

    提供按分类/优先级检索问题模板的能力，供 interview_engine 在
    start_session 时实例化问题记录。
    """

    def __init__(self, templates: Optional[list[QuestionTemplate]] = None) -> None:
        self._templates: list[QuestionTemplate] = list(templates) if templates else list(_QUESTION_BANK)

    @property
    def categories(self) -> tuple[str, ...]:
        """返回 7 大类分类标识。"""
        return ALL_CATEGORIES

    @property
    def total(self) -> int:
        """问题总数。"""
        return len(self._templates)

    def all_templates(self) -> list[QuestionTemplate]:
        """返回全部问题模板（按优先级 P0→P1→P2 排序）。"""
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        return sorted(self._templates, key=lambda q: priority_order.get(q.priority, 9))

    def by_category(self, category: str) -> list[QuestionTemplate]:
        """按分类筛选问题模板。"""
        return [q for q in self._templates if q.category == category]

    def affected_stages_for_field(self, affected_field: str) -> list[str]:
        """根据 affected_field 推导受影响的编译层级（用于增量重编译）。

        映射规则（重构方案 §7.6 阶段1 + spec.md §10.2 编译层级）：
        - data.*            → [information, knowledge]（数据/知识层）
        - organization.*    → [process, capability, runtime]（组织→流程→能力→运行时）
        - process.*         → [process, capability, runtime]
        - role.*            → [capability, runtime]
        - kpi.*             → [capability, runtime]
        """
        if not affected_field:
            return ["information", "knowledge", "process", "capability", "runtime"]
        prefix = affected_field.split(".", 1)[0]
        mapping = {
            "data": ["information", "knowledge"],
            "organization": ["process", "capability", "runtime"],
            "process": ["process", "capability", "runtime"],
            "role": ["capability", "runtime"],
            "kpi": ["capability", "runtime"],
        }
        return mapping.get(prefix, ["information", "knowledge", "process", "capability", "runtime"])


# 模块级单例（无状态，可安全共享）
question_bank = QuestionBank()
