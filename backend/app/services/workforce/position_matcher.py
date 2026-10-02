"""岗位匹配器：6 维度匹配（PRD §5.11 岗位匹配机制）。

6 个匹配维度：
1. 岗位（position）：position_id 匹配
2. 技能（skills）：required_skills → 匹配可注册的 skill
3. 知识库（knowledge_bases）：required_knowledge → 关联知识库 ID
4. 工具（tools）：required_tools → 匹配已安装工具
5. 权限（permissions）：required_permissions → 生成权限令牌
6. system_prompt：岗位职责 + SOP + 知识 → 自动生成提示词

输入：CapabilityMatrix（WT1）+ AgentConfigTemplate 列表（WT2）
输出：PositionMatch 列表（含 6 维度评分 + 总体匹配度）
"""
import logging
from typing import Optional

from app.schemas.compiler import (
    PositionCapability,
    AgentConfigTemplate,
)

logger = logging.getLogger(__name__)


class PositionMatch:
    """单岗位匹配结果。"""

    def __init__(
        self,
        position: PositionCapability,
        agent_template: Optional[AgentConfigTemplate] = None,
        scores: Optional[dict] = None,
    ):
        self.position = position
        self.agent_template = agent_template
        # 6 维度评分（0-1）
        self.scores = scores or {
            "position": 0.0,
            "skills": 0.0,
            "knowledge": 0.0,
            "tools": 0.0,
            "permissions": 0.0,
            "system_prompt": 0.0,
        }

    @property
    def overall_score(self) -> float:
        """总体匹配度（6 维度加权平均）。"""
        weights = {
            "position": 0.20,
            "skills": 0.25,
            "knowledge": 0.15,
            "tools": 0.10,
            "permissions": 0.10,
            "system_prompt": 0.20,
        }
        return sum(self.scores.get(k, 0.0) * w for k, w in weights.items())

    @property
    def is_matched(self) -> bool:
        """是否达到匹配阈值（总体 ≥ 0.5）。"""
        return self.overall_score >= 0.5

    def to_dict(self) -> dict:
        return {
            "position_id": self.position.position_id,
            "position_name": self.position.position_name,
            "department": self.position.department,
            "level": self.position.level,
            "priority": self.position.priority,
            "scores": self.scores,
            "overall_score": round(self.overall_score, 4),
            "is_matched": self.is_matched,
            "required_skills": [s.model_dump() for s in self.position.required_skills],
            "required_knowledge": [k.model_dump() for k in self.position.required_knowledge],
            "required_tools": [t.model_dump() for t in self.position.required_tools],
            "required_permissions": self.position.required_permissions,
            "kpi_ids": self.position.kpi_ids,
            "main_processes": self.position.main_processes,
        }


