"""P0-S5: ffmpeg_runner 安全与异步执行测试。"""
import asyncio
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRunFFmpeg:
    """run_ffmpeg 执行封装测试。"""

    @pytest.mark.asyncio
    async def test_run_ffmpeg_uses_create_subprocess_exec(self):
        """应使用列表参数调用 ffmpeg，禁止 shell 解析。"""
        from app.utils.ffmpeg_runner import run_ffmpeg

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"stdout", b"stderr"))

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
        ) as mock_create:
            result = await run_ffmpeg(["-version"], timeout=5)

        assert result.returncode == 0
        mock_create.assert_awaited_once()
        call_args = mock_create.call_args
        # 第一个位置参数必须是 ffmpeg 可执行文件名
        assert call_args.args[0] == "ffmpeg"
        # 后续参数应为我们传入的列表元素
        assert call_args.args[1] == "-version"
        # 必须禁用 stdin
        assert call_args.kwargs.get("stdin") is not None

    @pytest.mark.asyncio
    async def test_run_ffmpeg_rejects_non_string_args(self):
        """非字符串参数应直接拒绝，避免类型错误导致异常行为。"""
        from app.utils.ffmpeg_runner import run_ffmpeg

        with pytest.raises(ValueError, match="ffmpeg 参数必须是字符串列表"):
            await run_ffmpeg(["-i", 123])

    @pytest.mark.asyncio
    async def test_run_ffmpeg_timeout_kills_process(self):
        """超时时应杀掉子进程并抛出 TimeoutExpired。"""
        from app.utils.ffmpeg_runner import run_ffmpeg

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with pytest.raises(subprocess.TimeoutExpired):
                await run_ffmpeg(["-i", "in.mp4", "out.wav"], timeout=1)

        mock_proc.kill.assert_called_once()


class TestSafeInputCopy:
    """safe_ffmpeg_input_copy 安全复制测试。"""

    @pytest.mark.asyncio
    async def test_malicious_filename_copied_safely(self, tmp_path):
        """包含特殊字符的文件名应被复制为安全随机名，不产生命令注入风险。"""
        from app.utils.ffmpeg_runner import safe_ffmpeg_input_copy, cleanup_ffmpeg_temp

        # 构造一个以 ffmpeg 选项前缀开头、含特殊字符的文件名
        evil_name = "-i --evil;option.mp4"
        input_path = tmp_path / evil_name
        input_path.write_text("fake video content")

        temp_dir, safe_path = await safe_ffmpeg_input_copy(str(input_path))
        try:
            assert os.path.exists(safe_path)
            # 安全文件名以 input_ 开头，不以 '-' 开头，避免被解析为 ffmpeg 选项
            assert os.path.basename(safe_path).startswith("input_")
            # 文件内容应被原样复制
            with open(safe_path, "rb") as f:
                assert f.read() == b"fake video content"
        finally:
            cleanup_ffmpeg_temp(temp_dir)

    @pytest.mark.asyncio
    async def test_missing_extension_uses_bin(self, tmp_path):
        """无扩展名输入应使用 .bin 扩展名。"""
        from app.utils.ffmpeg_runner import safe_ffmpeg_input_copy, cleanup_ffmpeg_temp

        input_path = tmp_path / "noext"
        input_path.write_text("content")

        temp_dir, safe_path = await safe_ffmpeg_input_copy(str(input_path))
        try:
            assert safe_path.endswith(".bin")
        finally:
            cleanup_ffmpeg_temp(temp_dir)


class TestCheckFFmpegAvailable:
    """ffmpeg 可用性检查测试。"""

    @pytest.mark.asyncio
    async def test_returns_true_when_ffmpeg_works(self):
        from app.utils.ffmpeg_runner import check_ffmpeg_available

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("app.utils.ffmpeg_runner.run_ffmpeg", new=AsyncMock(return_value=mock_result)):
            assert await check_ffmpeg_available() is True

    @pytest.mark.asyncio
    async def test_returns_false_on_missing_ffmpeg(self):
        from app.utils.ffmpeg_runner import check_ffmpeg_available

        with patch(
            "app.utils.ffmpeg_runner.run_ffmpeg",
            new=AsyncMock(side_effect=FileNotFoundError),
        ):
            assert await check_ffmpeg_available() is False
