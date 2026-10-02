"""Runtime 查询接口（WT2 → WT3/WT4，spec §10.5）。

本模块实现 ``spec.md §10.5`` 定义的 ``RuntimeQueryInterface``，供 WT3（Workforce 生成器）
与 WT4（进化层组织分析）通过**直接导入**（非 HTTP）消费。

签名约定：每个查询方法首参为 ``db: AsyncSession``，由调用方传入其请求/任务 session。
这与 §10.5 草图（省略 db）一致——db 是实现细节，调用方需在事务/session 上下文中调用。

返回值为结构化 Pydantic 对象（``RuntimeCompileResult`` 及子结构），从
``EnterpriseRuntime.runtime_data`` JSONB 反序列化而来。激活 Runtime 读取复用
``runtime_store`` 的缓存（best-effort）。
"""
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runtime import EnterpriseRuntime
from app.schemas.runtime import (
    AgentConfigTemplate,
    CollaborationGraph,
    ProcessEngineInstance,
    RuntimeCompileResult,
    RuntimeOrganization,
    ToolRegistryEntry,
)
from app.services.runtime.runtime_store import (
    get_active_runtime as _store_get_active_runtime,
    get_runtime_by_version as _store_get_runtime_by_version,
)

logger = logging.getLogger(__name__)


def _to_compile_result(runtime: Optional[EnterpriseRuntime]) -> Optional[RuntimeCompileResult]:
    """将 ORM 行的 runtime_data JSONB 反序列化为 RuntimeCompileResult。"""
    if runtime is None or not runtime.runtime_data:
        return None
    try:
        return RuntimeCompileResult.model_validate(runtime.runtime_data)
    except Exception as e:  # noqa: BLE001 — 损坏数据不应让消费方崩溃
        logger.error(
            "反序列化 RuntimeCompileResult 失败（runtime_id=%s）: %s",
            runtime.id if runtime else None,
            e,
            exc_info=True,
        )
        return None


async def get_active_runtime(
    db: AsyncSession, enterprise_id: str
) -> Optional[RuntimeCompileResult]:
    """获取当前激活的 Runtime（结构化）。"""
    runtime = await _store_get_active_runtime(db, enterprise_id)
    return _to_compile_result(runtime)


async def get_organization(
    db: AsyncSession, enterprise_id: str
) -> Optional[RuntimeOrganization]:
    """读取组织运行时（§10.5）。"""
    result = await get_active_runtime(db, enterprise_id)
    return result.organization if result else None


async def get_agent_templates(
    db: AsyncSession, enterprise_id: str
) -> list[AgentConfigTemplate]:
    """读取 Agent 配置模板列表（供 WT3 Workforce 生成器使用，§10.5）。"""
    result = await get_active_runtime(db, enterprise_id)
    return list(result.agents) if result else []


async def get_agent_template(
    db: AsyncSession, enterprise_id: str, role_id: str
) -> Optional[AgentConfigTemplate]:
    """按岗位 ID 读取单个 Agent 配置模板（§10.5）。"""
    templates = await get_agent_templates(db, enterprise_id)
    for tpl in templates:
        if tpl.role_id == role_id:
            return tpl
    return None


async def get_process_engines(
    db: AsyncSession, enterprise_id: str
) -> list[ProcessEngineInstance]:
    """读取流程引擎实例（§10.5）。"""
    result = await get_active_runtime(db, enterprise_id)
    return list(result.process_engines) if result else []


async def get_collaboration_graph(
    db: AsyncSession, enterprise_id: str
) -> Optional[CollaborationGraph]:
    """读取协作关系图（§10.5）。"""
    result = await get_active_runtime(db, enterprise_id)
    return result.collaboration_graph if result else None


async def get_tool_registry(
    db: AsyncSession, enterprise_id: str
) -> list[ToolRegistryEntry]:
    """读取工具注册表（§10.5）。"""
    result = await get_active_runtime(db, enterprise_id)
    return list(result.tool_registry) if result else []


async def get_runtime_version(
    db: AsyncSession, enterprise_id: str, version: str
) -> Optional[RuntimeCompileResult]:
    """按版本号读取 Runtime（供 WT4 进化层对比版本，§10.5）。"""
    runtime = await _store_get_runtime_by_version(db, enterprise_id, version)
    return _to_compile_result(runtime)


class RuntimeQueryService:
    """``RuntimeQueryInterface`` 的类封装（spec §10.5）。

    供偏好实例调用的消费方使用；方法语义与模块级函数一致。
    用法：
        svc = RuntimeQueryService()
        agents = await svc.get_agent_templates(db, enterprise_id)
    """

    @staticmethod
    async def get_active_runtime(
        db: AsyncSession, enterprise_id: str
    ) -> Optional[RuntimeCompileResult]:
        return await get_active_runtime(db, enterprise_id)

    @staticmethod
    async def get_organization(
        db: AsyncSession, enterprise_id: str
    ) -> Optional[RuntimeOrganization]:
        return await get_organization(db, enterprise_id)

    @staticmethod
    async def get_agent_templates(
        db: AsyncSession, enterprise_id: str
    ) -> list[AgentConfigTemplate]:
        return await get_agent_templates(db, enterprise_id)

    @staticmethod
    async def get_agent_template(
        db: AsyncSession, enterprise_id: str, role_id: str
    ) -> Optional[AgentConfigTemplate]:
        return await get_agent_template(db, enterprise_id, role_id)

    @staticmethod
    async def get_process_engines(
        db: AsyncSession, enterprise_id: str
    ) -> list[ProcessEngineInstance]:
        return await get_process_engines(db, enterprise_id)

    @staticmethod
    async def get_collaboration_graph(
        db: AsyncSession, enterprise_id: str
    ) -> Optional[CollaborationGraph]:
        return await get_collaboration_graph(db, enterprise_id)

    @staticmethod
    async def get_tool_registry(
        db: AsyncSession, enterprise_id: str
    ) -> list[ToolRegistryEntry]:
        return await get_tool_registry(db, enterprise_id)

    @staticmethod
    async def get_runtime_version(
        db: AsyncSession, enterprise_id: str, version: str
    ) -> Optional[RuntimeCompileResult]:
        return await get_runtime_version(db, enterprise_id, version)
