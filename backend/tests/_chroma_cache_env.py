"""Chroma ONNX 模型缓存可写性兜底（测试环境修复）。

chromadb 的 ``ONNXMiniLM_L6_V2`` 在**类定义时**就用 ``Path.home()`` 算出模型
缓存目录 ``~/.cache/chroma/onnx_models/<model>/onnx``。某些机器上该目录存在
但 ACL 不可读，向量化步骤会以 ``Permission denied`` 失败 —— 看着像产品缺陷，
其实是环境问题。

本模块必须在任何 app / chromadb 导入**之前**执行（缓存路径在类定义时就固定了），
因此由 ``tests/conftest.py`` 作为第一个导入调用。

只在默认路径确实不可读时改道到临时目录，正常机器完全不受影响；改道也失败时
保持原样，把原始错误交回，不掩盖问题。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: Chroma 的 ONNX 模型在缓存目录下的固定子路径（用于探针）。
_MODEL_SUBDIR = ("all-MiniLM-L6-v2", "onnx")


def ensure_chroma_onnx_cache_writable() -> str | None:
    """确保 Chroma 的 ONNX 缓存可读可写。

    :return: 实际生效的缓存根目录；未改道时返回 ``None``。
    """
    model_dir = Path(os.path.expanduser("~")).joinpath(
        ".cache", "chroma", "onnx_models", *_MODEL_SUBDIR
    )
    if _is_writable(model_dir):
        return None

    fallback = Path(tempfile.gettempdir()) / "autoteams-chroma-onnx-cache"
    if not _is_writable(fallback):
        # 改道也不可行：不要伪造"已处理"，把原始权限错误留给调用方。
        return None

    os.environ["USERPROFILE"] = str(fallback)
    os.environ["HOME"] = str(fallback)
    os.environ["AUTOTEAMS_CHROMA_ONNX_CACHE_REDIRECTED"] = str(fallback)
    return str(fallback)


def _is_writable(directory: Path) -> bool:
    probe = directory / ".write-probe"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
