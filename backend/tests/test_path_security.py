"""P3-5: app/services/path_security.py 覆盖率补充测试。"""
import os
import sys

import pytest

from app.config import settings
from app.services.path_security import (
    PathSecurityError,
    validate_path,
    validate_file_size,
    validate_upload_path,
    sanitize_path_for_response,
)


class TestValidatePath:
    """路径安全校验测试。"""

    def test_valid_path_under_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        sub = tmp_path / "sub"
        sub.mkdir()
        result = validate_path(str(sub))
        assert result == str(sub)

    def test_path_outside_root_rejected(self, tmp_path):
        with pytest.raises(PathSecurityError, match="路径不在允许的根目录下"):
            validate_path(str(tmp_path), allowed_root="/another/root")

    def test_empty_path_rejected(self):
        with pytest.raises(PathSecurityError, match="路径不能为空"):
            validate_path("")

    def test_null_byte_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        with pytest.raises(PathSecurityError, match="非法字符"):
            validate_path(str(tmp_path) + "\x00file.txt")

    def test_symlink_rejected(self, tmp_path, monkeypatch):
        if sys.platform == "win32":
            pytest.skip("Windows 符号链接需管理员权限，跳过")
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        target = tmp_path / "target.txt"
        target.write_text("x")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("当前系统不支持创建符号链接")
        with pytest.raises(PathSecurityError, match="拒绝访问符号链接"):
            validate_path(str(link))

    def test_must_exist_fails_for_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        missing = tmp_path / "missing.txt"
        with pytest.raises(PathSecurityError, match="路径不存在"):
            validate_path(str(missing), must_exist=True)

    def test_traversal_attempt_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        sub = tmp_path / "sub"
        sub.mkdir()
        # 通过 .. 试图跳出根目录
        evil = sub / ".." / ".." / "etc" / "passwd"
        with pytest.raises(PathSecurityError, match="路径不在允许的根目录下"):
            validate_path(str(evil))


class TestValidateFileSize:
    """文件大小校验测试。"""

    def test_size_within_limit(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("x")
        validate_file_size(str(f), 100)

    def test_size_exceeds_limit(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 100)
        with pytest.raises(PathSecurityError, match="超过上限"):
            validate_file_size(str(f), 10)

    def test_zero_max_size_allows_anything(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 1000)
        validate_file_size(str(f), 0)


class TestValidateUploadPath:
    """上传路径构造与校验测试。"""

    def test_valid_upload_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        result = validate_upload_path("user-1", "file.txt")
        assert result.endswith(os.path.join("user-1", "file.txt"))

    def test_filename_with_separator_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        with pytest.raises(PathSecurityError, match="非法文件名"):
            validate_upload_path("user-1", "../file.txt")

    def test_empty_filename_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
        with pytest.raises(PathSecurityError, match="文件名不能为空"):
            validate_upload_path("user-1", "")


class TestSanitizePathForResponse:
    """响应路径脱敏测试。"""

    def test_relative_path_returned(self, tmp_path):
        sub = tmp_path / "dir" / "file.txt"
        sub.parent.mkdir()
        sub.write_text("x")
        result = sanitize_path_for_response(str(sub), allowed_root=str(tmp_path))
        assert result == "dir/file.txt"

    def test_basename_for_outside_path(self, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("x")
        result = sanitize_path_for_response(str(outside), allowed_root="/nonexistent")
        assert result == "secret.txt"
