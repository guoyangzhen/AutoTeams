"""P0-S5 + P0-PERF: 安全、异步的 ffmpeg 执行封装。

核心设计：
- 将不受信任的输入文件复制到临时目录，并重命名为安全随机名，再交给 ffmpeg，
  杜绝因文件名特殊字符（如以 '-' 开头、含 ';|&$' 等）被解析为 ffmpeg 选项。
- 使用 asyncio.create_subprocess_exec 执行 ffmpeg，避免阻塞事件循环。
- 统一超时、环境变量与临时目录清理。
"""
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import uuid

logger = logging.getLogger(__name__)


def _ffmpeg_env(extra: dict | None = None) -> dict:
    """构造 ffmpeg 子进程环境变量，仅保留最小必要 PATH 与 locale。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }
    if extra:
        env.update(extra)
    return env


async def run_ffmpeg(
    args: list[str],
    timeout: int = 120,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """异步执行 ffmpeg 命令。

    Args:
        args: 传递给 ffmpeg 的参数列表（不含 ffmpeg 本身）。
        timeout: 最大等待秒数。
        env: 额外环境变量。

    Returns:
        subprocess.CompletedProcess，包含 returncode / stdout / stderr。

    Raises:
        subprocess.TimeoutExpired: 执行超时。
        FileNotFoundError: 系统中未找到 ffmpeg。
    """
    # P0-S5: 校验参数类型，防止意外传入非字符串导致命令解析异常
    if not all(isinstance(a, str) for a in args):
        raise ValueError("ffmpeg 参数必须是字符串列表")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdin=asyncio.subprocess.DEVNULL,  # 禁止从 stdin 读取，避免交互式挂起或注入
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_ffmpeg_env(env),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        raise subprocess.TimeoutExpired(cmd=["ffmpeg", *args], timeout=timeout) from None

    return subprocess.CompletedProcess(
        args=["ffmpeg", *args],
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


async def safe_ffmpeg_input_copy(input_path: str) -> tuple[str, str]:
    """将输入文件复制到临时目录并使用安全随机文件名。

    Args:
        input_path: 原始输入文件路径（应已通过 path_security 校验）。

    Returns:
        (temp_dir, safe_input_path)
    """
    ext = os.path.splitext(input_path)[1].lower() or ".bin"
    safe_name = f"input_{uuid.uuid4().hex}{ext}"
    tmpdir = tempfile.mkdtemp(prefix="autoteams_ffmpeg_")
    safe_path = os.path.join(tmpdir, safe_name)
    await asyncio.to_thread(shutil.copy2, input_path, safe_path)
    return tmpdir, safe_path


def cleanup_ffmpeg_temp(temp_dir: str | None) -> None:
    """清理 ffmpeg 临时目录，忽略常见错误。"""
    if not temp_dir:
        return
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except OSError:
        logger.warning(f"清理 ffmpeg 临时目录失败: {temp_dir}", exc_info=True)


async def check_ffmpeg_available(timeout: int = 10) -> bool:
    """异步检查 ffmpeg 是否可用。"""
    try:
        result = await run_ffmpeg(["-version"], timeout=timeout)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
