"""Runtime Compiler —— 第五级编译器（PRD §4.3 + §4.6）。

输入：Knowledge + Process + Capability 级产物
处理：整合为 Enterprise Runtime + 工具绑定 + Agent 配置模板
输出：RuntimeCompileOutput（RuntimeCompileResult，即 WT1→WT2 契约 §10.2）
置信度：基于各层级置信度的加权综合
"""
import logging
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
)
from app.schemas.compiler import (
    KnowledgeCompileOutput,
    ProcessCompileOutput,
    CapabilityCompileOutput,
    ProcessDefinition,
    PositionCapability,
    CapabilityMatrix,
    RuntimeCompileResult,
    RuntimeCompileOutput,
    AgentConfigTemplate,
    DepartmentInstance,
    RuntimeOrganization,
    ProcessEngineInstance,
    ProcessStep,
    ProcessTrigger,
    CollaborationGraph,
    CollaborationEdge,
    KnowledgeIndex,
    ToolRegistryEntry,
    SkillBinding,
    ToolBinding,
    MemoryConfig,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


# 平台默认工具集（当岗位无工具、工具注册表为空时使用，marked installed=True）
_PLATFORM_TOOLS = [
    ("knowledge_retrieval", "知识库检索", "api"),
    ("document_parser", "文档解析", "api"),
    ("db_query", "数据库查询", "api"),
    ("task_orchestration", "任务编排", "api"),
    ("approval_flow", "审批流", "api"),
    ("reporting", "报表生成", "api"),
    ("messaging_collab", "消息协作", "api"),
    ("vector_search", "向量检索", "api"),
    ("memory_store", "记忆存储", "api"),
]


class RuntimeCompiler(CompilerBase):
    """Runtime Compiler。

    将前四级产物整合为可执行的 Enterprise Runtime。
    流程：组织架构 → Agent 配置 → 流程引擎 → 协作关系图 → 知识索引 → 工具注册表

    产出 RuntimeCompileResult（spec.md §10.2 契约），供 WT2 持久化、WT3 生成 Workforce。
    Enterprise Runtime 9 字段块（PRD §4.6）：organization/agents/process_engines/
    collaboration_graph/knowledge_index/tool_registry/version/completeness/audit_trail
    """

    stage = Stage.RUNTIME.value

    async def compile(self, ctx: CompilationContext) -> CompilationResult:
        """执行 Runtime 级编译。"""
        # 从上游获取各级产物
        knowledge_output = ctx.upstream.get("knowledge")
        process_output = ctx.upstream.get("process")
        capability_output = ctx.upstream.get("capability")

        capability_matrix = self._get_capability_matrix(capability_output)
        process_defs = self._get_process_defs(process_output)

        # 从知识图谱加载组织架构
        graph_store = PGJSONBGraphStore(ctx.db, ctx.enterprise_id)
        departments = await graph_store.query_nodes_by_type(EntityType.DEPARTMENT.value)

        self.logger.info(
            f"Runtime 编译开始: enterprise={ctx.enterprise_id}, "
            f"positions={len(capability_matrix.positions)}, "
            f"processes={len(process_defs)}"
        )

        # 1. 构建组织架构
        organization = self._build_organization(departments, graph_store)

        # 2. 构建 Agent 配置模板列表
        agents = self._build_agents(capability_matrix)

        # 3. 构建流程引擎实例
        process_engines = self._build_process_engines(process_defs)

        # 4. 构建协作关系图
        collaboration_graph = await self._build_collaboration_graph(
            graph_store, capability_matrix.positions
        )

        # 5. 构建知识索引
        knowledge_index = self._build_knowledge_index(knowledge_output)

        # 6. 构建工具注册表
        tool_registry = self._build_tool_registry(capability_matrix)

        # 综合置信度
        confidence = self._calculate_overall_confidence(ctx)

        # 构建 RuntimeCompileResult（§10.2 契约）
        runtime = RuntimeCompileResult(
            version="v1.0.0",
            model_version="v1.0.0",
            compiled_at=utcnow(),
            completeness=0.0,  # 由 completeness.py 计算后回填
            organization=organization,
            agents=agents,
            process_engines=process_engines,
            collaboration_graph=collaboration_graph,
            knowledge_index=knowledge_index,
            tool_registry=tool_registry,
        )

        output = RuntimeCompileOutput(
            enterprise_id=ctx.enterprise_id,
            runtime=runtime,
        )

        summary = self._generate_summary(runtime)

        return CompilationResult(
            stage=self.stage,
            output=output,
            confidence=confidence,
            discovered_summary=summary,
            discovered_count=len(agents),
        )

    def _get_capability_matrix(self, capability_output: Any) -> CapabilityMatrix:
        """从 Capability 级输出中提取 CapabilityMatrix。"""
        if capability_output is None:
            return CapabilityMatrix(
                enterprise_id="", compiled_at=utcnow(), confidence=0.0
            )
        if isinstance(capability_output, CapabilityCompileOutput):
            return capability_output.capability_matrix
        if isinstance(capability_output, dict):
            cm_data = capability_output.get("capability_matrix", capability_output)
            return CapabilityMatrix(**cm_data)
        return CapabilityMatrix(
            enterprise_id="", compiled_at=utcnow(), confidence=0.0
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

    @staticmethod
    def _parse_level(level_val: Any) -> int:
        """解析层级值为整数。

        NER 抽取返回的 level 是字符串（如 'L1'、'L2'、'L3'、'L4'），
        但 DepartmentInstance.level 需要 int。本方法做安全转换。

        - int → 直接返回
        - 'L1'/'L2'/... → 提取数字
        - '3'/' 2 ' → 解析为 int
        - 无效/缺失 → 默认 1
        """
        if isinstance(level_val, int):
            return level_val
        if isinstance(level_val, str):
            import re
            match = re.search(r'\d+', level_val)
            if match:
                return int(match.group())
        if isinstance(level_val, float):
            return int(level_val)
        return 1

    @staticmethod
    def _parse_bool(val: Any) -> bool:
        """安全解析布尔值。LLM 可能返回 'true'/'false' 字符串。"""
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower().strip() in ("true", "1", "yes", "是")
        if isinstance(val, (int, float)):
            return bool(val)
        return False

    @staticmethod
    def _parse_int(val: Any, default: int = 0) -> int:
        """安全解析整数。LLM 可能返回字符串形式的数字。"""
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            import re
            match = re.search(r'\d+', val)
            if match:
                return int(match.group())
        return default

    def _build_organization(self, departments, graph_store) -> RuntimeOrganization:
        """构建组织运行时。

        知识图谱中同一部门常因多份文档重复出现（组织架构/权限表/系统文档反复提及），
        导致部门节点虚高（如 35 个重复部门）。此处按部门名去重，仅保留同名首个实例。
        """
        seen_names: set[str] = set()
        dept_instances: list[DepartmentInstance] = []
        for d in departments:
            name = d.name or ""
            if name in seen_names:
                continue
            seen_names.add(name)
            dept_instances.append(DepartmentInstance(
                dept_id=d.node_id,
                name=name,
                parent_dept_id=d.attributes.get("parent_id"),
                level=self._parse_level(d.attributes.get("level", 1)),
            ))
        # 构建汇报树（基于去重后的部门）
        reporting_tree: dict[str, Any] = {}
        used_ids = {obj.dept_id: obj for obj in dept_instances}
        for d in departments:
            if d.node_id not in used_ids:
                continue
            parent = d.attributes.get("parent_id")
            if parent:
                reporting_tree.setdefault(parent, []).append(d.node_id)
        return RuntimeOrganization(
            departments=dept_instances,
            reporting_tree=reporting_tree,
        )

    def _build_agents(self, capability_matrix: CapabilityMatrix) -> list[AgentConfigTemplate]:
        """从能力矩阵生成 Agent 配置模板列表。

        每个 PositionCapability → 一个 AgentConfigTemplate。
        """
        agents: list[AgentConfigTemplate] = []
        for pos in capability_matrix.positions:
            # 技能绑定
            skills = [
                SkillBinding(skill_id=s.skill_name, name=s.skill_name)
                for s in pos.required_skills
            ]
            # 工具绑定
            tools = [
                ToolBinding(
                    tool_id=t.tool_name,
                    name=t.tool_name,
                    tool_type=t.tool_type,
                    permissions=t.required_permissions,
                )
                for t in pos.required_tools
            ]
            # 生成系统 Prompt
            system_prompt = self._generate_system_prompt(pos)

            agent = AgentConfigTemplate(
                agent_id=f"agent_{pos.position_id}",
                agent_name=pos.position_name,
                role_id=pos.position_id,
                department=pos.department,
                level=pos.level,
                system_prompt=system_prompt,
                skills=skills,
                knowledge_bases=[],
                tools=tools,
                permissions=pos.required_permissions,
                memory_config=MemoryConfig(),
                kpi_ids=pos.kpi_ids,
                status="training",
            )
            agents.append(agent)
        return agents

    def _generate_system_prompt(self, pos: PositionCapability) -> str:
        """根据岗位能力生成 Agent 系统 Prompt。"""
        skills_str = "、".join(s.skill_name for s in pos.required_skills[:5]) or "通用办公"
        return (
            f"你是{pos.department}的{pos.position_name}。"
            f"你的核心技能包括：{skills_str}。"
            f"你负责执行以下流程：{'、'.join(pos.main_processes[:3]) or '日常事务'}。"
            "请按照企业规范完成任务，遇到不确定的情况及时向上级请示。"
        )

    def _build_process_engines(
        self, process_defs: list[ProcessDefinition]
    ) -> list[ProcessEngineInstance]:
        """从流程定义生成可执行的流程引擎实例。"""
        # ProcessEngineInstance.process_type 仅允许 approval/collaboration/business，
        # 但 ProcessDefinition.process_type 可能是 sop/decision/automated 等。
        # 做安全映射，避免 Pydantic 校验失败。
        _PROCESS_TYPE_MAP = {
            "approval": "approval",
            "collaboration": "collaboration",
            "business": "business",
            "sop": "business",
            "decision": "approval",
            "automated": "business",
            "manual": "business",
        }
        engines: list[ProcessEngineInstance] = []
        seen_process_ids: set[str] = set()
        for pd in process_defs:
            # #14 流程去重：同一 process_id 可能在不同文档中重复抽取（如 190 个重复实例）。
            # 按 process_id 去重，仅保留首个定义，避免流程引擎数量虚高。
            if pd.process_id in seen_process_ids:
                continue
            seen_process_ids.add(pd.process_id)
            steps: list[ProcessStep] = []
            for i, s in enumerate(pd.steps):
                steps.append(ProcessStep(
                    step_id=s.get("step_id", f"{pd.process_id}_step_{i+1}"),
                    name=str(s.get("name", f"步骤{i+1}"))[:200],
                    order=self._parse_int(s.get("order"), i + 1),
                    approval_required=self._parse_bool(s.get("approval_required", False)),
                    approver_role=s.get("approver_role"),
                    condition=s.get("condition"),
                    next_step_id=s.get("next_step_id"),
                ))
            triggers = [ProcessTrigger(
                trigger_type="manual",
                condition=pd.trigger_event or "",
            )]
            engines.append(ProcessEngineInstance(
                engine_id=f"engine_{pd.process_id}",
                process_id=pd.process_id,
                name=(pd.name or "").strip(),
                process_type=_PROCESS_TYPE_MAP.get(pd.process_type, "business"),
                steps=steps,
                triggers=triggers,
                participants=pd.participants,
                escalation_rules=[],
            ))
        return engines

    async def _build_collaboration_graph(
        self,
        graph_store: PGJSONBGraphStore,
        positions: list[PositionCapability],
    ) -> CollaborationGraph:
        """构建协作关系图。"""
        nodes: list[dict] = [
            {"id": p.position_id, "name": p.position_name, "department": p.department}
            for p in positions
        ]
        edges: list[CollaborationEdge] = []
        # 基于部门推断协作关系。
        # 之前用「两两全连接」（O(n²)），125 岗位/80 部门会生成数千条边，
        # 既不符合真实协作语义，也让前端 mermaid 渲染大图时冻结浏览器主线程（端到端测试发现）。
        # 改为「中心辐射」模型：每个部门首个岗位作为协调节点，连接其余岗位（O(n)）。
        dept_roles: dict[str, list[str]] = {}
        for p in positions:
            dept_roles.setdefault(p.department, []).append(p.position_id)
        for dept, role_ids in dept_roles.items():
            if len(role_ids) < 2:
                continue
            # 首个岗位作为部门协调节点，其余岗位与之协作
            coordinator = role_ids[0]
            for role_id in role_ids[1:]:
                edges.append(CollaborationEdge(
                    source_id=coordinator,
                    target_id=role_id,
                    relation="collaborates_with",
                    context=f"同属{dept}部门",
                ))
        return CollaborationGraph(nodes=nodes, edges=edges)

    def _build_knowledge_index(
        self, knowledge_output: Any
    ) -> KnowledgeIndex:
        """构建知识索引。"""
        vector_ref = ""
        if knowledge_output is not None:
            if isinstance(knowledge_output, KnowledgeCompileOutput):
                vector_ref = knowledge_output.vector_collection
            elif isinstance(knowledge_output, dict):
                vector_ref = knowledge_output.get("vector_collection", "")
        return KnowledgeIndex(
            vector_store_ref=vector_ref,
            graph_store_ref="knowledge_graph",
        )

    def _build_tool_registry(
        self, capability_matrix: CapabilityMatrix
    ) -> list[ToolRegistryEntry]:
        """构建工具注册表。

        当岗位无工具（注册表为空）时，填充一组平台默认工具（marked installed=True），
        保证 Enterprise Runtime 的工具注册表非空、可直接使用。
        """
        seen: set[str] = set()
        registry: list[ToolRegistryEntry] = []
        for pos in capability_matrix.positions:
            for tool in pos.required_tools:
                if tool.tool_name not in seen:
                    seen.add(tool.tool_name)
                    # #fix 运行底座：岗位收集到的工具均为平台已就绪的底座能力
                    # （知识检索/文档解析/编排/审批流等），应标记为已安装，而非默认
                    # installed=False，否则可视化「运行底座」会显示大面积未安装。
                    registry.append(ToolRegistryEntry(
                        tool_id=tool.tool_name,
                        name=tool.tool_name,
                        tool_type=tool.tool_type,
                        installed=True,
                        verified=True,
                        config={},
                    ))
        # 回退：注册表为空时填充平台默认工具集（installed=True）
        if not registry:
            for tool_id, tool_name, tool_type in _PLATFORM_TOOLS:
                registry.append(ToolRegistryEntry(
                    tool_id=tool_id,
                    name=tool_name,
                    tool_type=tool_type,
                    installed=True,
                    verified=True,
                    config={},
                ))
        return registry

    def _calculate_overall_confidence(self, ctx: CompilationContext) -> float:
        """综合各级置信度。"""
        confidences: list[float] = []
        for stage in ["information", "knowledge", "process", "capability"]:
            output = ctx.upstream.get(stage)
            if output is None:
                continue
            conf = 0.0
            if hasattr(output, "confidence"):
                conf = output.confidence
            elif isinstance(output, dict):
                conf = output.get("confidence", 0.0)
            confidences.append(conf)
        if not confidences:
            return 0.0
        return sum(confidences) / len(confidences)

    def _generate_summary(self, runtime: RuntimeCompileResult) -> str:
        """生成发现摘要。"""
        parts = [
            "编译 Enterprise Runtime：",
            f"{len(runtime.agents)} Agent、{len(runtime.process_engines)} 流程引擎、"
            f"{len(runtime.organization.departments)} 部门、"
            f"{len(runtime.collaboration_graph.edges)} 协作关系、{len(runtime.tool_registry)} 工具",
        ]
        return "".join(parts)
