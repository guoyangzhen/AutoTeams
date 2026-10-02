"""路径安全工具。

提供统一的路径校验，防止路径遍历、符号链接攻击、越权文件访问。
所有处理用户提供的路径的服务都应通过此模块校验。
"""
import os
from typing import Optional

from app.config import settings


class PathSecurityError(Exception):
    """路径安全校验失败。"""


def _normalize(path: str) -> str:
    """规范化路径并解析符号链接。

    使用 realpath 而非 abspath，确保解析符号链接到真实路径，
    防止通过符号链接绕过根目录校验。
    """
    return os.path.realpath(path)


def validate_path(
    path: str,
    allowed_root: Optional[str] = None,
    must_exist: bool = False,
    allow_symlink: bool = False,
) -> str:
    """校验路径安全性。

    Args:
        path: 待校验的路径
        allowed_root: 允许的根目录。None 时使用 settings.UPLOAD_ROOT
        must_exist: 是否要求路径必须存在
        allow_symlink: 是否允许符号链接（默认拒绝）

    Returns:
        规范化后的安全路径

    Raises:
        PathSecurityError: 校验失败
    """
    if not path or not isinstance(path, str):
        raise PathSecurityError("路径不能为空")

    # 拒绝空字节（防止截断攻击）
    if "\x00" in path:
        raise PathSecurityError("路径包含非法字符")

    root = allowed_root or settings.UPLOAD_ROOT
    root_real = _normalize(root)

    # 确保根目录存在
    os.makedirs(root_real, exist_ok=True)

    real_path = _normalize(path)

    # 校验路径在允许的根目录下
    # 使用 os.path.commonpath 避免 ./../ 前缀绕过
    if not real_path.startswith(root_real + os.sep) and real_path != root_real:
        raise PathSecurityError(
            f"路径不在允许的根目录下: {path}"
        )

    # 符号链接检查
    if not allow_symlink and os.path.islink(path):
        raise PathSecurityError(f"拒绝访问符号链接: {path}")

    # 存在性校验
    if must_exist and not os.path.exists(real_path):
        raise PathSecurityError(f"路径不存在: {path}")

    return real_path


def validate_file_size(path: str, max_bytes: int) -> None:
    """校验文件大小不超过上限。

    Args:
        path: 文件路径（应已通过 validate_path 校验）
        max_bytes: 最大字节数

    Raises:
        PathSecurityError: 文件过大或无法读取大小
    """
    if max_bytes <= 0:
        return
    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise PathSecurityError(f"无法读取文件大小: {e}") from e
    if size > max_bytes:
        raise PathSecurityError(
            f"文件大小 {size} 字节超过上限 {max_bytes} 字节"
        )


def validate_upload_path(user_id: str, filename: str, allowed_root: Optional[str] = None) -> str:
    """构造并校验上传路径。

    拒绝包含路径分隔符、..、空字节的文件名，将文件强制放入
    <UPLOAD_ROOT>/<user_id>/ 下。

    Args:
        user_id: 用户 ID（用于隔离不同用户的上传目录）
        filename: 原始文件名
        allowed_root: 允许的根目录

    Returns:
        规范化后的安全上传路径
    """
    if not filename or not isinstance(filename, str):
        raise PathSecurityError("文件名不能为空")
    if "\x00" in filename or "/" in filename or "\\" in filename or ".." in filename:
        raise PathSecurityError(f"非法文件名: {filename}")

    # 仅取 basename 防御
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name in (".", ".."):
        raise PathSecurityError(f"非法文件名: {filename}")

    root = allowed_root or settings.UPLOAD_ROOT
    target_dir = os.path.join(root, user_id)
    os.makedirs(target_dir, exist_ok=True)

    target_path = os.path.join(target_dir, safe_name)
    return validate_path(target_path, allowed_root=root)


def sanitize_path_for_response(path: str, allowed_root: Optional[str] = None) -> str:
    """将绝对路径转换为相对 allowed_root 的路径，避免在响应中暴露服务器目录结构。

    若路径不在根目录下，返回 basename 作为最低限度脱敏。
    """
    root = allowed_root or settings.UPLOAD_ROOT
    root_real = _normalize(root)
    real_path = _normalize(path)
    if real_path.startswith(root_real + os.sep) or real_path == root_real:
        rel = os.path.relpath(real_path, root_real)
        return rel.replace(os.sep, "/")
    return os.path.basename(path)
