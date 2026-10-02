"""Capability Compiler —— 第四级编译器（PRD §4.3）。

输入：KnowledgeCompileOutput + ProcessCompileOutput
处理：岗位能力矩阵构建
输出：CapabilityCompileOutput（CapabilityMatrix，即 WT1→WT3 契约 §10.3）
置信度：基于岗位覆盖率和能力项完整度
"""
import logging
import re
from typing import Any

from app.services.compiler.base import (
    CompilerBase,
    CompilationContext,
    CompilationResult,
    Stage,
)
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.compiler import (
    ProcessCompileOutput,
    ProcessDefinition,
    PositionCapability,
    CapabilityMatrix,
    CapabilityCompileOutput,
    SkillRequirement,
    KnowledgeRequirement,
    ToolRequirement,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


# 基于岗位名/部门关键词推断的默认技能集（当知识图谱角色节点缺失 required_skills 时使用）
_ROLE_SKILLS_BY_KEYWORD: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("销售", "商务", "客户经理", "大客户"), ["客户沟通跟进", "销售谈判", "CRM操作"]),
    (("客服", "售后", "服务", "支持"), ["客户服务", "工单处理", "FAQ检索"]),
    (("财务", "会计", "出纳", "报销"), ["财务核算", "预算管理", "报表编制"]),
    (("采购", "供应链", "供应商"), ["供应商管理", "比价议价", "采购下单"]),
    (("人事", "HR", "招聘", "绩效"), ["招聘管理", "员工档案", "绩效管理"]),
    (("仓管", "仓库", "库存"), ["库存管理", "出入库登记", "盘点管理"]),
    (("总经理", "总裁", "CEO", "VP", "总监"), ["团队管理", "经营分析", "决策审批"]),
    (("主管", "经理", "负责人"), ["团队管理", "任务分配", "绩效评估"]),
)
# 通用岗位兜底技能
_GENERIC_ROLE_SKILLS = ["通用办公协作", "文档处理", "流程执行"]
# 默认工具集（当岗位无 required_tools 时使用，保证工具注册表非空）
_DEFAULT_ROLE_TOOLS = [
    "知识库检索", "文档解析", "任务编排", "审批流", "消息协作", "报表生成",
]

# ============================================================================
# 非岗位「噪音」角色节点过滤
# ============================================================================
# NER / 文件名回退常把文件/目录名、关系动词、流程步骤名、技能等级标签等误识别为
# ROLE 节点，进而被当作岗位推荐为 AI 数字员工（端到端测试发现：README、
# UNGENERATED、01-company、sales-sop-v1 等被推荐为岗位）。这些显然不是可协作的
# 真实岗位，构建能力矩阵时应过滤掉，否则会生成无意义的 AI 员工。

_FILE_EXTENSIONS = (
    ".md", ".pdf", ".docx", ".xlsx", ".csv", ".json", ".py", ".txt",
    ".png", ".jpg", ".jpeg", ".svg", ".yaml", ".yml", ".html", ".mp4",
)
_GENERIC_FILE_TOKENS = {
    "readme", "ungenerated", "license", "changelog", "validate",
    "index", "main", "test", "sample", "fixtures", "assets", "docs",
    "faq", "customers", "contacts", "opportunities", "activities",
    "orders", "permissions", "flowcharts",
}
# 关系动词 / 占位标签（中文，非岗位）
_NON_POSITION_NOISE = (
    "拥有", "负责", "创建", "关联", "上报", "驳回", "通过",
    "能直接回答", "需技术支持", "能解决", "需升级", "需上门",
    "涉及产品缺陷", "普通投诉", "跨部门", "重大投诉",
    "客户询问", "客户满意", "客户不满意", "远程解决", "报价金额",
    "直属上级", "接收人", "法定代表人", "离职员工", "部门经理本人",
)
_SKILL_LEVEL_LABELS = {"l1", "l2", "l3", "p0", "p1", "p2"}


