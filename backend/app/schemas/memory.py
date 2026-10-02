"""WT3 记忆 schema —— PRD §5.12 三层记忆架构 API 响应 schema。

记忆配置 schema（MemoryConfig / ShortTermMemoryConfig / LongTermMemoryConfig / EntityMemoryConfig）
复用 WT1 在 schemas/compiler.py 中的定义（§10.2 契约）。
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# 短期记忆 schema
# ============================================================================

class ShortTermTurn(BaseModel):
    """短期记忆单轮对话。"""
    model_config = ConfigDict(from_attributes=True)
    role: str  # user / assistant / system
    content: str
    timestamp: Optional[datetime] = None
    metadata: Optional[dict[str, Any]] = None


# ============================================================================
# 长期记忆 schema
# ============================================================================

class LongTermMemoryResponse(BaseModel):
    """长期记忆条目响应。"""
    model_config = ConfigDict(from_attributes=True)
    id: str
    summary: str
    conversation_id: Optional[str] = None
    vector_id: Optional[str] = None
    created_at: datetime


class LongTermMemoryWriteRequest(BaseModel):
    """手动写入长期记忆请求。"""
    model_config = ConfigDict(from_attributes=True)
    summary: str
    conversation_id: Optional[str] = None


class LongTermSearchResult(BaseModel):
    """长期记忆语义检索结果。"""
    model_config = ConfigDict(from_attributes=True)
    summary: str
    score: float = 0.0
    conversation_id: Optional[str] = None
    created_at: Optional[datetime] = None


# ============================================================================
# 实体记忆 schema
# ============================================================================

class EntityMemoryResponse(BaseModel):
    """实体记忆响应。"""
    model_config = ConfigDict(from_attributes=True)
    id: str
    entity_type: str
    entity_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class EntityMemoryUpsertRequest(BaseModel):
    """实体记忆更新请求。"""
    model_config = ConfigDict(from_attributes=True)
    entity_type: str
    entity_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 统一记忆查询响应（GET /api/v1/workforce/{agent_id}/memory/{conversation_id}）
# ============================================================================

class MemoryQueryResponse(BaseModel):
    """统一记忆查询响应（短期 + 实体 + 长期）。"""
    model_config = ConfigDict(from_attributes=True)
    short_term: list[ShortTermTurn] = Field(default_factory=list)
    entity: list[EntityMemoryResponse] = Field(default_factory=list)
    long_term: list[LongTermSearchResult] = Field(default_factory=list)
