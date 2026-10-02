"""P3-5: app/utils/logging_config.py 覆盖率补充测试。"""
import json
import logging
import os
from unittest.mock import patch

import pytest

from app.utils.logging_config import JsonFormatter, RequestIdFilter, setup_logging


class TestRequestIdFilter:
    """RequestIdFilter 测试。"""

    def test_filter_sets_request_id(self):
        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="msg", args=(), exc_info=None,
        )
        with patch("app.utils.logging_config.get_request_id", return_value="req-123"):
            assert filt.filter(record) is True
        assert record.request_id == "req-123"

    def test_filter_handles_exception(self):
        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=1,
            msg="msg", args=(), exc_info=None,
        )
        with patch("app.utils.logging_config.get_request_id", side_effect=RuntimeError("boom")):
            assert filt.filter(record) is True
        assert record.request_id is None


class TestJsonFormatter:
    """JsonFormatter 测试。"""

    def test_basic_format(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="app.test", level=logging.INFO, pathname="", lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "hello"
        assert "timestamp" in data

    def test_format_with_extra_fields(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="app.test", level=logging.INFO, pathname="", lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        record.user_id = "u1"
        record.enterprise_id = "e1"
        record.agent_id = "a1"
        record.request_id = "r1"
        output = formatter.format(record)
        data = json.loads(output)
        assert data["user_id"] == "u1"
        assert data["enterprise_id"] == "e1"
        assert data["agent_id"] == "a1"
        assert data["request_id"] == "r1"

    def test_format_with_exception(self):
        import sys
        formatter = JsonFormatter()
        try:
            raise ValueError("oops")
        except ValueError:
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="app.test", level=logging.ERROR, pathname="", lineno=1,
                msg="error", args=(), exc_info=exc_info,
            )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exc_info" in data
        assert "oops" in data["exc_info"]


class TestSetupLogging:
    """setup_logging 测试。"""

    def test_setup_logging_json_mode(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        setup_logging(debug=False, log_dir=log_dir)
        root = logging.getLogger()
        assert root.level == logging.INFO
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
        assert os.path.isdir(log_dir)

    def test_setup_logging_debug_mode(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        setup_logging(debug=True, log_dir=log_dir)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_setup_logging_oserror_fallback(self, tmp_path):
        log_dir = str(tmp_path / "logs")
        with patch("os.makedirs") as mock_makedirs:
            mock_makedirs.side_effect = OSError("no permission")
            # 不应抛出异常，而是降级为仅控制台
            setup_logging(debug=False, log_dir=log_dir)

    def test_setup_logging_removes_existing_handlers(self, tmp_path):
        root = logging.getLogger()
        root.addHandler(logging.NullHandler())
        setup_logging(debug=True, log_dir=str(tmp_path / "logs"))
        assert not any(isinstance(h, logging.NullHandler) for h in root.handlers)
