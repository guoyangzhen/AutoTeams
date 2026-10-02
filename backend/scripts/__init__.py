"""运维脚本包。

存在本文件是为了让 `python -m scripts.<name>` 稳定可用：AUD-06 记录过
Compose 直接执行 `python scripts/run_*_worker.py`，此时 `sys.path[0]` 是
`scripts/` 而不是仓库根，`from app... import` 必然 ModuleNotFoundError。
"""
