import os
import asyncio
import base64
import logging
import subprocess

import httpx

from app.services.llm_service import llm_service
from app.services.audio_transcriber import audio_transcriber
from app.config import settings
from app.services.path_security import (
    validate_path, validate_file_size, sanitize_path_for_response, PathSecurityError,
)
from app.utils.ffmpeg_runner import (
    run_ffmpeg, safe_ffmpeg_input_copy, cleanup_ffmpeg_temp,
)
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

VIDEO_ANALYSIS_PROMPT = """请根据以下视频帧截图，描述这个视频的主要内容：
1. 视频的主题和场景
2. 关键画面和转场
3. 如果包含文字/字幕，请提取内容
4. 视频的整体风格和用途
请用中文回答。"""

AUDIO_SUMMARY_PROMPT = """视频文件 "{filename}" 的音频无法转录（faster-whisper 或 ffmpeg 不可用）。
请根据文件名和上下文，生成一个简短的描述。
文件信息：{file_info}
请用中文回答，不超过100字。"""


async def process_video(path: str) -> dict:
    # P0-04/07: 路径与大小校验
    try:
        safe_path = validate_path(path, must_exist=True)
        validate_file_size(safe_path, settings.MAX_FILE_SIZE_VIDEO)
    except PathSecurityError as e:
        logger.warning(f"视频路径校验失败: {e}")
        raise ValueError(f"文件路径不合法或过大: {e}") from e
    except FileNotFoundError:
        raise FileNotFoundError(f"文件不存在: {path}") from None
    path = safe_path

    basename = os.path.basename(path)
    if basename.startswith("-"):
        raise ValueError(f"非法文件名（不能以 '-' 开头）: {basename}")

    ext = os.path.splitext(path)[1].lower()
    supported = {".mp4", ".avi", ".mov", ".mkv", ".wmv"}
    if ext not in supported:
        raise ValueError(f"不支持的视频格式: {ext}")

    result = {
        # P0-04: 不暴露完整服务器路径
        "path": sanitize_path_for_response(path),
        "filename": os.path.basename(path),
        "file_size": os.path.getsize(path),
        "format": ext,
        "keyframes_analysis": "",
        "transcription": "",
        "summary": "",
    }

    try:
        keyframes = await _extract_keyframes(path)
        if keyframes:
            analysis = await _analyze_keyframes(keyframes)
            result["keyframes_analysis"] = analysis
            result["summary"] = analysis

        # D5: 使用 faster-whisper 转录视频音频
        transcription = await audio_transcriber.transcribe_video(path)
        if transcription:
            result["transcription"] = transcription
        else:
            fallback = await _generate_fallback_summary(path)
            result["transcription"] = fallback

        if not result["summary"] and result["transcription"]:
            result["summary"] = result["transcription"]

    except (ValueError, FileNotFoundError, subprocess.SubprocessError, httpx.HTTPError, RuntimeError, OSError) as e:
        logger.error(f"处理视频失败 {path}: {e}", exc_info=True)
        # 不暴露内部错误细节
        result["error"] = "视频处理失败"
    except (TypeError, KeyError, AttributeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"处理视频失败（未预期错误） {path}: {e}", exc_info=True)
        raise RuntimeError(f"处理视频失败: {path}") from e

    return result


async def _extract_keyframes(path: str, max_frames: int = 5) -> list[str]:
    """P0-S5 + P0-PERF: 安全异步提取视频关键帧。

    - 将输入文件复制到临时目录并使用随机安全文件名，避免 ffmpeg 命令注入。
    - 使用 asyncio.create_subprocess_exec 避免阻塞事件循环。
    """
    temp_dir: str | None = None
    try:
        temp_dir, safe_input = await safe_ffmpeg_input_copy(path)
        safe_output = os.path.join(temp_dir, "frame_%03d.jpg")
        args = [
            "-y",
            "-i", safe_input,
            "-vf", "select=gt(scene\\,0.3),scale=512:-1",
            "-frames:v", str(max_frames),
            "-vsync", "vfr",
            safe_output,
        ]
        result = await run_ffmpeg(args, timeout=120)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")[:500]
            logger.error(f"ffmpeg 提取关键帧失败: {stderr}")
            return []

        frames = []
        for fname in sorted(os.listdir(temp_dir)):
            if not fname.startswith("frame_"):
                continue
            fpath = os.path.join(temp_dir, fname)
            content = await asyncio.to_thread(_read_file_bytes, fpath)
            frames.append(base64.b64encode(content).decode("utf-8"))
        return frames

    except FileNotFoundError:
        logger.warning("ffmpeg 未安装，跳过关键帧提取")
        return []
    except (subprocess.SubprocessError, OSError, PermissionError) as e:
        logger.error(f"提取关键帧失败: {e}", exc_info=True)
        return []
    except (TypeError, ValueError, KeyError, AttributeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"提取关键帧失败（未预期错误）: {e}", exc_info=True)
        raise RuntimeError(f"提取关键帧失败: {path}") from e
    finally:
        cleanup_ffmpeg_temp(temp_dir)


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _analyze_keyframes(keyframes: list[str]) -> str:
    content_parts = [{"type": "text", "text": VIDEO_ANALYSIS_PROMPT}]
    for frame_b64 in keyframes:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
        })

    messages = [{"role": "user", "content": content_parts}]
    try:
        return await llm_service.chat_with_image(messages)
    except (httpx.HTTPError, ValueError, RuntimeError, OSError) as e:
        logger.error(f"分析关键帧失败: {e}", exc_info=True)
        return ""
    except (TypeError, KeyError, AttributeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"分析关键帧失败（未预期错误）: {e}", exc_info=True)
        raise RuntimeError("分析关键帧失败") from e


async def _generate_fallback_summary(path: str) -> str:
    try:
        filename = os.path.basename(path)
        file_size = os.path.getsize(path)
        file_size_mb = file_size / (1024 * 1024)

        prompt = AUDIO_SUMMARY_PROMPT.format(
            filename=filename,
            file_info=f"文件大小: {file_size_mb:.1f}MB, 格式: {os.path.splitext(path)[1]}"
        )

        return await llm_service.chat([{"role": "user", "content": prompt}])
    except (httpx.HTTPError, ValueError, RuntimeError, OSError) as e:
        logger.error(f"生成备用摘要失败: {e}", exc_info=True)
        return ""
    except (TypeError, KeyError, AttributeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"生成备用摘要失败（未预期错误）: {e}", exc_info=True)
        raise RuntimeError("生成备用摘要失败") from e
