"""Agent CRUD + 版本管理端点 (3.3.3 拆分)。

涵盖职责：
- Agent CRUD：list / get / delete / update
- 版本管理：list versions / rollback version
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.user import User
from app.schemas.agent import (
    AgentResponse,
    AgentUpdate,
    AgentVersionResponse,
)
# P0-2: Agent 版本快照服务
from app.services.agent_version_service import (
    create_version_snapshot,
    list_versions as list_agent_versions,
    rollback_to_version as rollback_agent_to_version,
)
from app.services.vector_store import VectorStoreService, ChromaDBConnectionError
from app.utils.security import get_current_user
from app.utils.rbac import require_admin
from app.utils.audit import log_audit
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.metrics import errors_total
from app.utils.error_codes import ErrorCode

from app.api.agents._helpers import (
    _get_agent_or_404,
    _get_agent_or_404_for_watching,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agent"])


@router.get("")
@rate_limit_api()
async def list_agents(
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="每页数量（1-200）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前企业下的 Agent。

    3.2.4: 增加 limit/offset 分页参数，与 list_files 等端点保持一致。
    默认 limit=50，最大 200；前端可通过 offset 翻页。
    """
    query = select(Agent)
    # P0-01: 无企业用户不返回任何 Agent（之前在 enterprise_id=None 时返回全表）
    if not current_user.enterprise_id:
        return success_response([])
    query = query.where(Agent.enterprise_id == current_user.enterprise_id)

    # 3.2.4: 应用分页，避免极端场景（1000+ Agent）下响应体过大
    result = await db.execute(query.limit(limit).offset(offset))
    agents = result.scalars().all()
    return success_response([AgentResponse.model_validate(a).model_dump() for a in agents])


@router.get("/{agent_id}")
@rate_limit_api()
async def get_agent(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # P0-01: 校验企业归属
    agent = await _get_agent_or_404_for_watching(db, agent_id, current_user)
    return success_response(AgentResponse.model_validate(agent).model_dump())


@router.delete("/{agent_id}")
@rate_limit_api()
async def delete_agent(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)

    if current_user.enterprise_id and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)

    # BE-REL-02: 模型已配置级联删除（files/conversations/messages/skills/versions/optimization_history），
    # 只需删除 Agent 即可由数据库和 ORM 保证原子性清理。
    # 技术审计 R2 C-2: processing_tasks/task_plans 的 agent_id ondelete=SET NULL
    # （迁移 g1b2c3d4e5f6 补全），删除 Agent 时自动解绑而非阻塞。
    try:
        # 新集合为企业前缀命名；同时清理迁移期只读旧集合，防止已删 Agent 数据残留。
        vector_store = await VectorStoreService.create_prefixed(agent.enterprise_id, agent_id)
        await vector_store.delete_collection()
        legacy_store = await VectorStoreService.create(f"agent_{agent_id}")
        await legacy_store.delete_collection()

    except ChromaDBConnectionError as e:
        logger.warning(f"删除向量集合失败（ChromaDB 连接异常）: {e}")
    except (RuntimeError, OSError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"删除向量集合失败: {e}", exc_info=True)

    await db.delete(agent)
    await db.flush()
    # P1-16: 写入审计日志
    await log_audit(db, current_user, "delete", "agent", agent_id, request=request)
    # 显式 commit：删除属于写操作，必须持久化，不能依赖 get_db 的 yield-after commit
    await db.commit()


@router.put("/{agent_id}")
@rate_limit_api()
async def update_agent(
    agent_id: str,
    request: Request,
    data: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P1-8: 更新 Agent 配置（名称、描述、system_prompt）。

    P0-2: 在应用新值之前，先将"当前配置"保存为 AgentVersion 快照，
    并递增 agent.version（patch 位），便于后续回滚与审计。
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)

    if current_user.enterprise_id and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)

    # P0-2: 先保存"旧配置"为版本快照（在应用新值之前）
    # 仅当有实际字段更新时才创建快照，避免空更新产生噪声版本
    has_update = any(
        v is not None for v in (data.name, data.description, data.system_prompt, data.config)
    )
    if has_update:
        try:
            changelog = data.changelog or _build_update_changelog(data)
            await create_version_snapshot(db, agent, current_user.id, changelog=changelog)
        except SQLAlchemyError as e:
            logger.error(f"创建版本快照失败: {e}", exc_info=True)
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            # 快照失败不阻塞主更新流程，但需记录
            # 主流程继续：agent.version 不递增，但字段仍更新

    # 仅更新非 None 字段
    if data.name is not None:
        agent.name = data.name
    if data.description is not None:
        agent.description = data.description
    if data.system_prompt is not None:
        agent.system_prompt = data.system_prompt
    # P1-FE: 保存知识库配置（合并到 agent.config，保留其他配置项）
    if data.config is not None:
        current_config = agent.config or {}
        current_config.update(data.config)
        agent.config = current_config

    await db.flush()
    await db.refresh(agent)
    # P1-16: 写入审计日志
    await log_audit(
        db, current_user, "update", "agent", agent_id, request=request,
        details={"version": agent.version} if has_update else None,
    )
    await db.commit()
    return success_response(AgentResponse.model_validate(agent).model_dump())


def _build_update_changelog(data: AgentUpdate) -> str:
    """根据更新字段自动生成 changelog。"""
    fields = []
    if data.name is not None:
        fields.append("name")
    if data.description is not None:
        fields.append("description")
    if data.system_prompt is not None:
        fields.append("system_prompt")
    if data.config is not None:
        fields.append("config")
    return f"更新字段: {', '.join(fields)}"


@router.get("/{agent_id}/versions")
@rate_limit_api()
async def list_agent_versions_api(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P0-2: 列出 Agent 版本历史（最新在前）。"""
    await _get_agent_or_404_for_watching(db, agent_id, current_user)
    versions = await list_agent_versions(db, agent_id)
    return success_response([
        AgentVersionResponse.model_validate(v).model_dump() for v in versions
    ])


@router.post("/{agent_id}/versions/{version_id}/rollback")
@rate_limit_api()
async def rollback_agent_version_api(
    agent_id: str,
    version_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P0-2: 回滚 Agent 配置到指定历史版本。

    流程：先把当前配置保存为快照（便于追溯），再从目标快照恢复字段。
    仅管理员可执行回滚。
    """
    agent = await _get_agent_or_404(db, agent_id, current_user)
    try:
        await rollback_agent_to_version(db, agent, version_id, current_user.id)
    except ValueError as e:
        if str(e) == "version_not_found":
            raise HTTPException(status_code=404, detail=ErrorCode.AGENT_VERSION_NOT_FOUND) from e
        raise HTTPException(status_code=400, detail=ErrorCode.AGENT_VERSION_ROLLBACK_FAILED) from e
    except SQLAlchemyError as e:
        logger.error(f"回滚 Agent 版本失败: {e}", exc_info=True)
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        raise HTTPException(status_code=500, detail=ErrorCode.AGENT_VERSION_ROLLBACK_FAILED) from e

    await db.refresh(agent)
    await log_audit(
        db, current_user, "rollback", "agent", agent_id, request=request,
        details={"rollback_to_version_id": version_id, "new_version": agent.version},
    )
    await db.commit()
    return success_response(AgentResponse.model_validate(agent).model_dump())
