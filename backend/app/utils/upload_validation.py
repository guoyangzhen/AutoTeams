"""上传文件统一校验工具。

被 `files.py` 与 `skills.py` 共用，避免重复实现并保证上传安全策略一致。
"""
import asyncio
import os
import logging

from fastapi import UploadFile

from app.config import settings
from app.services.folder_scanner import classify_file_type
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)


class FileUploadError(Exception):
    """上传校验失败内部异常，携带错误码。"""

    def __init__(self, error_code: str, status_code: int = 400):
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(error_code)


# BE-SEC-07: 上传文件 content-type 白名单（按 file_type 分组）
_ALLOWED_CONTENT_TYPES = {
    "document": {
        "text/plain",
        "text/markdown",
        "text/rtf",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/json",
        "application/xml",
    },
    "spreadsheet": {
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
    "presentation": {
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
    "data": {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "text/plain",
        "text/yaml",
    },
    "code": {
        "text/plain",
        "text/html",
        "text/css",
        "application/javascript",
        "text/javascript",
        "application/json",
        "application/xml",
    },
    "image": {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/webp",
    },
    "video": {
        "video/mp4",
        "video/avi",
        "video/quicktime",
        "video/x-matroska",
        "video/x-ms-wmv",
    },
    "audio": {
        "audio/mpeg",
        "audio/wav",
        "audio/flac",
        "audio/aac",
        "audio/mp4",
    },
}


def max_size_for_file_type(file_type: str) -> int:
    """根据文件类型返回最大允许字节数。"""
    return {
        "image": settings.MAX_FILE_SIZE_IMAGE,
        "video": settings.MAX_FILE_SIZE_VIDEO,
        "audio": settings.MAX_FILE_SIZE_AUDIO,
    }.get(file_type, settings.MAX_FILE_SIZE_DOCUMENT)


def detect_file_type(filename: str) -> str:
    """基于扩展名检测文件类型，拒绝未知类型。显式拦截 .svg 防止同源存储型 XSS (P1-5)。"""
    _, ext = os.path.splitext(filename)
    ext_lower = ext.lower()
    if ext_lower in (".svg", ".svgz"):
        logger.warning(f"上传文件包含高危 SVG 格式被拦截: {filename}")
        raise FileUploadError(ErrorCode.FILE_TYPE_NOT_ALLOWED)
    file_type = classify_file_type(ext)
    if file_type == "other" or not ext:
        raise FileUploadError(ErrorCode.FILE_TYPE_NOT_ALLOWED)
    return file_type


def validate_content_type(filename: str, content_type: str | None, file_type: str) -> None:
    """校验 MIME 类型是否在允许列表内；缺失时拒绝上传。

    BE-SEC-07: 原实现在 content_type 为空时直接放行，攻击者可通过不发送
    Content-Type 头绕过 MIME 白名单校验。现改为缺失时拒绝上传。
    """
    if not content_type:
        logger.warning(
            f"上传文件 {filename} 未携带 Content-Type，拒绝上传"
        )
        raise FileUploadError(ErrorCode.FILE_TYPE_NOT_ALLOWED)
    allowed = _ALLOWED_CONTENT_TYPES.get(file_type, set())
    if content_type.lower() not in allowed:
        logger.warning(
            f"上传文件 {filename} 的 content-type {content_type} 不在类型 {file_type} 的允许列表中"
        )
        raise FileUploadError(ErrorCode.FILE_TYPE_NOT_ALLOWED)


def check_magic_bytes(first_chunk: bytes, file_type: str, filename: str) -> None:
    """基于 Magic Bytes 做简单校验，明显不匹配时拒绝。"""
    if not first_chunk:
        return

    lower_name = filename.lower()
    checks: dict[str, list[bytes]] = {
        "document": (
            [b"%PDF"]
            if lower_name.endswith(".pdf")
            else [b"PK\x03\x04"]
            if lower_name.endswith((".docx", ".xlsx", ".pptx"))
            else []
        ),
        "image": [b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM", b"RIFF"],
        "video": [b"\x00\x00\x00", b"ftyp", b"RIFF", b"\x1aE\xdf\xa3"],
        "audio": [b"ID3", b"RIFF", b"fLaC", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
    }
    signatures = checks.get(file_type, [])
    if not signatures:
        return

    header = first_chunk[:16]
    if not any(sig in header or header.startswith(sig) for sig in signatures):
        logger.warning(
            f"上传文件 {filename} 的 Magic Bytes 与类型 {file_type} 不匹配: {header[:8]!r}"
        )
        raise FileUploadError(ErrorCode.FILE_TYPE_NOT_ALLOWED)


async def save_upload_to_disk(
    upload_file: UploadFile,
    target_path: str,
    max_bytes: int,
) -> tuple[int, bytes | None]:
    """流式写入上传文件到目标路径，边写边校验大小；返回实际字节数与首 chunk。

    首 chunk 用于后续 Magic Bytes 校验。
    """
    chunk_size = 1024 * 1024  # 1MB
    first_chunk: bytes | None = None
    total = 0

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    try:
        # f.write 是同步 IO，但单 chunk 1MB 的写入耗时很短（OS buffer cache）；
        # 保持同步实现以避免 aiofiles 引入的额外复杂度与潜在并发问题。
        with open(target_path, "wb") as f:
            while True:
                chunk = await upload_file.read(chunk_size)
                if not chunk:
                    break
                if first_chunk is None:
                    first_chunk = chunk
                total += len(chunk)
                if total > max_bytes:
                    # 排空剩余内容，避免连接异常
                    while await upload_file.read(chunk_size):
                        pass
                    raise FileUploadError(ErrorCode.FILE_SIZE_EXCEEDED)
                f.write(chunk)
    except FileUploadError:
        # 删除已写入的部分文件
        try:
            os.remove(target_path)
        except OSError:
            pass
        raise

    if total == 0:
        try:
            os.remove(target_path)
        except OSError:
            pass
        raise FileUploadError(ErrorCode.FILE_EMPTY)

    return total, first_chunk


# ---------------------------------------------------------------------------
# T21: ClamAV 病毒扫描
#
# 生产环境应部署 ClamAV 守护进程并配置 CLAMD_HOST；
# 开发/测试环境 CLAMD_HOST 为空时自动跳过扫描。
# python-clamd 包由 C1 在 requirements.txt 中统一声明（lazy import 避免硬依赖）。
# ---------------------------------------------------------------------------

# ClamAV INSTREAM 单次扫描大小上限（25MB，协议限制）
_CLAMD_MAX_STREAM_SIZE = 25 * 1024 * 1024


def _scan_file_sync(file_path: str) -> tuple[bool, str | None]:
    """同步执行 ClamAV 扫描，返回 (is_clean, virus_name)。

    - (True, None)  表示文件干净
    - (False, name) 表示检测到病毒，name 为病毒名称
    """
    try:
        import clamd
    except ModuleNotFoundError:
        logger.warning(
            "T21: python-clamd 未安装，跳过病毒扫描。"
            "生产环境请在 requirements.txt 中添加 python-clamd>=0.7.0"
        )
        return True, None

    cd = clamd.ClamdNetworkSocket(
        settings.CLAMD_HOST, settings.CLAMD_PORT, timeout=30
    )

    file_size = os.path.getsize(file_path)

    # 大文件使用 scan_file（要求 clamd 可访问文件路径）
    # 小文件使用 scan_stream（通过 socket 传输，跨容器更可靠）
    if file_size > _CLAMD_MAX_STREAM_SIZE:
        logger.info(f"T21: 大文件扫描（scan_file）：{file_path} ({file_size} bytes)")
        result = cd.scan_file(file_path)
    else:
        with open(file_path, "rb") as f:
            result = cd.scan_stream(f)

    # clamd 返回格式：{filename: (status, virus_name)}
    # status: 'OK' | 'FOUND' | 'ERROR'
    for _filename, (status, virus_name) in result.items():
        if status == "FOUND":
            return False, virus_name
        if status == "ERROR":
            logger.error(f"T21: ClamAV 扫描出错：{virus_name}")
            raise FileUploadError(ErrorCode.FILE_SCAN_UNAVAILABLE)
    return True, None


async def scan_file_for_viruses(file_path: str) -> None:
    """T21: 对已落盘的文件执行 ClamAV 病毒扫描。

    - CLAMD_HOST 为空时跳过（开发环境默认行为）
    - 检测到病毒时删除文件并抛出 FileUploadError(FILE_VIRUS_DETECTED)
    - ClamAV 连接失败时抛出 FileUploadError(FILE_SCAN_UNAVAILABLE)
    - 使用 asyncio.to_thread 避免阻塞事件循环

    调用时机：文件已保存到磁盘后、创建 DB 记录前。
    """
    if not settings.CLAMD_HOST:
        # 开发环境未配置 ClamAV，跳过扫描
        return

    try:
        is_clean, virus_name = await asyncio.to_thread(_scan_file_sync, file_path)
    except FileUploadError:
        # 扫描服务异常（FILE_SCAN_UNAVAILABLE）直接向上传播
        _safe_remove(file_path)
        raise
    except Exception as e:
        logger.error(
            f"T21: ClamAV 扫描异常，文件 {file_path}: {e}", exc_info=True
        )
        _safe_remove(file_path)
        raise FileUploadError(ErrorCode.FILE_SCAN_UNAVAILABLE) from e

    if not is_clean:
        logger.warning(
            f"T21: 检测到病毒文件 {file_path}，病毒名: {virus_name}，已删除"
        )
        _safe_remove(file_path)
        raise FileUploadError(ErrorCode.FILE_VIRUS_DETECTED)


def _safe_remove(file_path: str) -> None:
    """安全删除文件，忽略不存在的文件。"""
    try:
        os.remove(file_path)
    except OSError:
        pass
