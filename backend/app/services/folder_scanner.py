import os
import mimetypes
from dataclasses import dataclass
from typing import Optional

from app.services.path_security import (
    validate_path, PathSecurityError,
)


@dataclass
class ScannedFile:
    name: str
    path: str
    size: int
    file_type: str
    extension: str


FILE_TYPE_MAP = {
    ".pdf": "document",
    ".doc": "document",
    ".docx": "document",
    ".txt": "document",
    ".md": "document",
    ".rtf": "document",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".csv": "spreadsheet",
    ".ppt": "presentation",
    ".pptx": "presentation",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
    ".svg": "image",
    ".mp4": "video",
    ".avi": "video",
    ".mov": "video",
    ".mkv": "video",
    ".wmv": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".flac": "audio",
    ".aac": "audio",
    ".json": "data",
    ".xml": "data",
    ".yaml": "data",
    ".yml": "data",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".java": "code",
    ".cpp": "code",
    ".c": "code",
    ".go": "code",
    ".rs": "code",
    ".html": "code",
    ".css": "code",
    ".sql": "code",
    ".zip": "archive",
    ".rar": "archive",
    ".7z": "archive",
    ".tar": "archive",
    ".gz": "archive",
}


def classify_file_type(extension: str) -> str:
    ext = extension.lower()
    if ext in FILE_TYPE_MAP:
        return FILE_TYPE_MAP[ext]
    mime_type, _ = mimetypes.guess_type(f"file{ext}")
    if mime_type:
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"
        if mime_type.startswith("audio/"):
            return "audio"
        if mime_type.startswith("text/"):
            return "document"
    return "other"


def scan_folder(path: str, recursive: bool = True, allowed_root: Optional[str] = None) -> list[ScannedFile]:
    # P0-04: 路径校验，防止扫描任意目录
    # allowed_root: 指定允许的根目录（None 时默认 UPLOAD_ROOT）；
    #   服务端控制的示例数据目录（SAMPLE_DATA_DIR）可显式传入，绕过 UPLOAD_ROOT 限制
    try:
        path = validate_path(path, allowed_root=allowed_root, must_exist=True)
    except PathSecurityError as e:
        raise ValueError(f"目录路径不合法: {e}") from e
    except FileNotFoundError:
        raise FileNotFoundError(f"目录不存在: {path}") from None

    if not os.path.isdir(path):
        raise FileNotFoundError(f"路径不是目录: {path}")

    results: list[ScannedFile] = []
    # P0-04: 扩展 skip_dirs，覆盖更多常见无关目录
    skip_dirs = {
        ".git", "node_modules", "__pycache__", ".venv", "venv", ".env",
        ".svn", ".hg", "CVS", ".idea", ".vscode", "dist", "build",
        ".pytest_cache", ".mypy_cache", "coverage", ".next", ".turbo",
    }

    if recursive:
        # followlinks=False（默认）：不跟随符号链接，防止循环
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for filename in files:
                filepath = os.path.join(root, filename)
                _append_file(results, filepath, path)
    else:
        for entry in os.scandir(path):
            if entry.is_file():
                _append_file(results, entry.path, path)

    return results


def _append_file(results: list[ScannedFile], filepath: str, base_root: str) -> None:
    try:
        stat = os.stat(filepath)
        name = os.path.basename(filepath)
        _, ext = os.path.splitext(name)
        file_type = classify_file_type(ext)
        results.append(
            ScannedFile(
                name=name,
                # P0-04: 返回绝对路径供下游处理；API 响应前再脱敏
                path=filepath,
                size=stat.st_size,
                file_type=file_type,
                extension=ext.lower(),
            )
        )
    except OSError:
        pass
