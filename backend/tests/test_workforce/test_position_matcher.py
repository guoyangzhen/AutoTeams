"""岗位匹配器测试（PRD §5.11 岗位匹配机制）。

覆盖 6 维度匹配：
1. 岗位（position）
2. 技能（skills）
3. 知识库（knowledge）
4. 工具（tools）
5. 权限（permissions）
6. system_prompt
"""
import pytest

from app.services.workforce.position_matcher import PositionMatcher, PositionMatch
from app.schemas.compiler import (
    PositionCapability,
    CapabilityMatrix,
    SkillRequirement,
    KnowledgeRequirement,
    ToolRequirement,
    AgentConfigTemplate,
    ToolBinding,
    SkillBinding,
)
from datetime import datetime, timezone


def _make_position(
    position_id="pos_1",
    position_name="销售代表",
    department="销售部",
    level="L2",
    skills=None,
    knowledge=None,
    tools=None,
    permissions=None,
    priority="P0",
) -> PositionCapability:
    return PositionCapability(
        position_id=position_id,
        position_name=position_name,
        department=department,
        level=level,
        required_skills=skills or [],
        required_knowledge=knowledge or [],
        required_tools=tools or [],
        required_permissions=permissions or [],
        kpi_ids=[],
        main_processes=[],
        priority=priority,
    )


def _make_template(
    role_id="sales",
    agent_name="销售数字员工",
    skills=None,
    knowledge_bases=None,
    tools=None,
    permissions=None,
    system_prompt="你是销售数字员工",
) -> AgentConfigTemplate:
    return AgentConfigTemplate(
        agent_id=f"agent-{role_id}",
        role_id=role_id,
        agent_name=agent_name,
        system_prompt=system_prompt,
        skills=skills or [],
        knowledge_bases=knowledge_bases or [],
        tools=tools or [],
        permissions=permissions or [],
    )


class TestPositionMatcherSixDimensions:
    """6 维度匹配测试。"""

    def test_match_returns_all_positions(self):
        """match 对每个岗位返回匹配结果。"""
        matcher = PositionMatcher()
        cm = CapabilityMatrix(
            enterprise_id="ent-1",
            positions=[_make_position("pos_a"), _make_position("pos_b")],
            compiled_at=datetime.now(timezone.utc),
            confidence=0.8,
        )
        matches = matcher.match(cm, [])
        assert len(matches) == 2

    def test_dimension_position(self):
        """维度 1: 岗位 ID/名称匹配。"""
        matcher = PositionMatcher()
        position = _make_position(position_id="sales", position_name="销售代表")
        template = _make_template(role_id="sales", agent_name="销售数字员工")
        match = matcher.match_single(position, template)
        assert match.scores["position"] == 1.0  # ID 完全匹配

    def test_dimension_skills(self):
        """维度 2: 技能匹配。"""
        matcher = PositionMatcher()
        position = _make_position(
            skills=[
                SkillRequirement(skill_name="线索评分", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="销售话术", skill_type="hard", proficiency_level="intermediate", source="sop"),
            ],
        )
        template = _make_template(
            skills=[
                SkillBinding(skill_id="s1", name="线索评分"),
                SkillBinding(skill_id="s2", name="销售话术"),
            ],
        )
        match = matcher.match_single(position, template)
        assert match.scores["skills"] == 1.0

    def test_dimension_skills_partial_match(self):
        """维度 2: 部分技能匹配。"""
        matcher = PositionMatcher()
        position = _make_position(
            skills=[
                SkillRequirement(skill_name="线索评分", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="未知技能", skill_type="hard", proficiency_level="basic", source="sop"),
            ],
        )
        template = _make_template(
            skills=[SkillBinding(skill_id="s1", name="线索评分")],
        )
        match = matcher.match_single(position, template)
        assert match.scores["skills"] == 0.5  # 2 个技能匹配 1 个

    def test_dimension_knowledge(self):
        """维度 3: 知识库匹配。"""
        matcher = PositionMatcher()
        position = _make_position(
            knowledge=[
                KnowledgeRequirement(knowledge_domain="产品介绍", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="竞品分析", coverage=0.5),
            ],
        )
        template = _make_template(knowledge_bases=["产品介绍", "竞品分析"])
        match = matcher.match_single(position, template)
        assert match.scores["knowledge"] == 1.0

    def test_dimension_tools(self):
        """维度 4: 工具匹配。"""
        matcher = PositionMatcher()
        position = _make_position(
            tools=[
                ToolRequirement(tool_name="CRM系统", tool_type="api", required_permissions=["read"]),
            ],
        )
        template = _make_template(
            tools=[ToolBinding(tool_id="t1", name="CRM系统", tool_type="api", permissions=["read"])],
        )
        match = matcher.match_single(position, template)
        assert match.scores["tools"] == 1.0

    def test_dimension_permissions(self):
        """维度 5: 权限匹配。"""
        matcher = PositionMatcher()
        position = _make_position(permissions=["sales.view", "sales.quote"])
        template = _make_template(permissions=["sales.view", "sales.quote", "customer.view"])
        match = matcher.match_single(position, template)
        assert match.scores["permissions"] == 1.0

    def test_dimension_permissions_partial(self):
        """维度 5: 部分权限匹配。"""
        matcher = PositionMatcher()
        position = _make_position(permissions=["sales.view", "sales.quote"])
        template = _make_template(permissions=["sales.view"])
        match = matcher.match_single(position, template)
        assert match.scores["permissions"] == 0.5

    def test_dimension_system_prompt(self):
        """维度 6: system_prompt 匹配。"""
        matcher = PositionMatcher()
        position = _make_position(position_name="销售代表")
        template = _make_template(system_prompt="你是销售数字员工，负责销售工作")
        match = matcher.match_single(position, template)
        assert match.scores["system_prompt"] > 0


