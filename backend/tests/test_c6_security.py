"""C6: API 端点安全加固 测试。

覆盖缺口：
- S10: copilot.py 3处 str(e) 泄漏异常详情（修复后应返回 ErrorCode 常量）
- T21: ClamAV 病毒扫描（upload_validation.scan_file_for_viruses）
"""
import os
import asyncio
from unittest.mock import patch, MagicMock

import pytest

from app.utils.upload_validation import (
    scan_file_for_viruses,
    FileUploadError,
    _scan_file_sync,
)
from app.utils.error_codes import ErrorCode
from app.config import settings


# ---------------------------------------------------------------------------
# S10: copilot.py 异常泄漏修复验证
# ---------------------------------------------------------------------------

class TestS10CopilotExceptionLeakage:
    """S10: copilot.py 不应向客户端泄漏异常详情。"""

    def test_copilot_source_no_str_e_in_detail(self):
        """源码检查：copilot.py 中不应存在 detail=str(e) 模式。"""
        copilot_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "app", "api", "copilot.py",
        )
        with open(copilot_path, "r", encoding="utf-8") as f:
            source = f.read()
        # 不应存在 detail=str(e) 或 detail=str(e) or 模式
        assert "detail=str(e)" not in source, (
            "S10 修复失败：copilot.py 仍存在 detail=str(e) 异常泄漏"
        )
        assert "detail=str(e) or" not in source, (
            "S10 修复失败：copilot.py 仍存在 detail=str(e) or ErrorCode 模式"
        )

    def test_copilot_uses_error_codes(self):
        """源码检查：copilot.py 异常应使用 ErrorCode 常量。"""
        copilot_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "app", "api", "copilot.py",
        )
        with open(copilot_path, "r", encoding="utf-8") as f:
            source = f.read()
        # 验证三处异常处理使用了 ErrorCode
        assert "ErrorCode.FOLDER_NOT_FOUND" in source
        assert "ErrorCode.FOLDER_ACCESS_DENIED" in source
        assert "ErrorCode.FOLDER_PATH_INVALID" in source

    @pytest.mark.asyncio
    async def test_copilot_value_error_returns_error_code(self, client):
        """集成测试：copilot 异常响应应返回 ErrorCode 而非异常字符串。"""
        # 先注册并登录用户（无企业，会触发 SETUP_ENTERPRISE_REQUIRED）
        await client.post("/api/v1/auth/register", json={
            "email": "copilots10@test.com",
            "name": "S10测试",
            "password": "pass1234",
        })

        # 发送无效消息
        resp = await client.post("/api/v1/setup/copilot", json={
            "message": "",  # 空消息
        })

        # 可能返回 400（INVALID_REQUEST）或 403（SETUP_ENTERPRISE_REQUIRED）
        assert resp.status_code in (400, 403), \
            f"期望 400 或 403，实际 {resp.status_code}"
        body = resp.json()
        message = body.get("message", "")

        # 核心验证：返回的 message 必须是 ErrorCode 常量，不是异常字符串
        assert message in (
            ErrorCode.INVALID_REQUEST,
            ErrorCode.FOLDER_PATH_INVALID,
            ErrorCode.SETUP_ENTERPRISE_REQUIRED,
        ), f"应返回 ErrorCode 常量，实际返回: {message}"

        # 确保不泄漏 Python 异常详情
        assert "Traceback" not in message
        assert "str(e)" not in message


# ---------------------------------------------------------------------------
# T21: ClamAV 病毒扫描单元测试
# ---------------------------------------------------------------------------

