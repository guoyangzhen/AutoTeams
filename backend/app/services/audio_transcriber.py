"""音频转录服务（基于 faster-whisper）。

重构说明（D5）：
之前 video_processor._transcribe_audio 只是提取音频后返回空字符串，
注释"Whisper API未集成"。现改为使用 faster-whisper 进行本地转录。

设计要点：
- 懒加载模型：首次调用时才加载，避免启动慢
- 优雅降级：如果 faster-whisper 或 ffmpeg 不可用，返回空字符串而非崩溃
- 模型缓存：单例模式，避免重复加载模型
- 线程安全：使用 asyncio.to_thread 包装同步调用
"""
import os
import logging
import tempfile

from app.config import settings
from app.services.path_security import validate_path, validate_file_size, PathSecurityError
from app.utils.ffmpeg_runner import (
    run_ffmpeg, safe_ffmpeg_input_copy, cleanup_ffmpeg_temp, check_ffmpeg_available,
)
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

# faster-whisper 模型大小：tiny < base < small < medium < large
# base 模型约 145MB，中文识别效果可接受，速度较快
_DEFAULT_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
# 设备：cpu 或 cuda
_DEFAULT_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
# 计算类型：int8 (CPU最快) / float16 (GPU) / float32
_DEFAULT_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")


class AudioTranscriber:
    """faster-whisper 音频转录服务（单例模式）。"""

    def __init__(self):
        self._model = None
        self._initialized = False
        self._available = True  # 标记是否可用，不可用后不再重试

    def _load_model(self):
        """懒加载 faster-whisper 模型。"""
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
            logger.info(
                f"加载 faster-whisper 模型: size={_DEFAULT_MODEL_SIZE}, "
                f"device={_DEFAULT_DEVICE}, compute_type={_DEFAULT_COMPUTE_TYPE}"
            )
            self._model = WhisperModel(
                _DEFAULT_MODEL_SIZE,
                device=_DEFAULT_DEVICE,
                compute_type=_DEFAULT_COMPUTE_TYPE,
            )
            self._initialized = True
            logger.info("faster-whisper 模型加载完成")
            return self._model
        except ImportError:
            logger.warning(
                "faster-whisper 未安装，音频转录不可用。"
                "请运行: pip install faster-whisper"
            )
            self._available = False
            return None
        except (ValueError, RuntimeError, OSError) as e:
            logger.error(f"加载 faster-whisper 模型失败: {e}", exc_info=True)
            self._available = False
            return None
        except (TypeError, AttributeError, KeyError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"加载 faster-whisper 模型失败（未预期错误）: {e}", exc_info=True)
            self._available = False
            return None

    @property
    def is_available(self) -> bool:
        """检查服务是否可用（faster-whisper 已安装且模型可加载）。"""
        if not self._available:
            return False
        if self._initialized:
            return True
        # 尝试加载
        return self._load_model() is not None

    async def transcribe(self, audio_path: str, language: str = "zh") -> str:
        """转录音频文件。

        Args:
            audio_path: 音频文件路径（wav, mp3 等）
            language: 语言代码，默认中文

        Returns:
            转录文本，如果不可用或失败则返回空字符串
        """
        if not self._available:
            return ""

        # P0-04/07: 路径与大小校验
        try:
            audio_path = validate_path(audio_path, must_exist=True)
            validate_file_size(audio_path, settings.MAX_FILE_SIZE_AUDIO)
        except PathSecurityError as e:
            logger.warning(f"音频路径校验失败: {e}")
            return ""
        except FileNotFoundError:
            logger.warning(f"音频文件不存在: {audio_path}")
            return ""

        basename = os.path.basename(audio_path)
        if basename.startswith("-"):
            logger.warning(f"非法文件名（不能以 '-' 开头）: {basename}")
            return ""

        model = self._load_model()
        if model is None:
            return ""

        try:
            # faster-whisper 的 transcribe 返回惰性生成器，实际转录在迭代时才执行。
            # 技术审计 R2 H4: 必须将 transcribe + 迭代收集整体放入 to_thread，
            # 否则 line 134 的生成器迭代在主线程执行，CPU 密集转录阻塞事件循环。
            import asyncio

            def _transcribe_and_collect():
                segments, info = model.transcribe(
                    audio_path,
                    language=language,
                    beam_size=5,
                    vad_filter=True,  # 过滤静音段
                )
                return " ".join(segment.text.strip() for segment in segments), info

            text, info = await asyncio.to_thread(_transcribe_and_collect)
            logger.info(
                f"音频转录完成: {audio_path}, "
                f"时长={info.duration:.1f}s, 文本长度={len(text)}"
            )
            return text.strip()
        except (ValueError, RuntimeError, OSError) as e:
            logger.error(f"音频转录失败 {audio_path}: {e}", exc_info=True)
            return ""
        except (TypeError, AttributeError, KeyError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"音频转录失败（未预期错误） {audio_path}: {e}", exc_info=True)
            raise RuntimeError(f"音频转录失败: {audio_path}") from e

    async def transcribe_video(self, video_path: str, language: str = "zh") -> str:
        """P0-S5 + P0-PERF: 从视频文件中安全、异步地提取音频并转录。

        - 输入文件先复制到临时目录并改为随机安全文件名，再交给 ffmpeg，
          杜绝命令注入与路径穿越风险。
        - ffmpeg 调用使用 asyncio.create_subprocess_exec，避免阻塞事件循环。

        Args:
            video_path: 视频文件路径
            language: 语言代码

        Returns:
            转录文本，如果 ffmpeg 不可用或转录失败则返回空字符串
        """
        if not self._available:
            return ""

        if not await check_ffmpeg_available():
            logger.warning("ffmpeg 不可用，无法从视频提取音频")
            return ""

        # P0-04/07: 路径与大小校验
        try:
            safe_video_path = validate_path(video_path, must_exist=True)
            validate_file_size(safe_video_path, settings.MAX_FILE_SIZE_VIDEO)
        except PathSecurityError as e:
            logger.warning(f"视频路径校验失败: {e}")
            return ""
        except FileNotFoundError:
            logger.warning(f"视频文件不存在: {video_path}")
            return ""

        temp_dir: str | None = None
        tmp_path: str | None = None
        try:
            temp_dir, safe_input = await safe_ffmpeg_input_copy(safe_video_path)
            fd, tmp_path = tempfile.mkstemp(dir=temp_dir, suffix=".wav")
            os.close(fd)
            os.chmod(tmp_path, 0o600)

            args = [
                "-y",
                "-i", safe_input,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1",
                tmp_path,
            ]
            result = await run_ffmpeg(args, timeout=300)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="ignore")[:500]
                logger.error(f"ffmpeg 提取音频失败: {stderr}")
                return ""

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 1000:
                logger.warning(f"提取的音频文件过小或不存在: {video_path}")
                return ""

            # 转录提取的音频
            return await self.transcribe(tmp_path, language=language)

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            cleanup_ffmpeg_temp(temp_dir)


# 全局单例
audio_transcriber = AudioTranscriber()
