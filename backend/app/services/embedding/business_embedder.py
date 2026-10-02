"""嵌入业务服务（PRD §4.7）。

将企业业务上下文（流程/数据/规则）嵌入到 AI 员工的运行时配置中，
使 AI 员工能够理解并执行企业特定业务。

三类嵌入：
1. 业务流程嵌入：将 SOP/审批流注入 Agent 的流程引擎
2. 业务数据嵌入：将企业数据 schema/约束注入 Agent 的知识库
3. 业务规则嵌入：将业务规则/约束注入 Agent 的系统 Prompt
"""
import logging

from app.schemas.compiler import (
    AgentConfigTemplate,
    ProcessEngineInstance,
    RuntimeCompileResult,
)

logger = logging.getLogger(__name__)


class BusinessEmbedder:
    """业务嵌入器。

    在 Runtime Compiler 产出后，将业务上下文嵌入到 Agent 配置中，
    使 AI 员工具备企业特定的业务理解能力。
    """

    def __init__(self, enterprise_id: str):
        self._enterprise_id = enterprise_id

    def embed(
        self,
        runtime: RuntimeCompileResult,
        business_rules: list[dict] | None = None,
        data_schemas: list[dict] | None = None,
    ) -> RuntimeCompileResult:
        """执行业务嵌入。

        Args:
            runtime: Runtime Compiler 产出的 Enterprise Runtime
            business_rules: 业务规则列表（可选，从知识图谱提取）
            data_schemas: 数据 schema 列表（可选）

        Returns:
            嵌入业务上下文后的 Runtime
        """
        business_rules = business_rules or []
        data_schemas = data_schemas or []

        # 1. 业务流程嵌入：将流程引擎关联到对应 Agent
        agents = self._embed_processes(runtime.agents, runtime.process_engines)

        # 2. 业务数据嵌入：将数据 schema 注入 Agent 知识库引用
        agents = self._embed_data(agents, data_schemas)

        # 3. 业务规则嵌入：将规则注入 Agent 系统 Prompt
        agents = self._embed_rules(agents, business_rules)

        # 返回更新后的 runtime
        return runtime.model_copy(update={"agents": agents})

    def _embed_processes(
        self,
        agents: list[AgentConfigTemplate],
        process_engines: list[ProcessEngineInstance],
    ) -> list[AgentConfigTemplate]:
        """业务流程嵌入：将流程引擎关联到 Agent。

        每个 Agent 关联其参与的流程引擎 ID。
        """
        # 构建 agent_id → 关联流程引擎映射
        agent_processes: dict[str, list[str]] = {}
        for engine in process_engines:
            for participant in engine.participants:
                agent_id = f"agent_{participant}"
                agent_processes.setdefault(agent_id, []).append(engine.engine_id)

        updated: list[AgentConfigTemplate] = []
        for agent in agents:
            engine_ids = agent_processes.get(agent.agent_id, [])
            if engine_ids:
                # 在系统 Prompt 中追加流程信息
                process_info = f"\n你负责执行以下流程：{', '.join(engine_ids)}"
                updated_prompt = agent.system_prompt + process_info
                updated.append(agent.model_copy(update={"system_prompt": updated_prompt}))
            else:
                updated.append(agent)
        return updated

    def _embed_data(
        self,
        agents: list[AgentConfigTemplate],
        data_schemas: list[dict],
    ) -> list[AgentConfigTemplate]:
        """业务数据嵌入：将数据 schema 注入 Agent 知识库引用。"""
        if not data_schemas:
            return agents
        # 将数据 schema 名作为知识库引用
        schema_refs = [s.get("name", "") for s in data_schemas if s.get("name")]
        updated: list[AgentConfigTemplate] = []
        for agent in agents:
            merged = list(set(agent.knowledge_bases + schema_refs))
            updated.append(agent.model_copy(update={"knowledge_bases": merged}))
        return updated

    def _embed_rules(
        self,
        agents: list[AgentConfigTemplate],
        business_rules: list[dict],
    ) -> list[AgentConfigTemplate]:
        """业务规则嵌入：将规则注入 Agent 系统 Prompt。"""
        if not business_rules:
            return agents
        # 格式化规则
        rule_lines = []
        for rule in business_rules:
            name = rule.get("name", "")
            desc = rule.get("description", "")
            if name:
                rule_lines.append(f"- {name}：{desc}")
        if not rule_lines:
            return agents
        rules_text = "\n\n业务规则（请严格遵守）：\n" + "\n".join(rule_lines)

        updated: list[AgentConfigTemplate] = []
        for agent in agents:
            updated.append(agent.model_copy(update={
                "system_prompt": agent.system_prompt + rules_text
            }))
        return updated
