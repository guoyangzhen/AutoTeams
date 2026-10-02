"""外部 Agent REST API 的请求与响应 Schema。"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.agent_api_auth import AGENT_API_SCOPES, normalize_scopes


class CreateAgentApiCredentialRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="便于撤销与审计的凭证名称")
    scopes: list[str] = Field(min_length=1, max_length=len(AGENT_API_SCOPES))
    allowed_agent_ids: list[str] = Field(default_factory=list, max_length=100)
    expires_at: Optional[datetime] = Field(default=None, description="为空表示不自动过期，仍可手动撤销")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("凭证名称不能为空")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        return normalize_scopes(value)

    @field_validator("allowed_agent_ids")
    @classmethod
    def normalize_agent_ids(cls, value: list[str]) -> list[str]:
        result = sorted({str(item).strip() for item in value if str(item).strip()})
        if len(result) != len([str(item).strip() for item in value if str(item).strip()]):
            raise ValueError("allowed_agent_ids 不能包含重复值")
        return result

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value <= datetime.now(timezone.utc):
            raise ValueError("expires_at 必须是未来时间")
        return value


class AgentApiCredentialView(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    allowed_agent_ids: list[str]
    is_active: bool
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreatedAgentApiCredential(AgentApiCredentialView):
    # 只会在创建接口的一次性响应中出现，永不落库或在列表/查询接口重放。
    api_key: str


class AgentApiChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    conversation_id: Optional[str] = Field(default=None, max_length=36)
