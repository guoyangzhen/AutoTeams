from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal
from datetime import datetime


class EnterpriseBase(BaseModel):
    name: str


class EnterpriseCreate(EnterpriseBase):
    pass


class EnterpriseUpdate(BaseModel):
    name: Optional[str] = None


class EnterpriseResponse(EnterpriseBase):
    id: str
    invite_token: Optional[str] = None
    invite_expires_at: Optional[datetime] = None
    # P2-2: 软删除标志与邀请使用次数
    is_active: bool = True
    invite_max_uses: int = 10
    invite_used_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InviteResponse(BaseModel):
    invite_token: str
    invite_url: str
    expires_at: datetime
    # P2-2: 邀请使用次数信息
    max_uses: int = 10
    used_count: int = 0
    invitation_id: Optional[str] = None


# P2-2: 成员管理相关 schema
class MemberResponse(BaseModel):
    """企业成员信息。"""
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UpdateRoleRequest(BaseModel):
    """角色变更请求。"""
    role: Literal["admin", "member"]


class InvitationResponse(BaseModel):
    """邀请记录响应。"""
    id: str
    enterprise_id: str
    invited_by_user_id: str
    token: str
    email: Optional[str] = None
    status: str
    expires_at: Optional[datetime] = None
    created_at: datetime
    used_at: Optional[datetime] = None
    used_by_user_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CreateInvitationRequest(BaseModel):
    """创建邀请请求。"""
    max_uses: int = 10
    email: Optional[str] = None
