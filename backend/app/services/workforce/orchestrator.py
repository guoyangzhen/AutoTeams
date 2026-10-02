"""Workforce Orchestrator：MVP 简化版（PRD §5.3）。

根据任务特征智能选择最合适的执行方式：

| 任务特征 | 执行方式 | MVP | P1 |
|---------|---------|-----|-----|
| 规则明确、无需判断 | 脚本自动化 | - | P1 |
| 步骤固定、有分支 | 工作流引擎 | - | P1 |
| 单一能力、需理解 | Skill + 模型 | - | P1 |
| 多步骤、需判断 | 单 Agent | ✅ MVP | - |
| 跨角色、需协作 | 多 Agent 编排 | - | P1 |

MVP 阶段仅实现"多步骤、需判断 → 单 Agent"执行方式。
"""
import logging
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ExecutionMethod(str, Enum):
    """执行方式枚举。"""
    SCRIPT = "script"              # 脚本自动化（P1）
    WORKFLOW = "workflow"          # 工作流引擎（P1）
    SKILL_MODEL = "skill_model"   # Skill + 模型（P1）
    SINGLE_AGENT = "single_agent"  # 单 Agent（MVP）
    MULTI_AGENT = "multi_agent"   # 多 Agent 编排（P1）


class TaskComplexity(str, Enum):
    """任务复杂度分类。"""
    SIMPLE_RULE = "simple_rule"        # 规则明确
    FIXED_BRANCH = "fixed_branch"      # 步骤固定有分支
    SINGLE_ABILITY = "single_ability"  # 单一能力
    MULTI_STEP = "multi_step"          # 多步骤需判断
    CROSS_ROLE = "cross_role"          # 跨角色协作


class OrchestrationResult:
    """编排结果。"""

    def __init__(
        self,
        method: ExecutionMethod,
        agent_id: Optional[str] = None,
        status: str = "pending",
        result: Optional[Any] = None,
        message: str = "",
    ):
        self.method = method
        self.agent_id = agent_id
        self.status = status
        self.result = result
        self.message = message

    def to_dict(self) -> dict:
        return {
            "method": self.method.value,
            "agent_id": self.agent_id,
            "status": self.status,
            "result": self.result,
            "message": self.message,
        }


class WorkforceOrchestrator:
    """Workforce Orchestrator（MVP 简化版）。

    用法：
        orch = WorkforceOrchestrator()
        result = await orch.orchestrate(
            task_description="处理客户询盘并生成报价",
            agent_id="agent-xxx",
        )
    """

    def classify_task(self, task_description: str) -> TaskComplexity:
        """分类任务复杂度。

        MVP 阶段：所有任务都归类为 MULTI_STEP（单 Agent 执行）。
        P1 阶段：实现完整的分类逻辑。
        """
        # MVP: 所有任务都走单 Agent 路径
        # P1: 基于任务特征分类
        # - 包含"格式转换/同步" → SIMPLE_RULE
        # - 包含"审批/入职" → FIXED_BRANCH
        # - 包含"查询/分类" → SINGLE_ABILITY
        # - 包含"跨角色/协作" → CROSS_ROLE
        # - 其他 → MULTI_STEP
        return TaskComplexity.MULTI_STEP

    def select_method(self, complexity: TaskComplexity) -> ExecutionMethod:
        """根据任务复杂度选择执行方式。

        MVP 阶段：所有任务都使用 SINGLE_AGENT。
        """
        # MVP: 全部走单 Agent
        # P1: 根据复杂度选择
        # SIMPLE_RULE → SCRIPT
        # FIXED_BRANCH → WORKFLOW
        # SINGLE_ABILITY → SKILL_MODEL
        # MULTI_STEP → SINGLE_AGENT
        # CROSS_ROLE → MULTI_AGENT
        return ExecutionMethod.SINGLE_AGENT

    async def orchestrate(
        self,
        task_description: str,
        agent_id: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> OrchestrationResult:
        """编排任务执行。

        MVP 阶段：仅支持单 Agent 执行方式。
        P1 阶段：支持全部 5 种执行方式。

        Args:
            task_description: 任务描述
            agent_id: 指定执行的 Agent ID（可选）
            context: 任务上下文（可选）

        Returns:
            OrchestrationResult
        """
        # 1. 分类任务复杂度
        complexity = self.classify_task(task_description)

        # 2. 选择执行方式
        method = self.select_method(complexity)

        # 3. 执行（MVP 仅支持单 Agent）
        if method == ExecutionMethod.SINGLE_AGENT:
            return await self._execute_single_agent(
                task_description, agent_id, context
            )
        else:
            # P1 执行方式暂不支持
            logger.info(
                f"执行方式 {method.value} 暂未实现（P1），降级为单 Agent"
            )
            return await self._execute_single_agent(
                task_description, agent_id, context
            )

    async def _execute_single_agent(
        self,
        task_description: str,
        agent_id: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> OrchestrationResult:
        """单 Agent 执行方式（MVP 唯一实现）。

        MVP 阶段：仅返回编排决策，实际 Agent 调用由 WT4 协作层或前端触发。
        P1 阶段：集成 Agent 执行引擎，自动调用 Agent 处理任务。
        """
        result = OrchestrationResult(
            method=ExecutionMethod.SINGLE_AGENT,
            agent_id=agent_id,
            status="ready",
            message=(
                f"任务已分配给单 Agent 执行: {task_description[:100]}"
                if agent_id
                else f"任务已分类为单 Agent 执行（待分配 Agent）: {task_description[:100]}"
            ),
        )

        logger.info(
            f"Orchestrator 编排完成: method=single_agent, "
            f"agent_id={agent_id}, task={task_description[:50]}"
        )

        return result
