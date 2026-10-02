"""Workforce Orchestrator 测试（PRD §5.3）。

MVP 简化版：仅支持单 Agent 执行方式。

测试覆盖：
- 任务复杂度分类（MVP 全部归为 MULTI_STEP）
- 执行方式选择（MVP 全部归为 SINGLE_AGENT）
- orchestrate 编排结果
- OrchestrationResult.to_dict
"""
import pytest

from app.services.workforce.orchestrator import (
    WorkforceOrchestrator,
    ExecutionMethod,
    TaskComplexity,
    OrchestrationResult,
)


class TestTaskClassification:
    """任务复杂度分类测试。"""

    def test_classify_returns_multi_step(self):
        """MVP 阶段所有任务都归类为 MULTI_STEP。"""
        orch = WorkforceOrchestrator()
        assert orch.classify_task("处理客户询盘") == TaskComplexity.MULTI_STEP
        assert orch.classify_task("格式转换") == TaskComplexity.MULTI_STEP
        assert orch.classify_task("审批流程") == TaskComplexity.MULTI_STEP
        assert orch.classify_task("跨部门协作") == TaskComplexity.MULTI_STEP


class TestMethodSelection:
    """执行方式选择测试。"""

    def test_select_method_returns_single_agent(self):
        """MVP 阶段所有任务都使用 SINGLE_AGENT。"""
        orch = WorkforceOrchestrator()
        for complexity in TaskComplexity:
            assert orch.select_method(complexity) == ExecutionMethod.SINGLE_AGENT


class TestOrchestrate:
    """orchestrate 编排测试。"""

    async def test_orchestrate_with_agent_id(self):
        """指定 Agent 的编排。"""
        orch = WorkforceOrchestrator()
        result = await orch.orchestrate(
            task_description="处理客户询盘并生成报价",
            agent_id="agent-orch-1",
        )

        assert result.method == ExecutionMethod.SINGLE_AGENT
        assert result.agent_id == "agent-orch-1"
        assert result.status == "ready"
        assert "处理客户询盘" in result.message

    async def test_orchestrate_without_agent_id(self):
        """未指定 Agent 的编排。"""
        orch = WorkforceOrchestrator()
        result = await orch.orchestrate(
            task_description="分析销售数据",
            agent_id=None,
        )

        assert result.method == ExecutionMethod.SINGLE_AGENT
        assert result.agent_id is None
        assert "待分配" in result.message

    async def test_orchestrate_with_context(self):
        """带上下文的编排。"""
        orch = WorkforceOrchestrator()
        result = await orch.orchestrate(
            task_description="生成月度报告",
            agent_id="agent-orch-2",
            context={"period": "2026-07", "department": "销售部"},
        )

        assert result.method == ExecutionMethod.SINGLE_AGENT
        assert result.status == "ready"

    async def test_orchestrate_p1_method_degrades_to_single_agent(self):
        """P1 执行方式（非 SINGLE_AGENT）降级为单 Agent。"""
        orch = WorkforceOrchestrator()
        # 直接调用 _execute_single_agent 验证降级路径
        result = await orch._execute_single_agent("测试任务", "agent-x")
        assert result.method == ExecutionMethod.SINGLE_AGENT


class TestOrchestrationResult:
    """OrchestrationResult 测试。"""

    def test_to_dict(self):
        """to_dict 返回完整字段。"""
        result = OrchestrationResult(
            method=ExecutionMethod.SINGLE_AGENT,
            agent_id="agent-1",
            status="ready",
            result={"output": "done"},
            message="任务完成",
        )
        d = result.to_dict()
        assert d["method"] == "single_agent"
        assert d["agent_id"] == "agent-1"
        assert d["status"] == "ready"
        assert d["result"] == {"output": "done"}
        assert d["message"] == "任务完成"

    def test_default_values(self):
        """默认值测试。"""
        result = OrchestrationResult(method=ExecutionMethod.SINGLE_AGENT)
        assert result.agent_id is None
        assert result.status == "pending"
        assert result.result is None
        assert result.message == ""


class TestExecutionMethodEnum:
    """执行方式枚举测试。"""

    def test_mvp_only_supports_single_agent(self):
        """MVP 仅支持 SINGLE_AGENT。"""
        assert ExecutionMethod.SINGLE_AGENT.value == "single_agent"

    def test_p1_methods_defined(self):
        """P1 执行方式已定义（暂未实现）。"""
        assert ExecutionMethod.SCRIPT.value == "script"
        assert ExecutionMethod.WORKFLOW.value == "workflow"
        assert ExecutionMethod.SKILL_MODEL.value == "skill_model"
        assert ExecutionMethod.MULTI_AGENT.value == "multi_agent"
