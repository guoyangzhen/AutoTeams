"""行业模板资产的 Pydantic 契约。

依据重构计划 §4.2.2「服务层重构 — 消灭巨型文件」：``template_service.py`` 原有
1194 行里约 790 行是内联的模板字面量。本包把模板数据外提为
``assets/*.json``，并用这里的模型做**加载期强校验**——资产写错字段名、
漏必填项、引用了不存在的 skill code，都会在服务启动时立刻失败，
而不是等到某个企业套用模板时才发现。

字段与 ORM 的对应（保持一一映射，不引入新概念）：
- :class:`SkillAsset`      → ``SkillTemplate``  (code/name/skill_type/description/config/output_schema)
- :class:`AgentAsset`      → ``AgentTemplate``  (role/name/description/system_prompt/skill_ids/
                                            knowledge_structure/sample_dialogues)
- :class:`IndustryAsset`   → 一个行业 = 1 份 JSON，含若干 agent + 若干 skill
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 对齐 Skill.skill_type 的既有取值域
SkillType = Literal[
    "text_generation",
    "text_summarization",
    "text_classification",
    "data_extraction",
    "translation",
    "code_generation",
    "custom",
]

# 岗位角色（对齐 AgentTemplate.role 的既有取值域）
Role = Literal[
    "customer_service",
    "sales",
    "hr",
    "ops",
    "finance",
    "medical",
    "education",
    "legal",
    "consulting",
    "manufacturing",
    "ecommerce",
    "cross_border",
    "data_analyst",
]

# 6 大中小企业行业（重构计划 §决策一）
INDUSTRIES = (
    "manufacturing",   # 智能制造
    "ecommerce",        # 电商零售
    "cross_border",     # 跨境出海
    "consulting",       # 专业咨询
    "education",        # 教育培训
    "healthcare",       # 医疗健康
)


class SkillAsset(BaseModel):
    """一个技能模板资产（对应一条 SkillTemplate 记录）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=64, description="语义化标识，全局唯一")
    name: str = Field(..., min_length=1, max_length=255)
    skill_type: SkillType
    description: str = Field(default="", max_length=2000)
    config: Dict[str, Any] = Field(default_factory=dict, description="实例化时写入 Skill.config")
    output_schema: Dict[str, Any] = Field(
        default_factory=dict, description="输出结构声明（JSON Schema 风格）"
    )


class KnowledgeStructure(BaseModel):
    """推荐的知识库结构。"""

    model_config = ConfigDict(extra="forbid")

    categories: List[str] = Field(default_factory=list)
    file_types: List[str] = Field(default_factory=list)


class SampleDialogue(BaseModel):
    """示例对话。"""

    model_config = ConfigDict(extra="forbid")

    user: str
    assistant: str


class AgentAsset(BaseModel):
    """一个数字员工能力模板资产（对应一条 AgentTemplate 记录）。"""

    model_config = ConfigDict(extra="forbid")

    role: Role
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(..., min_length=1, description="中文专业 system_prompt")
    skill_ids: List[str] = Field(
        default_factory=list, description="引用的 SkillAsset.code 列表"
    )
    knowledge_structure: KnowledgeStructure = Field(default_factory=KnowledgeStructure)
    sample_dialogues: List[SampleDialogue] = Field(default_factory=list)


class IndustryAsset(BaseModel):
    """一个行业的完整模板资产（= 一份 JSON 文件）。"""

    model_config = ConfigDict(extra="forbid")

    industry: str = Field(..., min_length=1, description="行业标识，须在 INDUSTRIES 内")
    industry_name: str = Field(..., min_length=1, description="行业中文名")
    description: str = Field(default="", max_length=2000)
    agents: List[AgentAsset] = Field(default_factory=list)
    skills: List[SkillAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_references(self) -> "IndustryAsset":
        """skill_ids 必须指向本行业内真实存在的 skill code。

        跨行业引用会让"套用模板"在运行时才炸，这里提前拦。
        """
        known = {s.code for s in self.skills}
        if len(known) != len(self.skills):
            raise ValueError(f"行业 {self.industry} 内存在重复的 skill code")
        for agent in self.agents:
            missing = [sid for sid in agent.skill_ids if sid not in known]
            if missing:
                raise ValueError(
                    f"行业 {self.industry} 的岗位 {agent.role} 引用了不存在的 skill: {missing}"
                )
        return self

    def all_skill_dicts(self) -> List[Dict[str, Any]]:
        """Skill 资产 → 兼容 4.0 ``PRESET_SKILL_TEMPLATES`` 的 dict 形态。"""
        return [s.model_dump() for s in self.skills]

    def all_agent_dicts(self) -> List[Dict[str, Any]]:
        """Agent 资产 → 兼容 4.0 ``PRESET_AGENT_TEMPLATES`` 的 dict 形态。"""
        return [a.model_dump() for a in self.agents]
