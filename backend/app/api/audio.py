"""音频转录 API 路由（产品完善方案 P0-1：语音讨论入口）。

端点（前缀 ``/api/v1/audio``，全部无尾斜杠）：
- POST /audio/transcribe                           上传音频并转录为文本

设计要点：
- 复用 files.py 同等上传安全策略（MIME 白名单、Magic Bytes、大小限制、
  路径安全校验、流式落盘），保证与既有上传入口一致。
- 调用 `app.services.audio_transcriber.audio_transcriber`（faster-whisper 单例）。
- 优雅降级：faster-whisper / ffmpeg 不可用时返回空文本提示，而非 5xx。
"""
import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.audio_transcriber import audio_transcriber
from app.services.path_security import validate_upload_path
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_upload
from app.utils.response import success_response
from app.utils.security import get_current_user
from app.utils.upload_validation import (
    FileUploadError,
    check_magic_bytes,
    detect_file_type,
    max_size_for_file_type,
    save_upload_to_disk,
    validate_content_type,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["音频转录"])


@router.post("/transcribe", response_model=None)
@rate_limit_upload()
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    language: str = "zh",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传音频并转录为文本（P0-1 语音讨论入口）。

    复用与 files.py 一致的上传安全策略：
    - 扩展名/MIME/Magic Bytes 三重校验
    - 按类型大小限制（MAX_FILE_SIZE_AUDIO）
    - 路径安全校验，强制落盘到 UPLOAD_ROOT/<user_id>/
    文件转录完成后删除临时落盘文件，不长期保留。
    """
    original_name = os.path.basename(file.filename or "audio.webm")

    # 1. 类型检测 + MIME 白名单（仅允许音频）
    try:
        file_type = detect_file_type(original_name)
        if file_type != "audio":
            raise FileUploadError(ErrorCode.FILE_TYPE_NOT_ALLOWED)
        validate_content_type(original_name, file.content_type, file_type)
    except FileUploadError as e:
        await file.close()
        raise HTTPException(status_code=400, detail=e.error_code) from e

    # 2. 构造安全写盘路径（使用随机文件名避免重名/路径注入）
    safe_name = f"{uuid.uuid4().hex}.{original_name.rsplit('.', 1)[-1] if '.' in original_name else 'webm'}"
    try:
        target_path = validate_upload_path(str(current_user.id), safe_name)
    except Exception as e:
        logger.warning(f"音频上传路径校验失败: {e}")
        await file.close()
        raise HTTPException(status_code=400, detail=ErrorCode.FILE_TYPE_NOT_ALLOWED) from e

    # 3. 流式落盘 + 大小限制 + Magic Bytes 校验
    try:
        max_bytes = max_size_for_file_type("audio")
        file_size, first_chunk = await save_upload_to_disk(file, target_path, max_bytes)
        if first_chunk:
            check_magic_bytes(first_chunk, "audio", original_name)
    except FileUploadError as e:
        await file.close()
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else 400,
            detail=e.error_code,
        ) from e
    finally:
        await file.close()

    # 4. 转录（faster-whisper，异步包一层 to_thread）
    try:
        text = await audio_transcriber.transcribe(target_path, language=language)
    finally:
        # 转录完成后删除临时文件
        try:
            os.remove(target_path)
        except OSError:
            pass

    if not text:
        logger.warning("音频转录返回空文本（transcriber 可能不可用）")

    await log_audit(
        db, current_user, "transcribe_audio", "audio", target_path,
        request=request,
        details={"file_size": file_size, "text_length": len(text), "available": audio_transcriber.is_available},
    )

    return success_response(
        data={
            "text": text,
            "language": language,
            "available": audio_transcriber.is_available,
        },
        message="音频转录完成" if text else "音频转录不可用或未识别到内容",
    )
