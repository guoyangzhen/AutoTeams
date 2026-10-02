from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services.folder_scanner import scan_folder
from app.services.path_security import (
    PathSecurityError,
    validate_path,
    sanitize_path_for_response,
)
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_scan, rate_limit_upload
from app.utils.metrics import errors_total
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/folders", tags=["文件夹"])


class FolderScanRequest(BaseModel):
    path: str
    recursive: bool = True


class FileItem(BaseModel):
    name: str
    path: str
    size: int
    file_type: str
    extension: str


class FolderScanResponse(BaseModel):
    files: list[FileItem]
    total_count: int
    total_size: int
    type_summary: dict[str, int]


class ChildItem(BaseModel):
    """目录子项（目录或文件），供 Setup 路径快捷选择。"""
    name: str
    path: str
    type: str  # "directory" | "file"
    size: Optional[int] = None


class FolderUploadResponse(BaseModel):
    """文件夹上传响应（暂存到 UPLOAD_ROOT/<user_id>/<folder_id>/ 供模板应用扫描）。"""
    folder_path: str
    folder_name: str
    file_count: int
    total_size: int


# list-children 跳过的目录名（与 scan_folder 保持一致）
_SKIP_DIRS = {".git", ".svn", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}


@router.get("/list-children")
@rate_limit_scan()
async def list_folder_children(
    request: Request,
    path: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出指定路径下的直接子项，供 Setup 路径快捷选择与目录浏览。

    - 不传 path 时默认列出 UPLOAD_ROOT 子项
    - 路径校验由 validate_path 完成（受 UPLOAD_ROOT 限制，防止路径遍历）
    - 返回 current_path / parent_path / children，支持前端向上导航
    - 端点无尾斜杠，避免 307 丢 Authorization 头
    """
    target = path or settings.UPLOAD_ROOT
    try:
        safe_path = validate_path(target, must_exist=True)
    except FileNotFoundError as e:
        logger.warning(f"list-children 文件夹不存在: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.FOLDER_NOT_FOUND) from e
    except PermissionError as e:
        logger.warning(f"list-children 文件夹无访问权限: {e}")
        raise HTTPException(status_code=403, detail=ErrorCode.FOLDER_ACCESS_DENIED) from e
    except (ValueError, PathSecurityError) as e:
        logger.warning(f"list-children 路径校验失败: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.FOLDER_PATH_INVALID) from e
    except (OSError, RuntimeError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"list-children 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e

    # 计算父目录（用于前端返回上级）
    parent_path = os.path.dirname(safe_path.rstrip(os.sep)) or None

    children: list[ChildItem] = []
    try:
        for entry in os.scandir(safe_path):
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            if entry.is_dir(follow_symlinks=False):
                children.append(ChildItem(name=entry.name, path=entry.path, type="directory"))
            elif entry.is_file(follow_symlinks=False):
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = None
                children.append(ChildItem(
                    name=entry.name, path=entry.path, type="file", size=size,
                ))
    except OSError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"list-children 扫描失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e

    # 排序：目录优先，再按名称
    children.sort(key=lambda c: (c.type != "directory", c.name.lower()))

    return success_response({
        "current_path": safe_path,
        "parent_path": parent_path,
        "children": [item.model_dump() for item in children],
    })


@router.post("/scan")
@rate_limit_scan()
async def scan_local_folder(
    request: Request,
    request_body: FolderScanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """P0-03: 扫描本地文件夹。

    - 必须认证（无 get_current_user 的版本允许匿名调用）
    - 路径校验在 scan_folder 内部完成（P0-04）
    - 返回相对路径（P0-04: 响应前由 API 层脱敏）
    """
    try:
        files = scan_folder(request_body.path, recursive=request_body.recursive)
    except FileNotFoundError as e:
        # P1-10: 避免泄漏文件系统路径细节
        logger.warning(f"扫描文件夹不存在: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.FOLDER_NOT_FOUND) from e
    except PermissionError:
        raise HTTPException(status_code=403, detail=ErrorCode.FOLDER_ACCESS_DENIED) from None
    except ValueError as e:
        # P0-04: PathSecurityError 在 scan_folder 中被转为 ValueError
        # P1-10: 不暴露路径校验细节，避免泄漏文件系统结构
        logger.warning(f"路径校验失败: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.FOLDER_PATH_INVALID) from e
    except PathSecurityError as e:
        logger.warning(f"路径安全校验失败: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.FOLDER_PATH_INVALID) from e
    except (OSError, RuntimeError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"扫描文件夹失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e

    type_summary: dict[str, int] = {}
    total_size = 0
    file_items = []

    for f in files:
        type_summary[f.file_type] = type_summary.get(f.file_type, 0) + 1
        total_size += f.size
        # P0-04: 响应中不暴露服务器绝对路径
        safe_path = sanitize_path_for_response(f.path, allowed_root=request_body.path)
        file_items.append(FileItem(
            name=f.name,
            path=safe_path,
            size=f.size,
            file_type=f.file_type,
            extension=f.extension,
        ))

    return success_response({
        "files": [item.model_dump() for item in file_items],
        "total_count": len(file_items),
        "total_size": total_size,
        "type_summary": type_summary,
    })


# 单文件大小上限（字节）。文件夹上传面向知识文档，50MB 足够且避免资源耗尽。
_FOLDER_UPLOAD_MAX_FILE_BYTES = 50 * 1024 * 1024
# 单次上传文件数量上限（与 files.batch-upload 的 MAX_BATCH_UPLOAD_FILES=50 保持一致）。
_FOLDER_UPLOAD_MAX_FILES = 50
# 单次上传总大小上限（字节），防止一次请求耗尽磁盘/内存。
_FOLDER_UPLOAD_MAX_TOTAL_BYTES = 200 * 1024 * 1024


@router.post("/upload", status_code=201)
@rate_limit_upload()
async def upload_folder(
    request: Request,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传文件夹（webkitdirectory 多文件）到 UPLOAD_ROOT。

    用于「模板库 → 知识源文件夹」：Web 端通过
    `<input type="file" webkitdirectory>` 选择本地文件夹后，将每个文件的相对路径
    （如 `公司文档/01-组织.md`）作为 multipart filename 上传，本端点按相对路径
    还原目录结构并暂存到 `UPLOAD_ROOT/<user_id>/<folder_id>/`，返回服务端可扫描的
    文件夹绝对路径，供套用模板时 `scan_folder` 使用 —— 从而修复此前用户直接输入
    本地绝对路径（不在 UPLOAD_ROOT 内）导致的 `FOLDER_PATH_INVALID`。

    安全：
    - 路径仅允许相对路径，拒绝绝对路径、`..`、空字节、空段
    - 目录深度限制为 4 层，避免异常嵌套
    - 最终目录经 validate_path 校验，确保落在 UPLOAD_ROOT 内
    - 单文件大小上限 50MB
    """
    if not files:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST)
    if len(files) > _FOLDER_UPLOAD_MAX_FILES:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST)

    user_dir = os.path.join(settings.UPLOAD_ROOT, str(current_user.id))
    os.makedirs(user_dir, exist_ok=True)
    staging = f"folder_{uuid.uuid4().hex[:12]}"
    base = os.path.join(user_dir, staging)
    os.makedirs(base, exist_ok=True)

    base_real = os.path.realpath(base)
    written = 0
    total_size = 0

    try:
        for uf in files:
            rel = (uf.filename or "").replace("\\", "/").strip()
            while rel.startswith("./"):
                rel = rel[2:]
            rel = rel.strip("/")
            if not rel or "\x00" in rel or ".." in rel.split("/"):
                await uf.close()
                raise HTTPException(status_code=400, detail=ErrorCode.FOLDER_PATH_INVALID)

            # 逐段清洗，限制目录深度（最多 3 层子目录 + 1 文件名）
            segs = [s for s in rel.split("/") if s and s not in (".", "..")]
            if not segs:
                await uf.close()
                continue
            segs = segs[:4]

            if len(segs) == 1:
                dest = os.path.join(base, segs[0])
            else:
                sub = os.path.join(base, *segs[:-1])
                os.makedirs(sub, exist_ok=True)
                dest = os.path.join(sub, segs[-1])

            # 二次校验：目标必须位于 base 内（防御之上再兜底）
            if os.path.commonpath([base_real, os.path.realpath(dest)]) != base_real:
                await uf.close()
                raise HTTPException(status_code=400, detail=ErrorCode.FOLDER_PATH_INVALID)

            size = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = await uf.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _FOLDER_UPLOAD_MAX_FILE_BYTES:
                        await uf.close()
                        raise HTTPException(status_code=413, detail=ErrorCode.FILE_SIZE_EXCEEDED)
                    out.write(chunk)
            await uf.close()
            written += 1
            total_size += size
            # 总大小上限：提前中止，避免累计写入过大、残留孤儿文件
            if total_size > _FOLDER_UPLOAD_MAX_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail=ErrorCode.FILE_SIZE_EXCEEDED)
    except HTTPException:
        await uf.close()
        raise

    if written == 0:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST)

    # 最终校验暂存目录在 UPLOAD_ROOT 内
    safe_base = validate_path(base, must_exist=True)

    return success_response(FolderUploadResponse(
        folder_path=safe_base,
        folder_name=os.path.basename(safe_base),
        file_count=written,
        total_size=total_size,
    ).model_dump())
