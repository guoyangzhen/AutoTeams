"""访谈问题库测试（PRD §5.7，7 大类）。

测试覆盖：
- 7 大类问题覆盖（sales/customer_service/procurement/finance/hr/data/kpi）
- 每类至少 2 个问题
- 优先级分布（P0/P1/P2）
- affected_field 映射到编译层级
"""
import pytest

from app.services.interview.question_bank import (
    QuestionBank,
    question_bank,
    ALL_CATEGORIES,
    CATEGORY_SALES,
    CATEGORY_CUSTOMER_SERVICE,
    CATEGORY_PROCUREMENT,
    CATEGORY_FINANCE,
    CATEGORY_HR,
    CATEGORY_DATA,
    CATEGORY_KPI,
    CATEGORY_LABELS,
)


class TestQuestionBankCategories:
    """7 大类问题覆盖测试。"""

    def test_seven_categories_defined(self):
        """确认 7 大类全部定义。"""
        assert len(ALL_CATEGORIES) == 7
        for cat in ALL_CATEGORIES:
            assert cat in CATEGORY_LABELS

    def test_each_category_has_questions(self):
        """每类至少 2 个问题。"""
        bank = question_bank
        for category in ALL_CATEGORIES:
            questions = bank.by_category(category)
            assert len(questions) >= 2, (
                f"分类 {category} 仅有 {len(questions)} 个问题，需 ≥ 2"
            )

    def test_sales_questions(self):
        """销售类问题包含 P0 关键问题。"""
        sales_qs = question_bank.by_category(CATEGORY_SALES)
        assert len(sales_qs) >= 3
        # 至少 1 个 P0
        p0_qs = [q for q in sales_qs if q.priority == "P0"]
        assert len(p0_qs) >= 1

    def test_customer_service_questions(self):
        """客服类问题包含 P0 升级流程。"""
        cs_qs = question_bank.by_category(CATEGORY_CUSTOMER_SERVICE)
        assert len(cs_qs) >= 2
        # 升级流程为 P0
        escalation = [q for q in cs_qs if "升级" in q.question]
        assert escalation and escalation[0].priority == "P0"

    def test_procurement_questions(self):
        """采购类问题。"""
        proc_qs = question_bank.by_category(CATEGORY_PROCUREMENT)
        assert len(proc_qs) >= 2

    def test_finance_questions(self):
        """财务类问题。"""
        fin_qs = question_bank.by_category(CATEGORY_FINANCE)
        assert len(fin_qs) >= 2

    def test_hr_questions(self):
        """人事类问题。"""
        hr_qs = question_bank.by_category(CATEGORY_HR)
        assert len(hr_qs) >= 3

    def test_data_questions(self):
        """数据权限类问题。"""
        data_qs = question_bank.by_category(CATEGORY_DATA)
        assert len(data_qs) >= 2

    def test_kpi_questions(self):
        """KPI 类问题。"""
        kpi_qs = question_bank.by_category(CATEGORY_KPI)
        assert len(kpi_qs) >= 2


class TestQuestionBankPriority:
    """优先级分布测试。"""

    def test_priority_distribution(self):
        """至少有 P0/P1/P2 三种优先级。"""
        priorities = {q.priority for q in question_bank.all_templates()}
        assert "P0" in priorities
        assert "P1" in priorities
        assert "P2" in priorities

    def test_all_templates_sorted_by_priority(self):
        """all_templates 按 P0→P1→P2 排序。"""
        templates = question_bank.all_templates()
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        for i in range(len(templates) - 1):
            curr = priority_order.get(templates[i].priority, 9)
            next_ = priority_order.get(templates[i + 1].priority, 9)
            assert curr <= next_


class TestAffectedStagesMapping:
    """affected_field → 编译层级映射测试。"""

    @pytest.mark.parametrize(
        "field,expected_stages",
        [
            ("data.crm_fields", ["information", "knowledge"]),
            ("data.sensitive_policy", ["information", "knowledge"]),
            ("organization.departments", ["process", "capability", "runtime"]),
            ("organization.customer_tiers", ["process", "capability", "runtime"]),
            ("process.order_handling", ["process", "capability", "runtime"]),
            ("process.approval_rules", ["process", "capability", "runtime"]),
            ("role.data_permissions", ["capability", "runtime"]),
            ("kpi.targets", ["capability", "runtime"]),
            ("kpi.customer_service.satisfaction", ["capability", "runtime"]),
        ],
    )
    def test_affected_stages_for_field(self, field, expected_stages):
        """affected_field 前缀正确映射到编译层级。"""
        stages = question_bank.affected_stages_for_field(field)
        assert stages == expected_stages

    def test_unknown_field_returns_all_stages(self):
        """未知前缀返回全部 5 级。"""
        stages = question_bank.affected_stages_for_field("unknown.field")
        assert stages == ["information", "knowledge", "process", "capability", "runtime"]

    def test_empty_field_returns_all_stages(self):
        """空字段返回全部 5 级。"""
        stages = question_bank.affected_stages_for_field("")
        assert len(stages) == 5


class TestQuestionBankInstance:
    """QuestionBank 实例方法测试。"""

    def test_total_count(self):
        """问题总数正确。"""
        bank = QuestionBank()
        assert bank.total == len(bank.all_templates())
        assert bank.total >= 20  # 7 大类至少 20 个问题

    def test_categories_returns_seven(self):
        """categories 返回 7 个分类。"""
        assert len(question_bank.categories) == 7

    def test_custom_templates(self):
        """自定义问题模板。"""
        from app.services.interview.question_bank import QuestionTemplate

        custom = QuestionBank([
            QuestionTemplate(
                category="sales",
                question="测试问题？",
                expected_output="测试产出",
                affected_field="process.test",
                priority="P1",
            )
        ])
        assert custom.total == 1
        assert custom.by_category("sales")[0].question == "测试问题？"
