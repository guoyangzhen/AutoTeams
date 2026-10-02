import os
import uuid
import logging
import asyncio

from fastapi import APIRouter, Depends, File as FastAPIFile, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.file import File
from app.models.agent import Agent
from app.schemas.file import FileCreate, FileResponse, FileListResponse
from app.services.path_security import (
    PathSecurityError,
    sanitize_path_for_response,
    validate_upload_path,
)
from app.services.confidential_sandbox import (
    detect_confidential,
    get_sandbox_path,
    log_confidential_access,
    check_confidential_access,
    grant_confidential_access,
    revoke_confidential_access,
)
from app.services.vector_store import VectorStoreService, ChromaDBConnectionError
from app.utils.security import get_current_user
from app.utils.rbac import require_admin
from app.utils.audit import log_audit
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api, rate_limit_upload, rate_limit_admin
from app.utils.metrics import errors_total
from app.utils.upload_validation import (
    FileUploadError,
    check_magic_bytes,
    detect_file_type,
    max_size_for_file_type,
    save_upload_to_disk,
    scan_file_for_viruses,
    validate_content_type,
)
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode

import chromadb.errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["文件"])

# 支持预览的文件类型（文本类）
PREVIEWABLE_FILE_TYPES = {"document", "code", "data"}

# 分页上限
MAX_LIMIT = 100
DEFAULT_LIMIT = 50


def _file_response_dict(file: "File") -> dict:
    """构建 FileResponse 字典，并将 file_path 脱敏为相对路径。

    避免在 API 响应中泄露服务器绝对目录结构（对齐 skills.py / folders.py 的
    sanitize_path_for_response 用法）。落库与下载仍使用原始绝对路径，仅序列化时脱敏。
    """
    data = FileResponse.model_validate(file).model_dump()
    data["file_path"] = sanitize_path_for_response(file.file_path)
    return data

# P0-DoS: 批量上传单次最多文件数，防止一次请求上传数千文件耗尽磁盘/内存
# 50 覆盖知识库文件夹批量入库的真实场景，同时远低于 DoS 阈值
MAX_BATCH_UPLOAD_FILES = 50

async def _verify_file_access(db: AsyncSession, file_id: str, current_user: User) -> File:
    """P0-01: 校验文件存在且归属当前用户的企业（通过 agent 关联）。

    普通企业用户：必须匹配 enterprise_id；
    超级管理员（enterprise_id=None）：放行（仅校验存在性）。
    """
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    query = select(File).join(Agent, File.agent_id == Agent.id).where(
        File.id == file_id
    )
    if current_user.enterprise_id is not None:
        query = query.where(Agent.enterprise_id == current_user.enterprise_id)
    result = await db.execute(query)
    file = result.scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail=ErrorCode.FILE_NOT_FOUND)
    return file


@router.get("")
@rate_limit_api()
async def list_files(
    request: Request,
    agent_id: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分页查询文件列表。"""
    # P0-01: 必须通过 agent 关联校验企业归属
    if not current_user.enterprise_id:
        return success_response({"files": [], "total": 0})

    # 基础 query：JOIN Agent 过滤企业
    query = (
        select(File)
        .join(Agent, File.agent_id == Agent.id)
        .where(Agent.enterprise_id == current_user.enterprise_id)
    )
    if agent_id:
        query = query.where(File.agent_id == agent_id)

    # 计算总数
    count_query = (
        select(func.count())
        .select_from(File)
        .join(Agent, File.agent_id == Agent.id)
        .where(Agent.enterprise_id == current_user.enterprise_id)
    )
    if agent_id:
        count_query = count_query.where(File.agent_id == agent_id)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页查询
    result = await db.execute(
        query.order_by(File.created_at.desc()).limit(limit).offset(offset)
    )
    files = result.scalars().all()

    response = FileListResponse(
        files=[FileResponse(**_file_response_dict(f)) for f in files],
        total=total,
    )
    return success_response(response.model_dump())


@router.post("", status_code=201)
@rate_limit_api()
async def create_file(
    request: Request,
    data: FileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P1-8: 手动添加文件记录到指定 Agent。

    用于不通过文件夹扫描、直接将已有文件入库的场景。
    路径安全由 path_security 模块校验。
    """
    # 校验 Agent 存在且归属当前用户企业
    agent_result = await db.execute(select(Agent).where(Agent.id == data.agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)
    if current_user.enterprise_id is not None and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)

    # 校验文件路径安全
    from app.services.path_security import validate_path, PathSecurityError
    try:
        safe_path = validate_path(data.file_path, must_exist=True)
    except PathSecurityError:
        raise HTTPException(status_code=400, detail=ErrorCode.FILE_PATH_INVALID) from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=ErrorCode.FILE_NOT_ON_DISK) from None

    # 获取文件大小（若未提供）
    file_size = data.file_size
    if file_size == 0:
        try:
            file_size = os.path.getsize(safe_path)
        except OSError:
            file_size = 0

    file = File(
        agent_id=data.agent_id,
        original_name=data.original_name,
        file_path=str(safe_path),
        file_size=file_size,
        file_type=data.file_type,
        status="uploaded",
    )
    db.add(file)
    await db.flush()
    await db.refresh(file)
    await db.commit()
    return success_response(_file_response_dict(file))


