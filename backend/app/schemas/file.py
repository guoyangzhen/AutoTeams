from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class FileCreate(BaseModel):
    """P1-8: 手动上传文件到指定 Agent。"""
    agent_id: str
    original_name: str
    file_path: str
    file_size: int = 0
    file_type: str = "document"


class FileResponse(BaseModel):
    id: str
    agent_id: str
    original_name: str
    file_path: str
    file_size: int
    file_type: str
    status: str
    is_confidential: bool
    # P1-SANDBOX: 涉密文件字段
    is_highly_confidential: bool
    confidential_status: str
    # D6 新增：文件级状态追踪字段
    chunk_count: int
    vector_count: int
    error_message: Optional[str] = None
    content_hash: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FileListResponse(BaseModel):
    files: list[FileResponse]
    total: int
