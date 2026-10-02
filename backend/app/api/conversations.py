from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationDetail,
    MessageCreate,
    MessageResponse,
    SatisfactionUpdate,
    ImplicitFeedbackRequest,
)
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode

router = APIRouter(prefix="/conversations", tags=["对话"])


@router.post("", status_code=201)
@rate_limit_api()
async def create_conversation(
    request: Request,
    data: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # P0-01: 校验 agent 存在且归属当前用户的企业
    agent_result = await db.execute(select(Agent).where(Agent.id == data.agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if current_user.enterprise_id and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)

    conversation = Conversation(
        user_id=current_user.id,
        agent_id=data.agent_id,
        title=data.title or "新对话",
    )
    db.add(conversation)
    await db.flush()
    await db.refresh(conversation)
    # 显式 commit：确保 conversation 在返回 response 前已持久化
    await db.commit()
    return success_response(ConversationResponse.model_validate(conversation).model_dump())


@router.get("")
@rate_limit_api()
async def list_conversations(
    request: Request,
    agent_id: str | None = None,
    limit: int = Query(100, ge=1, le=500, description="每页数量（1-500）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Conversation).where(Conversation.user_id == current_user.id)
    if agent_id:
        query = query.where(Conversation.agent_id == agent_id)
    query = query.order_by(Conversation.updated_at.desc())

    result = await db.execute(query.limit(limit).offset(offset))
    conversations = result.scalars().all()
    return success_response([ConversationResponse.model_validate(c).model_dump() for c in conversations])


@router.get("/{conversation_id}")
@rate_limit_api()
async def get_conversation(
    request: Request,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail=ErrorCode.CONVERSATION_NOT_FOUND)

    # 过滤软删除的消息，保持与 _fetch_conversation_history 一致
    if conversation.messages:
        conversation.messages = [m for m in conversation.messages if not m.is_deleted]

    return success_response(ConversationDetail.model_validate(conversation).model_dump())


@router.put("/{conversation_id}")
@rate_limit_api()
async def update_conversation(
    request: Request,
    conversation_id: str,
    data: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P1-8: 更新对话标题。"""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail=ErrorCode.CONVERSATION_NOT_FOUND)

    conversation.title = data.title
    await db.flush()
    await db.refresh(conversation)
    await db.commit()
    return success_response(ConversationResponse.model_validate(conversation).model_dump())


@router.delete("/{conversation_id}", status_code=204)
@rate_limit_api()
async def delete_conversation(
    request: Request,
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P1-8: 删除对话及其所有消息。"""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail=ErrorCode.CONVERSATION_NOT_FOUND)

    # 批量删除关联消息，避免 N+1 逐条删除
    await db.execute(
        delete(Message).where(Message.conversation_id == conversation_id)
    )

    await db.delete(conversation)
    await db.flush()
    # P1/P2-INFRA: 记录删除对话审计日志
    await log_audit(
        db, current_user, "delete", "conversation", conversation_id,
        request=request,
        details={"agent_id": conversation.agent_id},
    )
    await db.commit()


@router.post("/{conversation_id}/messages", status_code=201)
@rate_limit_api()
async def create_message(
    request: Request,
    conversation_id: str,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail=ErrorCode.CONVERSATION_NOT_FOUND)

    message = Message(
        conversation_id=conversation_id,
        role="user",
        content=data.content,
    )
    db.add(message)
    await db.flush()
    await db.refresh(message)
    # 显式 commit：确保 message 在返回 response 前已持久化
    await db.commit()
    return success_response(MessageResponse.model_validate(message).model_dump())


@router.put("/messages/{message_id}/satisfaction")
@rate_limit_api()
async def update_message_satisfaction(
    request: Request,
    message_id: str,
    data: SatisfactionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Message)
        .join(Conversation)
        .where(
            Message.id == message_id,
            Conversation.user_id == current_user.id,
        )
    )
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail=ErrorCode.MESSAGE_NOT_FOUND)

    message.satisfaction = data.satisfaction
    await db.flush()
    await db.refresh(message)
    # 显式 commit：确保满意度更新在返回 response 前已持久化
    await db.commit()
    return success_response(MessageResponse.model_validate(message).model_dump())


@router.post("/messages/{message_id}/implicit-feedback")
@rate_limit_api()
async def record_implicit_feedback(
    request: Request,
    message_id: str,
    data: ImplicitFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """M12: 记录隐式满意度信号。

    前端在用户行为（复制/重新生成/停留）发生时上报，后端按规则
    映射为隐式满意度并写入 Message.satisfaction。

    覆盖规则：显式满意度（satisfied/unsatisfied）一旦设置不再被隐式信号覆盖；
    None 或已为 implicit:* 时允许最新隐式信号覆盖。
    """
    # 1. 校验 dwell_seconds 在 signal == "dwell" 时必填
    if data.signal == "dwell" and data.dwell_seconds is None:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST)

    # 2. 查询消息（带会话归属校验，与显式满意度端点一致）
    result = await db.execute(
        select(Message)
        .join(Conversation)
        .where(
            Message.id == message_id,
            Conversation.user_id == current_user.id,
        )
    )
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail=ErrorCode.MESSAGE_NOT_FOUND)

    # 4. 显式满意度已设置 → 不覆盖，直接返回
    current = message.satisfaction
    if current is not None and (
        current.startswith("satisfied") or current.startswith("unsatisfied")
    ):
        return success_response(MessageResponse.model_validate(message).model_dump())

    # 5. dwell >= 3s → 无信号，不变更
    if data.signal == "dwell" and data.dwell_seconds is not None and data.dwell_seconds >= 3:
        return success_response(MessageResponse.model_validate(message).model_dump())

    # 6. 映射信号到隐式满意度
    if data.signal == "copy":
        message.satisfaction = "implicit:satisfied:copy"
    elif data.signal == "regenerate":
        message.satisfaction = "implicit:unsatisfied:regenerate"
    else:  # dwell（dwell_seconds < 3 已在上面 >= 3 提前返回）
        message.satisfaction = "implicit:unsatisfied:dwell"

    # 7. flush + refresh + commit（业务事务）
    await db.flush()
    await db.refresh(message)
    await db.commit()

    # 8. 审计日志 + 独立 commit（参考 apply_optimization 模式：
    #    业务数据已提交，此处仅提交审计记录，作为独立事务）
    await log_audit(
        db, current_user, "implicit_feedback", "message", message_id,
        request=request,
        details={"signal": data.signal, "dwell_seconds": data.dwell_seconds},
    )
    await db.commit()

    return success_response(MessageResponse.model_validate(message).model_dump())