@router.post("/upload", status_code=201)
@rate_limit_upload()
async def upload_file(
    request: Request,
    agent_id: str = Form(...),  # form field
    file: UploadFile = FastAPIFile(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """BE-SEC-07: 真正的文件上传端点。

    - 文件通过 multipart/form-data 上传
    - 强制保存到 UPLOAD_ROOT/<user_id>/ 下
    - 校验扩展名白名单、MIME 类型、Magic Bytes、文件大小
    - 创建 File 记录，状态为 uploaded
    """
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    # 校验 Agent 存在且归属当前用户企业
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if current_user.enterprise_id and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    original_name = file.filename or "unnamed"

    # P1-SANDBOX: 自动识别涉密文件
    is_confidential, is_highly_confidential = detect_confidential(original_name)

    try:
        file_type = detect_file_type(original_name)
        validate_content_type(original_name, file.content_type, file_type)

        max_size = max_size_for_file_type(file_type)
        # P2-T16: 使用完全 UUID 文件名存储（保留扩展名），防止路径遍历和文件名注入
        _, ext = os.path.splitext(original_name)
        safe_filename = f"{uuid.uuid4().hex}{ext.lower()}"

        # P1-SANDBOX: 涉密文件物理隔离到沙箱目录
        if is_confidential:
            safe_path = get_sandbox_path(
                str(current_user.enterprise_id),
                safe_filename,
            )
        else:
            safe_path = validate_upload_path(
                current_user.id,
                safe_filename,
            )

        # 流式落盘并获取首 chunk 用于 Magic Bytes 校验
        file_size, first_chunk = await save_upload_to_disk(file, safe_path, max_size)
        check_magic_bytes(first_chunk, file_type, original_name)

        # T21: ClamAV 病毒扫描（CLAMD_HOST 为空时自动跳过）
        await scan_file_for_viruses(str(safe_path))

    except FileUploadError as e:
        raise HTTPException(status_code=e.status_code, detail=e.error_code) from e
    except PathSecurityError:
        raise HTTPException(status_code=400, detail=ErrorCode.FILE_PATH_INVALID) from None
    except OSError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"保存上传文件失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.FILE_UPLOAD_FAILED) from e
    finally:
        await file.close()

    confidential_status = "none"
    if is_confidential:
        confidential_status = "auto_detected"
    if is_highly_confidential:
        confidential_status = "pending_authorization"

    file_record = File(
        agent_id=agent_id,
        original_name=original_name,
        file_path=str(safe_path),
        file_size=file_size,
        file_type=file_type,
        status="uploaded",
        is_confidential=is_confidential,
        is_highly_confidential=is_highly_confidential,
        confidential_status=confidential_status,
    )
    db.add(file_record)
    await db.flush()
    await db.refresh(file_record)

    # P1-SANDBOX: 涉密文件隔离审计日志（必须在 commit 前写入）
    if file_record.is_confidential:
        await log_audit(
            db,
            current_user,
            "isolate",
            "confidential_file",
            str(file_record.id),
            request=request,
            details={
                "original_name": file_record.original_name,
                "is_highly_confidential": file_record.is_highly_confidential,
                "sandbox_path": file_record.file_path,
            },
        )

    await log_audit(db, current_user, "upload", "file", file_record.id, request=request)
    await db.commit()

    return success_response(_file_response_dict(file_record))


async def _persist_uploaded_file(
    request: Request,
    db: AsyncSession,
    current_user: User,
    agent_id: str,
    upload_file: UploadFile,
) -> dict:
    """保存单个上传文件，创建 File 记录，返回成功/失败结果。

    批量上传中单个文件失败不会抛异常，而是返回错误信息供汇总。
    """
    original_name = os.path.basename(upload_file.filename or "unnamed")
    if not original_name:
        await upload_file.close()
        return {"status": "error", "original_name": upload_file.filename, "error": ErrorCode.FILE_TYPE_NOT_ALLOWED}

    is_confidential, is_highly_confidential = detect_confidential(original_name)

    try:
        file_type = detect_file_type(original_name)
        validate_content_type(original_name, upload_file.content_type, file_type)
        max_size = max_size_for_file_type(file_type)
        _, ext = os.path.splitext(original_name)
        safe_filename = f"{uuid.uuid4().hex}{ext.lower()}"

        if is_confidential:
            safe_path = get_sandbox_path(
                str(current_user.enterprise_id),
                safe_filename,
            )
        else:
            safe_path = validate_upload_path(
                current_user.id,
                safe_filename,
            )

        file_size, first_chunk = await save_upload_to_disk(upload_file, safe_path, max_size)
        check_magic_bytes(first_chunk, file_type, original_name)

        # T21: ClamAV 病毒扫描（CLAMD_HOST 为空时自动跳过）
        await scan_file_for_viruses(str(safe_path))
    except FileUploadError as e:
        await upload_file.close()
        return {"status": "error", "original_name": original_name, "error": e.error_code}
    except PathSecurityError:
        await upload_file.close()
        return {"status": "error", "original_name": original_name, "error": ErrorCode.FILE_PATH_INVALID}
    except OSError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"批量保存上传文件失败: {e}", exc_info=True)
        await upload_file.close()
        return {"status": "error", "original_name": original_name, "error": ErrorCode.FILE_UPLOAD_FAILED}
    finally:
        await upload_file.close()

    confidential_status = "none"
    if is_confidential:
        confidential_status = "auto_detected"
    if is_highly_confidential:
        confidential_status = "pending_authorization"

    file_record = File(
        agent_id=agent_id,
        original_name=original_name,
        file_path=str(safe_path),
        file_size=file_size,
        file_type=file_type,
        status="uploaded",
        is_confidential=is_confidential,
        is_highly_confidential=is_highly_confidential,
        confidential_status=confidential_status,
    )
    db.add(file_record)
    await db.flush()
    await db.refresh(file_record)

    if file_record.is_confidential:
        await log_audit(
            db,
            current_user,
            "isolate",
            "confidential_file",
            str(file_record.id),
            request=request,
            details={
                "original_name": file_record.original_name,
                "is_highly_confidential": file_record.is_highly_confidential,
                "sandbox_path": file_record.file_path,
                "batch": True,
            },
        )

    await log_audit(db, current_user, "upload", "file", file_record.id, request=request)

    return {
        "status": "success",
        "file": _file_response_dict(file_record),
    }


@router.post("/batch-upload", status_code=201)
@rate_limit_upload()
async def batch_upload_files(
    request: Request,
    agent_id: str = Form(...),
    files: list[UploadFile] = FastAPIFile(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P1-UPLOAD: 批量文件/文件夹上传入口。

    前端通过 `webkitdirectory` 选择文件夹后，将多个文件作为 `files` 字段上传。
    每个文件独立校验、保存、创建 File 记录；单个文件失败不影响其他文件。
    """
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    # P0-DoS: 限制单次批量上传文件数，防止数千文件耗尽磁盘/内存/连接池
    if len(files) > MAX_BATCH_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.BATCH_UPLOAD_TOO_MANY_FILES,
        )

    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if current_user.enterprise_id and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)

    items = []
    for upload_file in files:
        result = await _persist_uploaded_file(
            request, db, current_user, agent_id, upload_file
        )
        items.append(result)

    successful = [i for i in items if i["status"] == "success"]
    failed = [i for i in items if i["status"] == "error"]

    await db.commit()

    return success_response({
        "total": len(items),
        "successful": len(successful),
        "failed": len(failed),
        "items": items,
    })


async def _verify_confidential_access(
    db: AsyncSession,
    request: Request,
    file: File,
    current_user: User,
    action: str = "view",
) -> None:
    """P1-SANDBOX: 校验涉密文件访问权限，未授权则抛 403，并记录审计日志。"""
    has_access = await check_confidential_access(db, file, current_user)
    if not has_access:
        await log_confidential_access(
            db, file, current_user, request, action=f"{action}_denied"
        )
        await db.commit()
        raise HTTPException(
            status_code=403, detail=ErrorCode.FILE_CONFIDENTIAL_ACCESS_DENIED
        )
    if file.is_confidential:
        await log_confidential_access(db, file, current_user, request, action=action)
        await db.commit()


@router.get("/{file_id}")
@rate_limit_api()
async def get_file(
    request: Request,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # P0-01: 校验企业归属
    file = await _verify_file_access(db, file_id, current_user)
    # P1-SANDBOX: 涉密文件额外权限校验
    await _verify_confidential_access(db, request, file, current_user, action="view")
    return success_response(_file_response_dict(file))


@router.get("/{file_id}/preview")
@rate_limit_api()
async def preview_file(
    request: Request,
    file_id: str,
    max_chars: int = Query(500, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """文件内容预览（前 N 字符）。

    仅支持文本类文件（document/code/data），二进制文件返回 400 错误。
    """
    # P0-01: 校验企业归属
    file = await _verify_file_access(db, file_id, current_user)
    # P1-SANDBOX: 涉密文件额外权限校验
    await _verify_confidential_access(db, request, file, current_user, action="preview")

    if file.file_type not in PREVIEWABLE_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.FILE_PREVIEW_TYPE_NOT_SUPPORTED,
        )

    # P0-04: 校验文件路径在允许范围内
    from app.services.path_security import validate_path, PathSecurityError
    try:
        safe_path = validate_path(file.file_path, must_exist=True)
    except PathSecurityError:
        raise HTTPException(status_code=404, detail=ErrorCode.FILE_NOT_ON_DISK) from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=ErrorCode.FILE_NOT_ON_DISK) from None

    try:
        # 异步包装同步文件读取，避免阻塞事件循环（P-Async）
        def _read_preview() -> tuple[str, str]:
            with open(safe_path, "r", encoding="utf-8") as f:
                preview = f.read(max_chars)
                # 读取一个额外字符判断是否被截断
                extra = f.read(1)
                return preview, extra
        preview, extra = await asyncio.to_thread(_read_preview)
        truncated = bool(extra)
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.FILE_BINARY_CONTENT,
        ) from None
    except OSError as e:
        # P0 信息泄漏修复：不返回原始异常
        logger.error(f"读取文件失败 {file.file_path}: {e}")
        raise HTTPException(status_code=500, detail=ErrorCode.FILE_READ_FAILED) from e

    return success_response({
        "file_id": file.id,
        "original_name": file.original_name,
        "file_type": file.file_type,
        "preview": preview,
        "truncated": truncated,
    })


@router.delete("/{file_id}")
@rate_limit_api()
async def delete_file(
    request: Request,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    # P0-01: 校验企业归属
    file = await _verify_file_access(db, file_id, current_user)
    # P1-SANDBOX: 涉密文件额外权限校验（管理员通常放行，但记录审计日志）
    await _verify_confidential_access(db, request, file, current_user, action="delete")

    # P2 bug 修复：清理 ChromaDB 中该文件对应的向量，避免孤立数据
    try:
        # 新集合按企业前缀隔离；旧集合只在迁移期间清理，避免已删除文件仍被双读检索。
        if not current_user.enterprise_id:
            raise ValueError("missing_enterprise_for_vector_cleanup")
        vector_store = await VectorStoreService.create_prefixed(current_user.enterprise_id, file.agent_id)
        deleted = await vector_store.delete_by_metadata({"file_path": file.file_path})
        legacy_store = await VectorStoreService.create(f"agent_{file.agent_id}")
        deleted += await legacy_store.delete_by_metadata({"file_path": file.file_path})
        if deleted:
            logger.info(f"已清理 {deleted} 个向量（file={file.original_name}）")

    except ChromaDBConnectionError as e:
        logger.warning(f"清理向量失败（ChromaDB 连接异常，不影响 File 记录删除）: {e}")
    except (RuntimeError, OSError, TypeError, chromadb.errors.ChromaError, ValueError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"清理向量失败（不影响 File 记录删除）: {e}", exc_info=True)

    await db.delete(file)
    await db.flush()
    # P1-16: 写入审计日志
    await log_audit(db, current_user, "delete", "file", file_id, request=request)
    # 显式 commit：确保删除在返回 response 前已持久化
    await db.commit()

    return success_response({"message": "文件记录已删除"})


@router.post("/{file_id}/confidential/grant")
@rate_limit_admin()
async def grant_confidential_file_access(
    request: Request,
    file_id: str,
    target_user_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P1-SANDBOX: 授予指定用户访问极度私密文件的权限（企业管理员）。"""
    file = await _verify_file_access(db, file_id, current_user)
    if not file.is_highly_confidential:
        raise HTTPException(
            status_code=400, detail=ErrorCode.INVALID_REQUEST
        )

    # 校验目标用户存在且属于同一企业
    target_result = await db.execute(select(User).where(User.id == target_user_id))
    target_user = target_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail=ErrorCode.MEMBER_NOT_FOUND)
    if target_user.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    access = await grant_confidential_access(
        db, file, target_user_id, current_user
    )
    file.confidential_status = "authorized"
    await db.flush()

    await log_audit(
        db,
        current_user,
        "grant_confidential_access",
        "confidential_file",
        str(file.id),
        request=request,
        details={
            "target_user_id": target_user_id,
            "granted_by": current_user.id,
        },
    )
    await db.commit()

    return success_response({
        "id": access.id,
        "file_id": access.file_id,
        "user_id": access.user_id,
        "granted_by": access.granted_by,
        "granted_at": access.granted_at.isoformat() if access.granted_at else None,
    })


@router.post("/{file_id}/confidential/revoke")
@rate_limit_admin()
async def revoke_confidential_file_access(
    request: Request,
    file_id: str,
    target_user_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P1-SANDBOX: 撤销指定用户对极度私密文件的访问权限（企业管理员）。"""
    file = await _verify_file_access(db, file_id, current_user)

    revoked = await revoke_confidential_access(db, file, target_user_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)

    await log_audit(
        db,
        current_user,
        "revoke_confidential_access",
        "confidential_file",
        str(file.id),
        request=request,
        details={"target_user_id": target_user_id},
    )
    await db.commit()

    return success_response({"message": "授权已撤销"})


@router.get("/{file_id}/confidential/access")
@rate_limit_admin()
async def list_confidential_file_access(
    request: Request,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P1-SANDBOX: 查询极度私密文件的授权访问列表。"""
    # _verify_file_access 用于权限校验，失败会抛出 HTTPException
    await _verify_file_access(db, file_id, current_user)

    from app.models.confidential_file_access import ConfidentialFileAccess
    result = await db.execute(
        select(ConfidentialFileAccess)
        .where(ConfidentialFileAccess.file_id == file_id)
        .order_by(ConfidentialFileAccess.granted_at.desc())
    )
    accesses = result.scalars().all()

    return success_response([
        {
            "id": a.id,
            "file_id": a.file_id,
            "user_id": a.user_id,
            "granted_by": a.granted_by,
            "granted_at": a.granted_at.isoformat() if a.granted_at else None,
        }
        for a in accesses
    ])