class PositionMatcher:
    """6 维度岗位匹配器。

    用法：
        matcher = PositionMatcher()
        matches = matcher.match(capability_matrix, agent_templates)
    """

    def match(
        self,
        capability_matrix,
        agent_templates: list[AgentConfigTemplate],
    ) -> list[PositionMatch]:
        """对 CapabilityMatrix 中的每个岗位进行 6 维度匹配。

        Args:
            capability_matrix: WT1 产出的 CapabilityMatrix（§10.3 契约）
            agent_templates: WT2 产出的 AgentConfigTemplate 列表（§10.5 契约）

        Returns:
            list[PositionMatch]: 每个岗位的匹配结果
        """
        results: list[PositionMatch] = []

        for position in capability_matrix.positions:
            # 找到最佳匹配的 Agent 模板
            best_template = self._find_best_template(position, agent_templates)
            scores = self._score_all_dimensions(position, best_template)
            results.append(PositionMatch(position, best_template, scores))

        return results

    def match_single(
        self,
        position: PositionCapability,
        agent_template: Optional[AgentConfigTemplate] = None,
    ) -> PositionMatch:
        """单个岗位匹配。"""
        scores = self._score_all_dimensions(position, agent_template)
        return PositionMatch(position, agent_template, scores)

    def _find_best_template(
        self,
        position: PositionCapability,
        agent_templates: list[AgentConfigTemplate],
    ) -> Optional[AgentConfigTemplate]:
        """找到与岗位最佳匹配的 Agent 模板。

        匹配策略：按 position_name/position_id 与 template.role_id/agent_name 的相似度。
        """
        if not agent_templates:
            return None

        best = None
        best_score = -1.0

        pos_name_lower = position.position_name.lower()
        pos_id_lower = position.position_id.lower()

        for tpl in agent_templates:
            role_lower = tpl.role_id.lower()
            name_lower = tpl.agent_name.lower()

            # 简单的字符串匹配评分
            score = 0.0
            if role_lower in pos_id_lower or pos_id_lower in role_lower:
                score += 0.5
            if role_lower in pos_name_lower or pos_name_lower in role_lower:
                score += 0.5
            # 关键词匹配
            keywords = self._extract_keywords(position.position_name)
            for kw in keywords:
                if kw in role_lower or kw in name_lower:
                    score += 0.2

            if score > best_score:
                best_score = score
                best = tpl

        return best

    def _score_all_dimensions(
        self,
        position: PositionCapability,
        template: Optional[AgentConfigTemplate],
    ) -> dict:
        """计算 6 维度评分。"""
        if template is None:
            # 无模板时，仅基于岗位自身完整度评分
            return {
                "position": 1.0,  # 岗位本身存在即 1.0
                "skills": self._score_skills_self(position),
                "knowledge": self._score_knowledge_self(position),
                "tools": self._score_tools_self(position),
                "permissions": self._score_permissions_self(position),
                "system_prompt": 0.0,  # 无模板则无 system_prompt
            }

        return {
            "position": self._match_position(position, template),
            "skills": self._match_skills(position, template),
            "knowledge": self._match_knowledge(position, template),
            "tools": self._match_tools(position, template),
            "permissions": self._match_permissions(position, template),
            "system_prompt": self._match_system_prompt(position, template),
        }

    # === 维度 1: 岗位匹配 ===

    def _match_position(self, position: PositionCapability, template: AgentConfigTemplate) -> float:
        """岗位 ID/名称匹配度。"""
        pos_name = position.position_name.lower()
        role_id = template.role_id.lower()
        agent_name = template.agent_name.lower()

        if role_id in position.position_id.lower() or position.position_id.lower() in role_id:
            return 1.0
        if role_id in pos_name or pos_name in role_id:
            return 0.8
        # 关键词重叠
        keywords = self._extract_keywords(position.position_name)
        matched = sum(1 for kw in keywords if kw in role_id or kw in agent_name)
        if keywords:
            return min(matched / len(keywords), 1.0)
        return 0.0

    # === 维度 2: 技能匹配 ===

    def _match_skills(self, position: PositionCapability, template: AgentConfigTemplate) -> float:
        """技能需求与模板技能的匹配度。"""
        if not position.required_skills:
            return 1.0  # 无技能需求视为完全匹配
        if not template.skills:
            return 0.0

        template_skill_names = {s.name.lower() for s in template.skills}
        matched = 0
        for req in position.required_skills:
            req_name = req.skill_name.lower()
            # 精确匹配或包含匹配
            if any(req_name in tsn or tsn in req_name for tsn in template_skill_names):
                matched += 1

        return matched / len(position.required_skills)

    def _score_skills_self(self, position: PositionCapability) -> float:
        """无模板时，基于技能需求完整度评分。"""
        if not position.required_skills:
            return 0.5
        # 有技能需求即有一定分数
        return min(0.3 + 0.1 * len(position.required_skills), 0.8)

    # === 维度 3: 知识库匹配 ===

    def _match_knowledge(self, position: PositionCapability, template: AgentConfigTemplate) -> float:
        """知识需求与模板知识库的匹配度。"""
        if not position.required_knowledge:
            return 1.0
        if not template.knowledge_bases:
            return 0.0

        template_kb = {kb.lower() for kb in template.knowledge_bases}
        matched = 0
        for req in position.required_knowledge:
            domain = req.knowledge_domain.lower()
            if any(domain in kb or kb in domain for kb in template_kb):
                matched += 1

        return matched / len(position.required_knowledge)

    def _score_knowledge_self(self, position: PositionCapability) -> float:
        """无模板时，基于知识需求完整度评分。"""
        if not position.required_knowledge:
            return 0.5
        return min(0.3 + 0.1 * len(position.required_knowledge), 0.8)

    # === 维度 4: 工具匹配 ===

    def _match_tools(self, position: PositionCapability, template: AgentConfigTemplate) -> float:
        """工具需求与模板工具的匹配度。"""
        if not position.required_tools:
            return 1.0
        if not template.tools:
            return 0.0

        template_tool_names = {t.name.lower() for t in template.tools}
        matched = 0
        for req in position.required_tools:
            req_name = req.tool_name.lower()
            if any(req_name in ttn or ttn in req_name for ttn in template_tool_names):
                matched += 1

        return matched / len(position.required_tools)

    def _score_tools_self(self, position: PositionCapability) -> float:
        """无模板时，基于工具需求完整度评分。"""
        if not position.required_tools:
            return 0.5
        return min(0.3 + 0.1 * len(position.required_tools), 0.8)

    # === 维度 5: 权限匹配 ===

    def _match_permissions(self, position: PositionCapability, template: AgentConfigTemplate) -> float:
        """权限需求与模板权限的匹配度。"""
        if not position.required_permissions:
            return 1.0
        if not template.permissions:
            return 0.0

        template_perms = {p.lower() for p in template.permissions}
        matched = sum(1 for p in position.required_permissions if p.lower() in template_perms)
        return matched / len(position.required_permissions)

    def _score_permissions_self(self, position: PositionCapability) -> float:
        """无模板时，基于权限需求完整度评分。"""
        if not position.required_permissions:
            return 0.5
        return min(0.3 + 0.1 * len(position.required_permissions), 0.8)

    # === 维度 6: system_prompt 匹配 ===

    def _match_system_prompt(self, position: PositionCapability, template: AgentConfigTemplate) -> float:
        """岗位职责与模板 system_prompt 的匹配度。"""
        if not template.system_prompt:
            return 0.0

        prompt_lower = template.system_prompt.lower()
        keywords = self._extract_keywords(position.position_name)
        if not keywords:
            return 0.5

        matched = sum(1 for kw in keywords if kw in prompt_lower)
        return min(matched / len(keywords), 1.0)

    # === 辅助方法 ===

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """从文本中提取关键词（简单分词）。"""
        if not text:
            return []
        # 中文：按常见岗位关键词匹配
        keyword_map = {
            "销售": ["sales", "sale", "销售"],
            "售前": ["pre_sales", "presales", "售前", "技术支持"],
            "财务": ["finance", "财务"],
            "客服": ["customer_service", "cs", "客服"],
            "售后": ["after_sales", "after-sales", "售后", "服务"],
            "运营": ["ops", "运营"],
            "人事": ["hr", "人事"],
            "法务": ["legal", "法务"],
            "医疗": ["medical", "医疗"],
            "教育": ["education", "edu", "教育"],
        }
        result = []
        text_lower = text.lower()
        for cn, en_list in keyword_map.items():
            if cn in text:
                result.extend(en_list)
            for en in en_list:
                if en in text_lower:
                    if en not in result:
                        result.append(en)
        return result or [text_lower]
