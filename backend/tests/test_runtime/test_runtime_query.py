"""Runtime 查询接口测试（runtime_query.py，spec §10.5 契约）。

重点验证：
1. **无无限递归**：``get_active_runtime`` 等模块级函数曾因同名导入遮蔽导致递归，
   本测试套件确保所有查询函数能正常返回（若递归会 RecursionError 超时）。
2. RuntimeQueryInterface 各方法语义正确（供 WT3/WT4 消费）。
3. ``RuntimeQueryService`` 类封装与模块级函数行为一致。
4. 反序列化损坏数据时返回 None（不抛异常）。
"""
import pytest

from app.models.runtime import EnterpriseRuntime
from app.schemas.runtime import (
    AgentConfigTemplate,
    CollaborationGraph,
    ProcessEngineInstance,
    RuntimeCompileResult,
    RuntimeOrganization,
    ToolRegistryEntry,
)
from app.services.runtime import (
    RuntimeQueryService,
    get_active_runtime,
    get_agent_template,
    get_agent_templates,
    get_collaboration_graph,
    get_organization,
    get_process_engines,
    get_runtime_version,
    get_tool_registry,
    save_runtime,
)
from app.services.runtime.runtime_query import _to_compile_result

from .conftest import make_compile_result, save_runtime_helper, seed_enterprise, seed_user


# ============================================================
# 反序列化辅助函数测试
# ============================================================


class TestToCompileResult:
    def test_none_returns_none(self):
        assert _to_compile_result(None) is None

    def test_empty_runtime_data_returns_none(self):
        runtime = EnterpriseRuntime(
            id="r1",
            enterprise_id="e1",
            version="v1.0.0",
            runtime_data=None,
            is_active=True,
        )
        assert _to_compile_result(runtime) is None

    def test_valid_data_returns_compile_result(self):
        cr = make_compile_result()
        runtime = EnterpriseRuntime(
            id="r1",
            enterprise_id="e1",
            version="v1.0.0",
            runtime_data=cr.model_dump(mode="json"),
            is_active=True,
        )
        result = _to_compile_result(runtime)
        assert isinstance(result, RuntimeCompileResult)
        assert len(result.agents) == 2

    def test_corrupted_data_returns_none(self):
        """损坏的 JSONB 不应抛异常，应返回 None。"""
        runtime = EnterpriseRuntime(
            id="r1",
            enterprise_id="e1",
            version="v1.0.0",
            runtime_data={"agents": "not-a-list"},  # 类型错误
            is_active=True,
        )
        assert _to_compile_result(runtime) is None


# ============================================================
# 模块级查询函数测试（验证无递归）
# ============================================================


