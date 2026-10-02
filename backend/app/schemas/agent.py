from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime


# P0-DoS: 用户输入文本字段长度上限，防止超大 payload 耗尽内存与 LLM token 预算。
# 值选取依据：覆盖真实业务场景（长文档粘贴、多轮对话），同时远低于 DoS 阈值。
#   - 单条 chat 消息 100K 字符 ≈ 25K tokens，足够粘贴整篇文档
#   - 标题类短文本 200 字符
# 这些上限仅在 schema 层兜底，service 层如需进一步限制可独立裁剪。
MAX_USER_MESSAGE_LENGTH = 100_000
MAX_TITLE_LENGTH = 200


class AgentBase(BaseModel):
    name: str
    description: Optional[str] = None


class AgentCreate(AgentBase):
    enterprise_id: str
    system_prompt: Optional[str] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    # P1-FE: 支持保存 Agent 配置（含知识库分块/检索参数）
    config: Optional[dict] = None
    # P0-2: 变更说明（写入 AgentVersion.changelog）
    changelog: Optional[str] = None


class ConfigSnapshot(BaseModel):
    """3.4.5: Agent 版本配置快照的强类型定义。

    兼容两种场景：
    - 配置快照：包含 name/description/system_prompt/config/version 全字段
    - 知识库快照：config_snapshot 为空 dict（仅记录 knowledge_snapshot）

    Pydantic v2 默认 extra='ignore'，向后兼容历史数据中的额外字段。
    """
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    config: Optional[dict] = None
    version: Optional[str] = None


class AgentVersionResponse(BaseModel):
    """P0-2: Agent 版本快照响应。"""
    id: str
    agent_id: str
    version: str
    # 3.4.5: 使用强类型 ConfigSnapshot 替代裸 dict
    # 空 dict {} 会验证为所有字段为 None 的 ConfigSnapshot，向后兼容
    config_snapshot: ConfigSnapshot
    changelog: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentResponse(AgentBase):
    id: str
    enterprise_id: str
    system_prompt: Optional[str] = None
    folder_path: Optional[str] = None
    file_count: int
    knowledge_count: int
    status: str
    # P1-FE: 返回 Agent 配置（知识库参数等）
    config: Optional[dict] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatMessage(BaseModel):
    # P0-DoS: 限制单条消息长度，防止超大 payload 耗尽内存与 LLM token 预算
    content: str = Field(..., max_length=MAX_USER_MESSAGE_LENGTH)
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    message_id: str
    content: str
    sources: Optional[List[dict]] = None
    conversation_id: str


# P1-2: LangGraph Agent 构建相关 schema

class BuildAgentViaGraphRequest(BaseModel):
    """通过 LangGraph 构建 Agent 的请求。"""
    enterprise_id: str
    name: str
    description: Optional[str] = None
    folder_path: str
    # 是否启用 HITL 审批（扫描后暂停等待人工确认）
    require_approval: bool = False


class ResumeBuildRequest(BaseModel):
    """恢复 HITL 暂停的构建。"""
    approved: bool
    comment: Optional[str] = None
