"""模型 API 配置的请求/响应 Schema。

安全约定：
- 更新请求中的 api_key 字段为可选。传入非空则视为「新密钥」重新加密存储；
  留空/省略则保留原密钥（便于只改模型名而不重填密钥）。
- 响应（View）中的 api_key 一律为掩码或空串，绝不返回明文。
"""
from typing import Optional

from pydantic import BaseModel, Field


class LLMApiConfigUpdate(BaseModel):
    """管理员提交的模型 API 配置（PUT 请求体）。"""

    openai_enabled: bool = False
    openai_api_base: Optional[str] = Field(default=None, max_length=500)
    openai_api_key: Optional[str] = Field(default=None, max_length=500)
    openai_model: Optional[str] = Field(default=None, max_length=200)

    anthropic_enabled: bool = False
    anthropic_api_base: Optional[str] = Field(default=None, max_length=500)
    anthropic_api_key: Optional[str] = Field(default=None, max_length=500)
    anthropic_model: Optional[str] = Field(default=None, max_length=200)


class LLMApiConfigView(BaseModel):
    """返回给前端的配置视图（密钥一律掩码）。"""

    enterprise_id: str
    openai_enabled: bool = False
    openai_api_base: Optional[str] = None
    openai_model: Optional[str] = None
    openai_api_key_masked: str = ""  # 掩码或空
    openai_has_key: bool = False

    anthropic_enabled: bool = False
    anthropic_api_base: Optional[str] = None
    anthropic_model: Optional[str] = None
    anthropic_api_key_masked: str = ""
    anthropic_has_key: bool = False

    updated_at: Optional[str] = None