class TestQueryFunctions:
    """验证 spec §10.5 各查询方法的正确性与非递归性。"""

    async def test_get_active_runtime_returns_compile_result(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        result = await get_active_runtime(db_session, enterprise.id)
        assert result is not None
        assert isinstance(result, RuntimeCompileResult)
        assert result.version == "v1.0.0"

    async def test_get_active_runtime_none_when_no_data(self, db_session):
        enterprise = await seed_enterprise(db_session)
        result = await get_active_runtime(db_session, enterprise.id)
        assert result is None

    async def test_get_organization(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        org = await get_organization(db_session, enterprise.id)
        assert org is not None
        assert isinstance(org, RuntimeOrganization)
        assert len(org.departments) == 1
        assert org.departments[0].dept_id == "dept-0"

    async def test_get_organization_none_when_no_runtime(self, db_session):
        enterprise = await seed_enterprise(db_session)
        assert await get_organization(db_session, enterprise.id) is None

    async def test_get_agent_templates(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id,
            compile_result=make_compile_result(agent_count=3),
        )

        agents = await get_agent_templates(db_session, enterprise.id)
        assert len(agents) == 3
        assert all(isinstance(a, AgentConfigTemplate) for a in agents)
        assert agents[0].agent_id == "agent-0"

    async def test_get_agent_templates_empty_when_no_runtime(self, db_session):
        enterprise = await seed_enterprise(db_session)
        agents = await get_agent_templates(db_session, enterprise.id)
        assert agents == []

    async def test_get_agent_template_by_role_id(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        tpl = await get_agent_template(db_session, enterprise.id, "role-1")
        assert tpl is not None
        assert tpl.agent_id == "agent-1"
        assert tpl.role_id == "role-1"

    async def test_get_agent_template_not_found(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        assert await get_agent_template(db_session, enterprise.id, "nonexistent-role") is None

    async def test_get_process_engines(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id,
            compile_result=make_compile_result(process_count=2),
        )

        processes = await get_process_engines(db_session, enterprise.id)
        assert len(processes) == 2
        assert all(isinstance(p, ProcessEngineInstance) for p in processes)
        assert processes[0].engine_id == "engine-0"

    async def test_get_collaboration_graph(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        graph = await get_collaboration_graph(db_session, enterprise.id)
        assert graph is not None
        assert isinstance(graph, CollaborationGraph)
        assert len(graph.edges) == 1
        assert graph.edges[0].source_id == "agent-0"
        assert graph.edges[0].target_id == "agent-1"

    async def test_get_tool_registry(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id,
            compile_result=make_compile_result(tool_count=2),
        )

        tools = await get_tool_registry(db_session, enterprise.id)
        assert len(tools) == 2
        assert all(isinstance(t, ToolRegistryEntry) for t in tools)

    async def test_get_runtime_version_by_version(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v2.1.0"
        )

        result = await get_runtime_version(db_session, enterprise.id, "v2.1.0")
        assert result is not None
        assert result.version == "v2.1.0"

    async def test_get_runtime_version_normalizes_v_prefix(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v2.1.0"
        )

        result = await get_runtime_version(db_session, enterprise.id, "2.1.0")
        assert result is not None

    async def test_get_runtime_version_not_found(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        assert await get_runtime_version(db_session, enterprise.id, "v9.9.9") is None

    async def test_query_reflects_active_version_after_rollback(self, db_session):
        """回滚后查询应反映新的激活版本数据。"""
        from app.services.runtime import rollback_to_version

        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result(agent_count=2, completeness=70.0)
        cr_b = make_compile_result(agent_count=5, completeness=90.0)
        await save_runtime(
            db_session, enterprise_id=enterprise.id, compile_result=cr_a,
            version="v1.0.0", created_by=user.id,
        )
        await save_runtime(
            db_session, enterprise_id=enterprise.id, compile_result=cr_b,
            version="v1.1.0", created_by=user.id,
        )

        # 当前激活 v1.1.0 有 5 个 agent
        agents = await get_agent_templates(db_session, enterprise.id)
        assert len(agents) == 5

        # 回滚到 v1.0.0
        await rollback_to_version(db_session, enterprise.id, "v1.0.0")

        # 查询应反映 v1.0.0 的 2 个 agent
        agents = await get_agent_templates(db_session, enterprise.id)
        assert len(agents) == 2


# ============================================================
# RuntimeQueryService 类封装测试
# ============================================================


class TestRuntimeQueryService:
    """验证类封装与模块级函数行为一致（spec §10.5）。"""

    async def test_service_get_active_runtime(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        result = await RuntimeQueryService.get_active_runtime(db_session, enterprise.id)
        assert result is not None
        assert isinstance(result, RuntimeCompileResult)

    async def test_service_get_agent_templates(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        agents = await RuntimeQueryService.get_agent_templates(db_session, enterprise.id)
        assert len(agents) == 2

    async def test_service_get_agent_template(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        tpl = await RuntimeQueryService.get_agent_template(db_session, enterprise.id, "role-0")
        assert tpl is not None
        assert tpl.role_id == "role-0"

    async def test_service_get_organization(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        org = await RuntimeQueryService.get_organization(db_session, enterprise.id)
        assert org is not None
        assert len(org.departments) == 1

    async def test_service_get_process_engines(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        processes = await RuntimeQueryService.get_process_engines(db_session, enterprise.id)
        assert len(processes) == 1

    async def test_service_get_collaboration_graph(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        graph = await RuntimeQueryService.get_collaboration_graph(db_session, enterprise.id)
        assert graph is not None

    async def test_service_get_tool_registry(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        tools = await RuntimeQueryService.get_tool_registry(db_session, enterprise.id)
        assert len(tools) == 1

    async def test_service_get_runtime_version(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v1.5.0"
        )

        result = await RuntimeQueryService.get_runtime_version(
            db_session, enterprise.id, "v1.5.0"
        )
        assert result is not None
        assert result.version == "v1.5.0"
