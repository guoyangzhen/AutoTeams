import os
import base64
import logging
import asyncio

import httpx

from app.services.llm_service import llm_service
from app.config import settings
from app.services.path_security import (
    validate_path, validate_file_size, sanitize_path_for_response, PathSecurityError,
)
from app.utils.metrics import errors_total
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

IMAGE_ANALYSIS_PROMPT = """请详细描述这张图片的内容，包括：
1. 图片的主要内容和主题
2. 关键元素和它们的关系
3. 如果包含文字，请提取所有文字内容
4. 如果是图表/数据图，请描述数据趋势和关键数据点
5. 图片的类型（照片、截图、图表、示意图等）

请用中文回答，保持客观准确。"""


async def process_image(path: str) -> dict:
    # P0-04/07: 路径与大小校验
    try:
        safe_path = validate_path(path, must_exist=True)
        validate_file_size(safe_path, settings.MAX_FILE_SIZE_IMAGE)
    except PathSecurityError as e:
        logger.warning(f"图片路径校验失败: {e}")
        # BE-SEC-02: 不暴露原始异常细节
        return {"filename": os.path.basename(path), "analysis": "", "error": ErrorCode.FILE_PATH_INVALID}
    except FileNotFoundError:
        raise FileNotFoundError(f"文件不存在: {path}") from None
    path = safe_path

    ext = os.path.splitext(path)[1].lower()
    supported = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}
    if ext not in supported:
        raise ValueError(f"不支持的图片格式: {ext}")

    try:
        # 异步读取图片字节，避免阻塞事件循环（P-Async）
        def _read_image() -> bytes:
            with open(path, "rb") as f:
                return f.read()
        image_bytes = await asyncio.to_thread(_read_image)
        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        mime_type = _get_mime_type(ext)

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": IMAGE_ANALYSIS_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}"
                        },
                    },
                ],
            }
        ]

        analysis = await llm_service.chat_with_image(messages)

        return {
            # P0-04: 不暴露完整服务器路径
            "path": sanitize_path_for_response(path),
            "filename": os.path.basename(path),
            "analysis": analysis,
            "file_size": os.path.getsize(path),
            "format": ext,
        }

    except (httpx.HTTPError, ValueError, RuntimeError, OSError) as e:
        logger.error(f"处理图片失败 {path}: {e}", exc_info=True)
        return {
            "filename": os.path.basename(path),
            "analysis": "",
            # P0-12 信息泄漏修复：不回传原始异常字符串
            "error": "图片处理失败",
        }
    except (TypeError, KeyError, AttributeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"处理图片失败（未预期错误） {path}: {e}", exc_info=True)
        raise RuntimeError(f"处理图片失败: {path}") from e


def _get_mime_type(ext: str) -> str:
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    return mime_map.get(ext, "image/png")