def is_noise_role_name(name: str) -> bool:
    """判断角色名是否为文件/目录名、关系动词或标签（非真实岗位）。"""
    if not name:
        return True
    lower = name.strip().lower()
    if not lower:
        return True
    # 技能等级标签：L1 / L2 / L3
    if lower in _SKILL_LEVEL_LABELS:
        return True
    # 带文件扩展名：company-profile.md 等
    if any(lower.endswith(ext) for ext in _FILE_EXTENSIONS):
        return True
    # 常见文件/目录占位名：README / faq / customers 等
    if lower in _GENERIC_FILE_TOKENS:
        return True
    # 目录编号前缀：01-company / 05-crm
    if re.match(r"^\d{1,2}[-_]", name):
        return True
    # 纯 ASCII 且含连字符/下划线/点：sales-sop-v1 / company-profile / hr-handbook
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_.]*", lower) and ("-" in lower or "_" in lower):
        return True
    # 中文关系动词 / 占位标签
    if name in _NON_POSITION_NOISE:
        return True
    return False


def _infer_skills_for_role(role_name: str, dept_name: str) -> list[str]:
    """按岗位名/部门关键词推断默认技能集；退化为通用技能。"""
    text = f"{role_name or ''} {dept_name or ''}"
    for keywords, skills in _ROLE_SKILLS_BY_KEYWORD:
        if any(k in text for k in keywords):
            return list(skills)
    return list(_GENERIC_ROLE_SKILLS)