class TestPositionMatchScoring:
    """匹配评分与阈值测试。"""

    def test_overall_score_is_weighted_average(self):
        """overall_score 是 6 维度加权平均。"""
        match = PositionMatch(
            position=_make_position(),
            scores={
                "position": 1.0,
                "skills": 0.8,
                "knowledge": 0.6,
                "tools": 0.5,
                "permissions": 1.0,
                "system_prompt": 0.5,
            },
        )
        # 权重: position=0.20, skills=0.25, knowledge=0.15, tools=0.10, permissions=0.10, system_prompt=0.20
        expected = (1.0*0.20 + 0.8*0.25 + 0.6*0.15 + 0.5*0.10 + 1.0*0.10 + 0.5*0.20)
        assert abs(match.overall_score - expected) < 0.001

    def test_is_matched_above_threshold(self):
        """overall_score >= 0.5 时 is_matched=True。"""
        match = PositionMatch(
            position=_make_position(),
            scores={
                "position": 1.0, "skills": 1.0, "knowledge": 1.0,
                "tools": 1.0, "permissions": 1.0, "system_prompt": 1.0,
            },
        )
        assert match.is_matched is True

    def test_is_matched_below_threshold(self):
        """overall_score < 0.5 时 is_matched=False。"""
        match = PositionMatch(
            position=_make_position(),
            scores={
                "position": 0.0, "skills": 0.0, "knowledge": 0.0,
                "tools": 0.0, "permissions": 0.0, "system_prompt": 0.0,
            },
        )
        assert match.is_matched is False

    def test_to_dict_contains_all_fields(self):
        """to_dict 包含所有必要字段。"""
        match = PositionMatch(position=_make_position())
        d = match.to_dict()
        assert "position_id" in d
        assert "position_name" in d
        assert "department" in d
        assert "priority" in d
        assert "scores" in d
        assert "overall_score" in d
        assert "is_matched" in d
        assert "required_skills" in d
        assert "required_knowledge" in d
        assert "required_tools" in d
        assert "required_permissions" in d


class TestPositionMatcherNoTemplate:
    """无 Agent 模板时的匹配（self-scoring）。"""

    def test_no_template_uses_self_scoring(self):
        """无模板时基于岗位自身完整度评分。"""
        matcher = PositionMatcher()
        position = _make_position(
            skills=[SkillRequirement(skill_name="技能1", skill_type="hard", proficiency_level="basic", source="sop")],
            knowledge=[KnowledgeRequirement(knowledge_domain="知识1", coverage=0.5)],
            tools=[ToolRequirement(tool_name="工具1", tool_type="api", required_permissions=[])],
            permissions=["perm1"],
        )
        match = matcher.match_single(position, None)
        # 无模板时 position 维度为 1.0（岗位本身存在）
        assert match.scores["position"] == 1.0
        # system_prompt 维度为 0.0（无模板则无 system_prompt）
        assert match.scores["system_prompt"] == 0.0
        # 其他维度基于完整度
        assert match.scores["skills"] > 0
        assert match.scores["knowledge"] > 0

    def test_find_best_template_picks_best_match(self):
        """_find_best_template 选择最佳匹配的模板。"""
        matcher = PositionMatcher()
        position = _make_position(position_id="sales", position_name="销售代表")
        templates = [
            _make_template(role_id="hr", agent_name="HR员工"),
            _make_template(role_id="sales", agent_name="销售员工"),
            _make_template(role_id="finance", agent_name="财务员工"),
        ]
        best = matcher._find_best_template(position, templates)
        assert best.role_id == "sales"

    def test_find_best_template_empty(self):
        """空模板列表返回 None。"""
        matcher = PositionMatcher()
        position = _make_position()
        assert matcher._find_best_template(position, []) is None


class TestPositionMatcherKeywords:
    """关键词提取测试。"""

    def test_extract_keywords_sales(self):
        """提取"销售"关键词。"""
        kws = PositionMatcher._extract_keywords("销售代表")
        assert "销售" in kws or "sales" in kws

    def test_extract_keywords_customer_service(self):
        """提取"客服"关键词。"""
        kws = PositionMatcher._extract_keywords("客服专员")
        assert "客服" in kws or "customer_service" in kws

    def test_extract_keywords_empty(self):
        """空文本返回空列表。"""
        assert PositionMatcher._extract_keywords("") == []
