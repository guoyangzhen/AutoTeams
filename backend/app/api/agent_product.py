"""面向外部 Agent 的受限 REST 产品 API。

机器凭证只开放状态读取与受授权 Agent 的同步对话；不开放编译、构建、审批、
本地文件写入或任何管理员副作用操作。浏览器会话与机器密钥管理入口相互隔离。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents.chat import chat_with_agent
from app.database import get_db
from app.models.agent import Agent
from app.models.agent_api_credential import AgentApiCredential
from app.models.agent_build_task import AgentBuildTask
from app.models.compiler import CompilationJob
from app.models.user import User
from app.schemas.agent import ChatMessage
from app.schemas.agent_api import (
    AgentApiChatRequest,
    AgentApiCredentialView,
    CreateAgentApiCredentialRequest,
    CreatedAgentApiCredential,
)
from app.utils.agent_api_auth import (
    AgentApiPrincipal,
    generate_agent_api_key,
    get_agent_api_principal,
    hash_agent_api_key,
)
from app.utils.audit import log_audit
from app.utils.rate_limit import rate_limit_admin, rate_limit_agent_api
from app.utils.response import success_response
from app.utils.security import get_current_user

router = APIRouter(prefix="/agent-api", tags=["Agent Product API"])


def _require_enterprise_admin(user: User) -> str:
    if not user.enterprise_id or user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅企业管理员可管理 Agent API 凭证")
    return user.enterprise_id


def _credential_view(credential: AgentApiCredential) -> dict[str, Any]:
    return AgentApiCredentialView.model_validate(credential).model_dump(mode="json")


def _agent_summary(agent: Agent) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "status": agent.status,
        "knowledge_count": agent.knowledge_count,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


def _compilation_summary(job: CompilationJob) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "confidence": job.confidence,
        "completeness": job.completeness,
        "attempt": job.attempt,
        "cancel_requested": job.cancel_requested,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _build_summary(task: AgentBuildTask) -> dict[str, Any]:
    result = task.result if isinstance(task.result, dict) else {}
    return {
        "task_id": str(task.id),
        "status": task.status,
        "current_step": task.current_step,
        "attempt": task.attempt,
        "agent_id": result.get("agent_id"),
        "test_result": result.get("test_result"),
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


async def _agent_for_principal(
    db: AsyncSession, principal: AgentApiPrincipal, agent_id: str
) -> Agent:
    principal.assert_agent_allowed(agent_id)
    agent = (
        await db.execute(
            select(Agent).where(Agent.id == agent_id, Agent.enterprise_id == principal.enterprise_id)
        )
    ).scalar_one_or_none()
    if not agent:
        # 不暴露跨企业资源是否存在。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 不存在或无访问权限")
    return agent


async def _assert_allowed_agent_ids(
    db: AsyncSession, enterprise_id: str, agent_ids: list[str]
) -> None:
    if not agent_ids:
        return
    rows = (
        await db.execute(
            select(Agent.id).where(Agent.enterprise_id == enterprise_id, Agent.id.in_(agent_ids))
        )
    ).scalars().all()
    if {str(item) for item in rows} != set(agent_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="allowed_agent_ids 包含不存在或不属于当前企业的 Agent")


# --------------------------- 浏览器会话：凭证管理 ---------------------------

@router.post("/credentials", status_code=status.HTTP_201_CREATED, response_model=None)
@rate_limit_admin()
async def create_agent_api_credential(
    body: CreateAgentApiCredentialRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enterprise_id = _require_enterprise_admin(current_user)
    await _assert_allowed_agent_ids(db, enterprise_id, body.allowed_agent_ids)

    raw_key = generate_agent_api_key()
    credential = AgentApiCredential(
        enterprise_id=enterprise_id,
        owner_user_id=str(current_user.id),
        name=body.name,
        key_prefix=raw_key[: min(len(raw_key), 20)],
        key_hash=hash_agent_api_key(raw_key),
        scopes=body.scopes,
        allowed_agent_ids=body.allowed_agent_ids,
        expires_at=body.expires_at,
        is_active=True,
    )
    db.add(credential)
    await db.flush()
    await log_audit(
        db,
        current_user,
        "create_agent_api_credential",
        "agent_api_credential",
        credential.id,
        request=request,
        details={
            "scopes": body.scopes,
            "allowed_agent_count": len(body.allowed_agent_ids),
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        },
    )
    await db.commit()
    await db.refresh(credential)

    response = CreatedAgentApiCredential(
        **_credential_view(credential),
        api_key=raw_key,
    )
    return success_response(
        response.model_dump(mode="json"),
        message="Agent API 密钥已创建。请立即安全保存，系统不会再次显示完整密钥。",
    )


@router.get("/credentials", response_model=None)
@rate_limit_admin()
async def list_agent_api_credentials(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enterprise_id = _require_enterprise_admin(current_user)
    credentials = (
        await db.execute(
            select(AgentApiCredential)
            .where(AgentApiCredential.enterprise_id == enterprise_id)
            .order_by(AgentApiCredential.created_at.desc())
        )
    ).scalars().all()
    return success_response([_credential_view(item) for item in credentials])


@router.post("/credentials/{credential_id}/revoke", response_model=None)
@rate_limit_admin()
async def revoke_agent_api_credential(
    credential_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enterprise_id = _require_enterprise_admin(current_user)
    credential = (
        await db.execute(
            select(AgentApiCredential)
            .where(
                AgentApiCredential.id == credential_id,
                AgentApiCredential.enterprise_id == enterprise_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not credential:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent API 凭证不存在")

    credential.is_active = False
    credential.revoked_at = datetime.now(timezone.utc)
    await log_audit(
        db,
        current_user,
        "revoke_agent_api_credential",
        "agent_api_credential",
        credential.id,
        request=request,
    )
    await db.commit()
    return success_response(message="Agent API 凭证已撤销")


# ----------------------------- 机器凭证：产品 API ----------------------------

@router.get("/v1/agents", response_model=None)
@rate_limit_agent_api()
async def list_agents_for_external_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: AgentApiPrincipal = Depends(get_agent_api_principal),
):
    principal.require_scopes("agent:read")
    query = select(Agent).where(Agent.enterprise_id == principal.enterprise_id).order_by(Agent.created_at.desc())
    if principal.allowed_agent_ids:
        query = query.where(Agent.id.in_(principal.allowed_agent_ids))
    agents = (await db.execute(query)).scalars().all()
    await log_audit(
        db, principal.owner, "agent_api_list_agents", "agent_api_credential", principal.credential_id,
        request=request, details={"credential_id": principal.credential_id, "count": len(agents)},
    )
    await db.commit()
    return success_response([_agent_summary(agent) for agent in agents])


@router.get("/v1/agents/{agent_id}", response_model=None)
@rate_limit_agent_api()
async def get_agent_for_external_agent(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: AgentApiPrincipal = Depends(get_agent_api_principal),
):
    principal.require_scopes("agent:read")
    agent = await _agent_for_principal(db, principal, agent_id)
    await log_audit(
        db, principal.owner, "agent_api_get_agent", "agent", agent_id,
        request=request, details={"credential_id": principal.credential_id},
    )
    await db.commit()
    return success_response(_agent_summary(agent))


@router.post("/v1/agents/{agent_id}/chat", response_model=None)
@rate_limit_agent_api()
async def chat_for_external_agent(
    agent_id: str,
    body: AgentApiChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: AgentApiPrincipal = Depends(get_agent_api_principal),
):
    principal.require_scopes("agent:chat")
    await _agent_for_principal(db, principal, agent_id)
    # 只记录调用元数据，不把用户提示词写入审计 details，避免重复保存敏感业务内容。
    await log_audit(
        db, principal.owner, "agent_api_chat", "agent", agent_id,
        request=request,
        details={"credential_id": principal.credential_id, "has_conversation_id": bool(body.conversation_id)},
    )
    return await chat_with_agent(
        agent_id=agent_id,
        data=ChatMessage(content=body.content, conversation_id=body.conversation_id),
        request=request,
        db=db,
        current_user=principal.owner,
    )


@router.get("/v1/compiler/jobs/{job_id}", response_model=None)
@rate_limit_agent_api()
async def get_compilation_job_for_external_agent(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: AgentApiPrincipal = Depends(get_agent_api_principal),
):
    principal.require_scopes("compiler:read")
    job = (
        await db.execute(
            select(CompilationJob).where(
                CompilationJob.id == job_id,
                CompilationJob.enterprise_id == principal.enterprise_id,
            )
        )
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="编译任务不存在或无访问权限")
    await log_audit(
        db, principal.owner, "agent_api_get_compilation_job", "compilation_job", job_id,
        request=request, details={"credential_id": principal.credential_id},
    )
    await db.commit()
    return success_response(_compilation_summary(job))


@router.get("/v1/build/jobs/{task_id}", response_model=None)
@rate_limit_agent_api()
async def get_agent_build_task_for_external_agent(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: AgentApiPrincipal = Depends(get_agent_api_principal),
):
    principal.require_scopes("build:read")
    task = (
        await db.execute(
            select(AgentBuildTask).where(
                AgentBuildTask.id == task_id,
                AgentBuildTask.enterprise_id == principal.enterprise_id,
            )
        )
    ).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 构建任务不存在或无访问权限")
    await log_audit(
        db, principal.owner, "agent_api_get_build_task", "agent_build_task", task_id,
        request=request, details={"credential_id": principal.credential_id},
    )
    await db.commit()
    return success_response(_build_summary(task))
