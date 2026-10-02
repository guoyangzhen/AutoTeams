"""涉密文件沙箱服务。

P1-SANDBOX:
- 自动识别涉密文件名关键词
- 将涉密文件物理隔离到独立沙箱目录
- 管理极度私密文件的显式授权
- 访问涉密文件时记录审计日志
"""
import logging
import os
import re
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.confidential_file_access import ConfidentialFileAccess
from app.models.file import File
from app.models.user import User
from app.services.path_security import validate_path
from app.utils.audit import log_audit

logger = logging.getLogger(__name__)

# 涉密文件名关键词（大小写不敏感）
_CONFIDENTIAL_KEYWORDS = [
    "机密",
    "秘密",
    "保密",
    "涉密",
    "内部资料",
    "confidential",
    "secret",
    "classified",
    "internal_only",
    "restricted",
    "private",
    "sensitive",
]

# 极度私密文件名关键词（需要显式授权）
_HIGHLY_CONFIDENTIAL_KEYWORDS = [
    "绝密",
    "top_secret",
    "topsecret",
    "高度机密",
    "extremely_confidential",
]

_CONFIDENTIAL_PATTERN = re.compile(
    r"(" + "|".join(re.escape(k) for k in _CONFIDENTIAL_KEYWORDS) + r")",
    re.IGNORECASE,
)
_HIGHLY_CONFIDENTIAL_PATTERN = re.compile(
    r"(" + "|".join(re.escape(k) for k in _HIGHLY_CONFIDENTIAL_KEYWORDS) + r")",
    re.IGNORECASE,
)


def detect_confidential(filename: str) -> tuple[bool, bool]:
    """检测文件名是否涉及机密或极度私密。

    Returns:
        (is_confidential, is_highly_confidential)
    """
    if not filename:
        return False, False
    lowered = filename.lower()
    is_highly = bool(_HIGHLY_CONFIDENTIAL_PATTERN.search(lowered))
    if is_highly:
        return True, True
    is_conf = bool(_CONFIDENTIAL_PATTERN.search(lowered))
    return is_conf, False


def get_sandbox_root() -> str:
    """获取沙箱根目录（物理隔离于普通上传目录之外）。"""
    root = os.path.join(settings.UPLOAD_ROOT, "sandbox")
    os.makedirs(root, exist_ok=True)
    return root


def get_sandbox_path(enterprise_id: Optional[str], filename: str) -> str:
    """构造沙箱文件路径。

    路径格式：<UPLOAD_ROOT>/sandbox/<enterprise_id>/<uuid><ext>
    不直接放在 user_id 下，避免用户离职后文件归属混乱。
    """
    root = get_sandbox_root()
    safe_ent = (enterprise_id or "global").replace("/", "_").replace("\\", "_")
    ent_dir = os.path.join(root, safe_ent)
    os.makedirs(ent_dir, exist_ok=True)

    _, ext = os.path.splitext(filename)
    safe_name = f"{uuid.uuid4().hex}{ext.lower()}"
    target = os.path.join(ent_dir, safe_name)
    return validate_path(target, allowed_root=root)


async def check_confidential_access(
    db: AsyncSession,
    file: File,
    user: User,
) -> bool:
    """检查用户是否有权访问涉密文件。

    权限规则：
    - 非涉密文件：放行
    - 涉密文件：文件上传者（通过企业隔离间接判断）、企业管理员放行
    - 极度私密文件：除上述外，还需在 confidential_file_accesses 中有授权记录
    - 超级管理员（enterprise_id is None）：放行
    """
    if not file.is_confidential:
        return True

    # 超级管理员放行
    if not user.enterprise_id:
        return True

    # 企业管理员放行
    if user.role == "admin" and user.enterprise_id:
        # 通过 agent 校验企业归属（调用方应已确保 file 属于该企业）
        return True

    # 极度私密文件需要显式授权
    if file.is_highly_confidential:
        result = await db.execute(
            select(ConfidentialFileAccess).where(
                ConfidentialFileAccess.file_id == file.id,
                ConfidentialFileAccess.user_id == user.id,
            )
        )
        access = result.scalar_one_or_none()
        if not access:
            return False

    # 普通涉密文件：同企业成员可访问（企业隔离在 API 层已校验）
    return True


async def grant_confidential_access(
    db: AsyncSession,
    file: File,
    target_user_id: str,
    granted_by: User,
) -> ConfidentialFileAccess:
    """授予指定用户访问极度私密文件的权限。"""
    # 幂等：先查后插
    result = await db.execute(
        select(ConfidentialFileAccess).where(
            ConfidentialFileAccess.file_id == file.id,
            ConfidentialFileAccess.user_id == target_user_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    access = ConfidentialFileAccess(
        file_id=file.id,
        user_id=target_user_id,
        granted_by=granted_by.id,
    )
    db.add(access)
    await db.flush()
    await db.refresh(access)
    return access


async def revoke_confidential_access(
    db: AsyncSession,
    file: File,
    target_user_id: str,
) -> bool:
    """撤销指定用户对极度私密文件的访问权限。"""
    result = await db.execute(
        select(ConfidentialFileAccess).where(
            ConfidentialFileAccess.file_id == file.id,
            ConfidentialFileAccess.user_id == target_user_id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        return False
    await db.delete(access)
    await db.flush()
    return True


async def log_confidential_access(
    db: AsyncSession,
    file: File,
    user: User,
    request,
    action: str = "view",
) -> None:
    """记录涉密文件访问审计日志。"""
    await log_audit(
        db,
        user,
        action,
        "confidential_file",
        str(file.id),
        request=request,
        details={
            "original_name": file.original_name,
            "is_highly_confidential": file.is_highly_confidential,
            "file_path": file.file_path,
        },
    )