class TestT21ClamAVScanning:
    """T21: ClamAV 病毒扫描功能测试。"""

    def test_scan_skipped_when_clamd_host_empty(self, tmp_path, monkeypatch):
        """CLAMD_HOST 为空时应跳过扫描（开发环境默认行为）。"""
        monkeypatch.setattr(settings, "CLAMD_HOST", "")
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        # 应无异常抛出（直接跳过）
        asyncio.run(scan_file_for_viruses(str(test_file)))

    def test_scan_skipped_when_clamd_module_missing(self, tmp_path, monkeypatch):
        """python-clamd 未安装时应跳过扫描（不报错）。"""
        monkeypatch.setattr(settings, "CLAMD_HOST", "localhost")
        monkeypatch.setattr(settings, "CLAMD_PORT", 3310)
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        # 模拟 clamd 模块不存在
        import sys
        original_clamd = sys.modules.get("clamd")
        sys.modules["clamd"] = None  # 触发 ModuleNotFoundError

        try:
            is_clean, virus = _scan_file_sync(str(test_file))
            assert is_clean is True
            assert virus is None
        finally:
            if original_clamd is not None:
                sys.modules["clamd"] = original_clamd
            else:
                sys.modules.pop("clamd", None)

    def test_scan_detects_virus(self, tmp_path, monkeypatch):
        """检测到病毒时应返回 (False, virus_name)。"""
        monkeypatch.setattr(settings, "CLAMD_HOST", "localhost")
        monkeypatch.setattr(settings, "CLAMD_PORT", 3310)
        test_file = tmp_path / "infected.txt"
        test_file.write_text("EICAR-TEST-STRING")

        # Mock clamd 模块
        mock_clamd = MagicMock()
        mock_instance = MagicMock()
        mock_instance.scan_stream.return_value = {
            "stream": ("FOUND", "Eicar-Test-Signature")
        }
        mock_clamd.ClamdNetworkSocket.return_value = mock_instance

        with patch.dict("sys.modules", {"clamd": mock_clamd}):
            is_clean, virus = _scan_file_sync(str(test_file))

        assert is_clean is False
        assert virus == "Eicar-Test-Signature"

    def test_scan_clean_file(self, tmp_path, monkeypatch):
        """干净文件应返回 (True, None)。"""
        monkeypatch.setattr(settings, "CLAMD_HOST", "localhost")
        monkeypatch.setattr(settings, "CLAMD_PORT", 3310)
        test_file = tmp_path / "clean.txt"
        test_file.write_text("safe content")

        mock_clamd = MagicMock()
        mock_instance = MagicMock()
        mock_instance.scan_stream.return_value = {
            "stream": ("OK", None)
        }
        mock_clamd.ClamdNetworkSocket.return_value = mock_instance

        with patch.dict("sys.modules", {"clamd": mock_clamd}):
            is_clean, virus = _scan_file_sync(str(test_file))

        assert is_clean is True
        assert virus is None

    @pytest.mark.asyncio
    async def test_scan_deletes_file_on_virus(self, tmp_path, monkeypatch):
        """检测到病毒时应删除文件并抛出 FileUploadError。"""
        monkeypatch.setattr(settings, "CLAMD_HOST", "localhost")
        monkeypatch.setattr(settings, "CLAMD_PORT", 3310)
        test_file = tmp_path / "virus.txt"
        test_file.write_text("malicious")
        assert test_file.exists()

        mock_clamd = MagicMock()
        mock_instance = MagicMock()
        mock_instance.scan_stream.return_value = {
            "stream": ("FOUND", "Test-Virus")
        }
        mock_clamd.ClamdNetworkSocket.return_value = mock_instance

        with patch.dict("sys.modules", {"clamd": mock_clamd}):
            with pytest.raises(FileUploadError) as exc_info:
                await scan_file_for_viruses(str(test_file))

        assert exc_info.value.error_code == ErrorCode.FILE_VIRUS_DETECTED
        # 文件应已被删除
        assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_scan_raises_unavailable_on_connection_error(self, tmp_path, monkeypatch):
        """ClamAV 连接失败时应抛出 FILE_SCAN_UNAVAILABLE 并删除文件。"""
        monkeypatch.setattr(settings, "CLAMD_HOST", "localhost")
        monkeypatch.setattr(settings, "CLAMD_PORT", 3310)
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        mock_clamd = MagicMock()
        mock_instance = MagicMock()
        # 模拟连接异常
        mock_instance.scan_stream.side_effect = ConnectionRefusedError("Connection refused")
        mock_clamd.ClamdNetworkSocket.return_value = mock_instance

        with patch.dict("sys.modules", {"clamd": mock_clamd}):
            with pytest.raises(FileUploadError) as exc_info:
                await scan_file_for_viruses(str(test_file))

        assert exc_info.value.error_code == ErrorCode.FILE_SCAN_UNAVAILABLE
        # 文件应已被删除（安全起见）
        assert not test_file.exists()

    def test_scan_large_file_uses_scan_file(self, tmp_path, monkeypatch):
        """大文件（>25MB）应使用 scan_file 而非 scan_stream。"""
        monkeypatch.setattr(settings, "CLAMD_HOST", "localhost")
        monkeypatch.setattr(settings, "CLAMD_PORT", 3310)

        # 创建一个大于 25MB 的假文件（用 sparse file 避免实际占用磁盘）
        test_file = tmp_path / "large.bin"
        # 写入 26MB 数据
        test_file.write_bytes(b"\x00" * (26 * 1024 * 1024))

        mock_clamd = MagicMock()
        mock_instance = MagicMock()
        mock_instance.scan_file.return_value = {
            str(test_file): ("OK", None)
        }
        mock_clamd.ClamdNetworkSocket.return_value = mock_instance

        with patch.dict("sys.modules", {"clamd": mock_clamd}):
            is_clean, _ = _scan_file_sync(str(test_file))

        assert is_clean is True
        # 验证使用了 scan_file 而非 scan_stream
        mock_instance.scan_file.assert_called_once_with(str(test_file))
        mock_instance.scan_stream.assert_not_called()
