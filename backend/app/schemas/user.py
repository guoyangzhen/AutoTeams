from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import datetime


# BE-SEC-09: bcrypt 最多处理 72 字节密码，超出部分被静默截断
# 在此校验密码长度，避免用户使用超长密码时不知情
_PASSWORD_MAX_BYTES = 72
_PASSWORD_MIN_LEN = 8


class UserBase(BaseModel):
    email: str
    name: str


class UserCreate(UserBase):
    password: str
    # P0-02-A: 移除 enterprise_id 字段
    # 企业归属通过 /auth/register-with-invite 端点由 Invitation 决定，
    # 客户端无法直接指定 enterprise_id（防止越权加入任意企业）
    # 超级管理员（无企业）通过 /auth/register 注册

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        """BE-SEC-09: 校验密码长度，防止 bcrypt 72 字节截断导致的安全问题。"""
        if len(v) < _PASSWORD_MIN_LEN:
            raise ValueError(f"密码长度不能少于 {_PASSWORD_MIN_LEN} 个字符")
        # bcrypt 截断为 72 字节，UTF-8 编码后可能更多字节
        if len(v.encode("utf-8")) > _PASSWORD_MAX_BYTES:
            raise ValueError("密码过长，请使用不超过 72 字节的密码")
        return v


class UserLogin(BaseModel):
    email: str
    password: str


class UpdateProfileRequest(BaseModel):
    """更新当前用户资料（A1：PUT /auth/me）。"""
    name: str

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("姓名不能为空")
        return name


class ChangePasswordRequest(BaseModel):
    """修改当前用户密码（A1：POST /auth/change-password）。"""
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password_length(cls, v: str) -> str:
        """与 UserCreate 密码校验保持一致，防止 bcrypt 72 字节截断。"""
        if len(v) < _PASSWORD_MIN_LEN:
            raise ValueError(f"新密码长度不能少于 {_PASSWORD_MIN_LEN} 个字符")
        if len(v.encode("utf-8")) > _PASSWORD_MAX_BYTES:
            raise ValueError("新密码过长，请使用不超过 72 字节的密码")
        return v


class UserResponse(UserBase):
    id: str
    role: str
    enterprise_id: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class TokenData(BaseModel):
    user_id: str
    email: str