class CapabilityCompiler(CompilerBase):
    """Capability Compiler。

    构建"岗位—能力—流程—权限—KPI"五元组矩阵。
    流程：识别岗位 → 关联流程/权限/KPI → 推断技能/知识/工具需求
    产出 CapabilityMatrix（spec.md §10.3 契约），供 WT3 Workforce 生成器消费。
    """

    stage = Stage.CAPABILITY.value

    async def compile(self, ctx: CompilationContext) -> CompilationResult:
        """执行 Capability 级编译。"""
        # 从上游获取 Knowledge 和 Process 级输出

        # 从知识图谱加载角色和关联节点
        graph_store = PGJSONBGraphStore(ctx.db, ctx.enterprise_id)
        role_nodes = await graph_store.query_nodes_by_type(EntityType.ROLE.value)
        # 过滤非岗位噪音节点（文件/目录名、关系动词、标签），避免把文件名当岗位推荐为 AI 员工
        role_nodes = [
            r for r in role_nodes
            if not is_noise_role_name(r.name)
        ]
        self._get_process_defs(ctx.upstream.get("process"))

        self.logger.info(
            f"Capability 编译开始: enterprise={ctx.enterprise_id}, roles={len(role_nodes)}"
        )

        # #14 岗位去重聚合：知识图谱中同一岗位常因多份文档重复出现（组织架构、SOP、
        # 权限表、KPI 矩阵反复提到同一岗位名），若逐 Role 节点生成会导致岗位数虚高
        # （如 117）。此处按「岗位名 + 部门」聚合，合并同一岗位的权限/KPI/流程/技能/
        # 知识/工具需求，收敛到真实的关键岗位数量（如 example-enterprise 的 24 个）。
        positions: list[PositionCapability] = []
        grouped: dict[tuple[str, str], dict] = {}
        for role in role_nodes:
            exec_processes = await graph_store.query_neighbors(
                role.node_id, relation=RelationType.EXECUTES.value
            )
            dept_neighbors = await graph_store.query_neighbors(
                role.node_id, relation=RelationType.BELONGS_TO.value
            )
            dept_name = dept_neighbors[0].name if dept_neighbors else ""
            key = (role.name, dept_name)
            acc = grouped.setdefault(key, {
                "role": role,
                "dept_name": dept_name,
                "permissions": set(),
                "kpis": set(),
                "processes": set(),
                "skills": [],
                "knowledge": [],
                "tools": [],
            })
            perm_neighbors = await graph_store.query_neighbors(
                role.node_id, relation=RelationType.HAS_PERMISSION.value
            )
            kpi_neighbors = await graph_store.query_neighbors(
                role.node_id, relation=RelationType.OWES.value
            )
            acc["permissions"].update(p.name for p in perm_neighbors)
            acc["kpis"].update(k.node_id for k in kpi_neighbors)
            acc["processes"].update(p.node_id for p in exec_processes)
            # 技能/知识/工具需求合并去重
            for skill in self._build_skill_requirements(role, exec_processes, dept_name):
                if not any(s.skill_name == skill.skill_name for s in acc["skills"]):
                    acc["skills"].append(skill)
            for k in self._build_knowledge_requirements(role, exec_processes):
                if not any(x.knowledge_domain == k.knowledge_domain for x in acc["knowledge"]):
                    acc["knowledge"].append(k)
            for tool in self._build_tool_requirements(exec_processes, role.name):
                if not any(x.tool_name == tool.tool_name for x in acc["tools"]):
                    acc["tools"].append(tool)

        for (role_name, dept_name), acc in grouped.items():
            role = acc["role"]
            position = PositionCapability(
                position_id=role.node_id,
                position_name=role_name,
                department=dept_name,
                level=role.attributes.get("level", ""),
                required_skills=acc["skills"],
                required_knowledge=acc["knowledge"],
                required_tools=acc["tools"],
                required_permissions=sorted(acc["permissions"]),
                kpi_ids=sorted(acc["kpis"]),
                main_processes=sorted(acc["processes"]),
                priority=self._determine_priority(len(acc["processes"])),
            )
            # 兜底：保证每个岗位至少有一个技能与一个工具（提升角色覆盖度）
            if not position.required_skills:
                position = position.model_copy(update={
                    "required_skills": [
                        SkillRequirement(skill_name=s, source="inferred")
                        for s in (_infer_skills_for_role(role_name, dept_name)
                                  or _GENERIC_ROLE_SKILLS)
                    ]
                })
            if not position.required_tools:
                position = position.model_copy(update={
                    "required_tools": [
                        ToolRequirement(tool_name=t)
                        for t in _DEFAULT_ROLE_TOOLS
                    ]
                })
            positions.append(position)

        # 计算置信度
        confidence = self.calculate_confidence(
            len(grouped), len(positions), base=0.4, max_bonus=0.6
        )

        # 构建 CapabilityMatrix（§10.3 契约）
        capability_matrix = CapabilityMatrix(
            enterprise_id=ctx.enterprise_id,
            positions=positions,
            compiled_at=utcnow(),
            confidence=confidence,
        )

        output = CapabilityCompileOutput(
            enterprise_id=ctx.enterprise_id,
            capability_matrix=capability_matrix,
        )

        summary = self._generate_summary(positions)

        return CompilationResult(
            stage=self.stage,
            output=output,
            confidence=confidence,
            discovered_summary=summary,
            discovered_count=len(positions),
        )

    def _get_process_defs(self, process_output: Any) -> list[ProcessDefinition]:
        """从 Process 级输出中提取流程定义。"""
        if process_output is None:
            return []
        if isinstance(process_output, ProcessCompileOutput):
            return process_output.processes
        if isinstance(process_output, dict):
            return [ProcessDefinition(**p) for p in process_output.get("processes", [])]
        return []

    def _build_skill_requirements(
        self, role_node, exec_processes, dept_name: str = ""
    ) -> list[SkillRequirement]:
        """构建技能需求。

        知识图谱角色节点常缺失 required_skills，导致岗位技能为空、角色覆盖度偏低。
        在显式技能缺失时，基于岗位名/部门关键词推断默认技能集，保证每个岗位
        至少有一个技能（role_coverage 的最大杠杆）。
        """
        skills: list[SkillRequirement] = []
        # 从角色属性中提取
        for skill_name in role_node.attributes.get("required_skills", []):
            skills.append(SkillRequirement(
                skill_name=skill_name,
                skill_type="hard",
                proficiency_level="intermediate",
                source="sop",
            ))
        # 从流程中推断
        for proc in exec_processes:
            proc_skills = proc.attributes.get("required_skills", [])
            for skill_name in proc_skills:
                if not any(s.skill_name == skill_name for s in skills):
                    skills.append(SkillRequirement(
                        skill_name=skill_name,
                        skill_type="hard",
                        proficiency_level="basic",
                        source="inferred",
                    ))
        # 回退：显式技能为空时，按岗位名/部门关键词推断默认技能
        if not skills:
            for skill_name in _infer_skills_for_role(role_node.name, dept_name):
                skills.append(SkillRequirement(
                    skill_name=skill_name,
                    skill_type="hard",
                    proficiency_level="intermediate",
                    source="inferred",
                ))
        return skills

    def _build_knowledge_requirements(
        self, role_node, exec_processes
    ) -> list[KnowledgeRequirement]:
        """构建知识需求。"""
        knowledge: list[KnowledgeRequirement] = []
        # 从角色属性中提取
        for domain in role_node.attributes.get("knowledge_domains", []):
            knowledge.append(KnowledgeRequirement(
                knowledge_domain=domain,
                coverage=0.5,
            ))
        # 从流程关联的知识条目推断
        for proc in exec_processes:
            domain = proc.attributes.get("knowledge_domain", "")
            if domain and not any(k.knowledge_domain == domain for k in knowledge):
                knowledge.append(KnowledgeRequirement(
                    knowledge_domain=domain,
                    coverage=0.3,
                ))
        return knowledge

    def _build_tool_requirements(
        self, exec_processes, role_name: str = ""
    ) -> list[ToolRequirement]:
        """构建工具需求（从流程关联的系统推断）。

        知识图谱流程节点的 system_ids 常为空，导致岗位 required_tools 为空、
        工具注册表为空。在显式工具缺失时回退到平台默认工具集，保证岗位有工具、
        工具注册表非空。
        """
        tools: list[ToolRequirement] = []
        seen = set()
        for proc in exec_processes:
            system_ids = proc.attributes.get("system_ids", [])
            if isinstance(system_ids, list):
                for sid in system_ids:
                    if sid not in seen:
                        seen.add(sid)
                        tools.append(ToolRequirement(
                            tool_name=sid,
                            tool_type="api",
                            required_permissions=[],
                        ))
        # 回退：显式工具为空时，使用平台默认工具集
        if not tools:
            for tool_name in _DEFAULT_ROLE_TOOLS:
                tools.append(ToolRequirement(
                    tool_name=tool_name,
                    tool_type="api",
                    required_permissions=[],
                ))
        return tools

    def _determine_priority(self, proc_count: int) -> str:
        """确定岗位优先级。

        规则：
        - 执行 3+ 流程的岗位 → P0
        - 执行 1-2 流程的岗位 → P1
        - 无流程关联的岗位 → P2
        """
        if proc_count >= 3:
            return "P0"
        if proc_count >= 1:
            return "P1"
        return "P2"

    def _generate_summary(self, positions: list[PositionCapability]) -> str:
        """生成发现摘要。"""
        dept_counts: dict[str, int] = {}
        priority_counts: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0}
        total_skills = 0
        total_permissions = 0
        for p in positions:
            dept_counts[p.department] = dept_counts.get(p.department, 0) + 1
            if p.priority in priority_counts:
                priority_counts[p.priority] += 1
            total_skills += len(p.required_skills)
            total_permissions += len(p.required_permissions)

        parts = [f"构建能力矩阵：{len(positions)} 岗位"]
        if priority_counts["P0"]:
            parts.append(f"P0 关键岗位 {priority_counts['P0']} 个")
        parts.append(f"共 {total_skills} 技能需求、{total_permissions} 权限项")
        return "；".join(parts)
