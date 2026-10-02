"""WT2 Runtime 测试共享 fixtures 与辅助函数。

设计要点：
- ``make_compile_result``：构造符合 spec §10.2 契约的 ``RuntimeCompileResult``，
  通过参数控制 agents / process_engines / tool_registry 等内容，便于 diff/回滚测试。
- ``seed_enterprise`` / ``seed_user``：在测试 DB 直接创建企业与用户（service 层测试用）。
- ``save_runtime_helper``：封装 ``save_runtime`` 的常用调用，减少重复样板。

注意：本文件不定义 pytest fixture 以外的全局状态；所有工厂函数均返回新实例，
避免测试间共享可变对象。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise import Enterprise
from app.models.user import User
from app.schemas.runtime import (
    AgentConfigTemplate,
    CollaborationEdge,
    CollaborationGraph,
    DepartmentInstance,
    KnowledgeIndex,
    ProcessEngineInstance,
    ProcessStep,
    RuntimeCompileResult,
    RuntimeOrganization,
    SkillBinding,
    ToolBinding,
    ToolRegistryEntry,
)
from app.services.runtime import save_runtime


def _utcnow() -> datetime:
    """带 tz 的 UTC now（RuntimeCompileResult.compiled_at 需要 datetime）。"""
    return datetime.now(timezone.utc)


def make_compile_result(
    *,
    version: Optional[str] = None,
    model_version: str = "org-model-v1",
    completeness: float = 80.0,
    compiled_at: Optional[datetime] = None,
    agent_count: int = 2,
    process_count: int = 1,
    tool_count: int = 1,
    department_count: int = 1,
    with_sensitive: bool = True,
) -> RuntimeCompileResult:
    """构造一个可配置的 RuntimeCompileResult（spec §10.2 契约本地副本）。

    Args:
        version: 显式版本号（仅写入 compile_result.version，不覆盖 save_runtime 的版本递增逻辑）
        with_sensitive: 是否填充 system_prompt / permissions 等敏感字段（用于脱敏测试）
    """
    departments = [
        DepartmentInstance(
            dept_id=f"dept-{i}",
            name=f"部门{i}",
            parent_dept_id=None if i == 0 else "dept-0",
            level=i,
        )
        for i in range(department_count)
    ]
    organization = RuntimeOrganization(
        departments=departments,
        reporting_tree={"dept-0": [d.dept_id for d in departments[1:]]} if len(departments) > 1 else {},
    )

    agents = [
        AgentConfigTemplate(
            agent_id=f"agent-{i}",
            agent_name=f"数字员工{i}",
            role_id=f"role-{i}",
            department="dept-0",
            level="L1",
            system_prompt=f"你是数字员工{i}，负责处理业务" if with_sensitive else "",
            skills=[SkillBinding(skill_id=f"skill-{i}", name=f"技能{i}")],
            knowledge_bases=[f"kb-{i}"],
            tools=[
                ToolBinding(
                    tool_id=f"tool-{i}",
                    name=f"工具{i}",
                    tool_type="api",
                    permissions=["read:orders"] if with_sensitive else [],
                )
            ],
            permissions=["manage:orders"] if with_sensitive else [],
            kpi_ids=[f"kpi-{i}"],
        )
        for i in range(agent_count)
    ]

    processes = [
        ProcessEngineInstance(
            engine_id=f"engine-{i}",
            process_id=f"process-{i}",
            process_type="approval" if i == 0 else "business",
            steps=[
                ProcessStep(
                    step_id=f"step-{i}-0",
                    name="发起",
                    order=0,
                    approval_required=True,
                    approver_role="role-0",
                ),
                ProcessStep(
                    step_id=f"step-{i}-1",
                    name="完成",
                    order=1,
                    next_step_id=None,
                ),
            ],
            triggers=[],
            participants=[f"agent-{i % max(agent_count, 1)}"],
        )
        for i in range(process_count)
    ]

    tool_registry = [
        ToolRegistryEntry(
            tool_id=f"tool-{i}",
            name=f"工具{i}",
            tool_type="api",
            installed=True,
            verified=True,
            config={"api_key": "secret-value"} if with_sensitive else {},
        )
        for i in range(tool_count)
    ]

    edges = []
    if agent_count >= 2:
        edges.append(
            CollaborationEdge(
                source_id="agent-0",
                target_id="agent-1",
                relation="collaborates_with",
                context="订单审批协作",
            )
        )

    return RuntimeCompileResult(
        version=version or "v1.0.0",
        model_version=model_version,
        compiled_at=compiled_at or _utcnow(),
        completeness=completeness,
        organization=organization,
        agents=agents,
        process_engines=processes,
        collaboration_graph=CollaborationGraph(
            nodes=[{"id": a.agent_id, "name": a.agent_name} for a in agents],
            edges=edges,
        ),
        knowledge_index=KnowledgeIndex(
            vector_store_ref="chroma://test",
            graph_store_ref="neo4j://test",
        ),
        tool_registry=tool_registry,
    )


async def seed_enterprise(db: AsyncSession, name: str = "测试企业") -> Enterprise:
    """在测试 DB 创建一个企业并 commit，返回带 id 的 Enterprise。"""
    enterprise = Enterprise(name=name)
    db.add(enterprise)
    await db.commit()
    await db.refresh(enterprise)
    return enterprise


async def seed_user(
    db: AsyncSession,
    enterprise: Enterprise,
    email: str = "runtime@test.com",
    role: str = "admin",
) -> User:
    """在测试 DB 创建一个用户（绑定到指定企业）并 commit。"""
    user = User(
        email=email,
        password_hash="hash",
        name="Runtime测试",
        role=role,
        enterprise_id=enterprise.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def save_runtime_helper(
    db: AsyncSession,
    enterprise_id: str,
    created_by: Optional[str] = None,
    change_type: str = "minor",
    version: Optional[str] = None,
    compile_result: Optional[RuntimeCompileResult] = None,
    changelog: Optional[str] = None,
):
    """封装 save_runtime 调用，默认使用 make_compile_result 产出。"""
    cr = compile_result or make_compile_result()
    return await save_runtime(
        db,
        enterprise_id=enterprise_id,
        compile_result=cr,
        created_by=created_by,
        change_type=change_type,
        version=version,
        changelog=changelog,
    )
