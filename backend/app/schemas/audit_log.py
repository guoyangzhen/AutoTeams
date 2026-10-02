"""审计日志 Schema。"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    """审计日志响应条目。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None


class AuditLogListResponse(BaseModel):
    """审计日志列表响应。"""

    total: int
    logs: list[AuditLogResponse]
    limit: int
    offset: int
