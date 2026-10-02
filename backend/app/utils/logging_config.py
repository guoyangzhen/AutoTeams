"""P1-06: 结构化日志配置 + 日志轮转。

提供两种模式：
- JSON 结构化日志（生产）：每条日志一行 JSON，便于 ELK/Loki 采集
- 人类可读格式（开发）：保持原有的 %(asctime)s 格式

日志轮转：RotatingFileHandler，默认 10MB × 5 份。
控制台同时输出（便于 docker logs 查看）。
"""
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone

from app.utils.request_context import get_request_id
from app.utils.metrics import errors_total


class RequestIdFilter(logging.Filter):
    """P3-1: 自动为每个 LogRecord 注入当前 request_id。

    无论业务代码是否显式传 extra={"request_id": ...}，
    过滤器都会尝试从 contextvar 读取并写入 record，供 JsonFormatter 输出。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.request_id = get_request_id()  # type: ignore[attr-defined]
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            record.request_id = None  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式器。

    每条日志输出为一行 JSON，包含 timestamp/level/logger/message/exc_info。
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exc_info"] = self.formatException(record.exc_info)
        # 附加自定义字段（通过 extra= 传入）
        for key in ("user_id", "enterprise_id", "agent_id", "request_id"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(debug: bool = False, log_dir: str = "logs") -> None:
    """配置全局日志。

    Args:
        debug: True 用人类可读格式（开发），False 用 JSON 格式（生产）
        log_dir: 日志文件目录
    """
    level = logging.DEBUG if debug else logging.INFO

    # 清除默认 handler（避免重复配置）
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handlers: list[logging.Handler] = []

    # P3-1: request_id 过滤器，自动注入到所有日志记录
    request_id_filter = RequestIdFilter()

    # 控制台 handler（docker logs 友好）
    console_handler = logging.StreamHandler()
    console_handler.addFilter(request_id_filter)
    if debug:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(request_id)s - %(message)s"
            )
        )
    else:
        console_handler.setFormatter(JsonFormatter())
    handlers.append(console_handler)

    # 文件 handler（轮转：10MB × 5 份）
    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "autoteams.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.addFilter(request_id_filter)
        file_handler.setFormatter(
            JsonFormatter() if not debug else
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(request_id)s - %(message)s"
            )
        )
        handlers.append(file_handler)
    except OSError:
        # 容器环境可能无写权限（只读文件系统），降级为仅控制台
        pass

    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )

    # 降低第三方库的日志级别（避免噪音）
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
