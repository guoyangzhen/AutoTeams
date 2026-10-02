from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Literal
from datetime import datetime

# P0-DoS: 与 schemas/agent.py 保持一致的用户输入长度上限
from app.schemas.agent import MAX_USER_MESSAGE_LENGTH, MAX_TITLE_LENGTH


class ConversationCreate(BaseModel):
    agent_id: str
    title: Optional[str] = Field(None, max_length=MAX_TITLE_LENGTH)


class ConversationUpdate(BaseModel):
    """P1-8: 更新对话标题。"""
    title: str = Field(..., max_length=MAX_TITLE_LENGTH)


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    agent_id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageCreate(BaseModel):
    # P0-DoS: 限制消息内容长度，防止超大 payload
    content: str = Field(..., max_length=MAX_USER_MESSAGE_LENGTH)


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sources: Optional[List[dict]] = None
    satisfaction: Optional[str] = None
    token_count: int = 0
    model_used: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SatisfactionUpdate(BaseModel):
    # P0-DoS: satisfaction 为短枚举字符串（satisfied/unsatisfied/implicit:*），上限 50 字符
    satisfaction: str = Field(..., max_length=50)


class ImplicitFeedbackRequest(BaseModel):
    """M12: 隐式满意度反馈请求体。

    signal:
      - regenerate: 用户点击重新生成 → 不满意
      - copy: 用户复制答案 → 满意
      - dwell: 用户停留时长信号，dwell_seconds < 3 视为不满意，>= 3 无信号
    """
    signal: Literal["regenerate", "copy", "dwell"]
    # P0-DoS: 停留秒数合理范围 0-86400（1 天上限），防止极端值
    dwell_seconds: Optional[int] = Field(None, ge=0, le=86400)


class ConversationDetail(ConversationResponse):
    messages: List[MessageResponse] = []
