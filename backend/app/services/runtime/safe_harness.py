"""AutoTeams 4.0 安全执行宿主与沙箱（SafeHarness）。

提供受管子进程生命周期管理、进程树超时强制终止、
工作区独立物理目录隔离（Workspace Sandbox）与会话并发执行防重入锁。
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SafeHarnessCommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


# 安全的环境变量白名单（绝不向受管子进程透传任何含 KEY、SECRET、TOKEN、PASSWORD、DATABASE 的敏感凭证）
_SAFE_ENV_WHITELIST = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL", "LC_CTYPE",
    "PYTHONPATH", "NODE_PATH", "TERM", "TZ",
}


def _build_sanitized_env(custom_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """构建经过严格安全净化的子进程环境变量，杜绝敏感密钥泄漏。"""
    sanitized: Dict[str, str] = {}
    # 先整收集敏感关键词过滤，再按白名单收录，两层互斥：即使白名单误含敏感名也不透传
    _sensitive_markers = ("KEY", "SECRET", "TOKEN", "PASSWD", "PASSWORD", "DATABASE", "REDIS", "CREDENTIAL", "AUTH")
    for k, v in os.environ.items():
        k_upper = k.upper()
        if any(s in k_upper for s in _sensitive_markers):
            continue
        if k_upper in _SAFE_ENV_WHITELIST:
            sanitized[k] = v

    if custom_env:
        for k, v in custom_env.items():
            k_upper = k.upper()
            if not any(s in k_upper for s in ("KEY", "SECRET", "TOKEN", "PASSWD", "PASSWORD", "DATABASE", "REDIS", "CREDENTIAL")):
                sanitized[k] = str(v)
            else:
                logger.warning("[SafeHarness] 阻止向受管子进程传递敏感环境变量: %s", k)

    return sanitized

class RuntimeLock:
    """会话级并发执行防重入锁管理器。"""
    _locks: Dict[str, asyncio.Lock] = {}

    @classmethod
    def get_lock(cls, session_id: str) -> asyncio.Lock:
        if session_id not in cls._locks:
            cls._locks[session_id] = asyncio.Lock()
        return cls._locks[session_id]

    @classmethod
    def release_session(cls, session_id: str) -> None:
        cls._locks.pop(session_id, None)


class SafeHarness:
    """企业级安全执行沙箱。"""

    def __init__(self, session_id: str, base_workspace_dir: Optional[str] = None):
        self.session_id = session_id
        if base_workspace_dir:
            self.base_dir = Path(base_workspace_dir)
        else:
            self.base_dir = Path(os.path.abspath("./data/workspaces"))

        self.workspace_dir = self.base_dir / session_id
        self._ensure_workspace()

    def _ensure_workspace(self) -> None:
        """确保独立的物理工作区目录存在。"""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def resolve_safe_path(self, relative_path: str) -> Path:
        """解析工作区内的安全路径，防止跨目录路径穿越（Path Traversal）。"""
        resolved = (self.workspace_dir / relative_path).resolve()
        # 强制断言解析后的绝对路径必须以工作区目录开头
        if not str(resolved).startswith(str(self.workspace_dir.resolve())):
            raise PermissionError(f"路径穿越检测拦截: 禁止访问工作区外部路径 '{relative_path}'")
        return resolved

    def write_file(self, relative_path: str, content: str) -> Path:
        """在工作区沙箱内安全写入文件。"""
        target = self.resolve_safe_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read_file(self, relative_path: str) -> str:
        """在工作区沙箱内安全读取文件。"""
        target = self.resolve_safe_path(relative_path)
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {relative_path}")
        return target.read_text(encoding="utf-8")

    async def execute_command(
        self,
        command: List[str] | str,
        timeout_seconds: float = 30.0,
        env: Optional[Dict[str, str]] = None,
    ) -> SafeHarnessCommandResult:
        """在受隔离的工作区环境中执行受管命令，支持进程树超时强制终止。"""
        start_time = time.perf_counter()
        is_windows = platform.system() == "Windows"
        exec_env = _build_sanitized_env(env)

        # 彻底废除 shell 执行通道（P0-2 安全修复）
        # 无论入参是字符串还是列表，一律严格通过 shlex 切分，绝不调用 shell 解释器
        if isinstance(command, str):
            cmd_args = shlex.split(command, posix=not is_windows)
        else:
            cmd_args = list(command)

        if not cmd_args:
            return SafeHarnessCommandResult(
                exit_code=1,
                stdout="",
                stderr="Empty command",
                duration_ms=0,
                timed_out=False,
            )

        timed_out = False
        try:
            # 统一采用 create_subprocess_exec 安全启动子进程
            proc = await asyncio.create_subprocess_exec(
                cmd_args[0],
                *cmd_args[1:],
                cwd=str(self.workspace_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=exec_env,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
                exit_code = proc.returncode or 0
                stdout_str = stdout_data.decode("utf-8", errors="replace")
                stderr_str = stderr_data.decode("utf-8", errors="replace")
            except asyncio.TimeoutError:
                timed_out = True
                logger.warning(f"命令执行超时 ({timeout_seconds}s)，正在强制清理进程树 PID: {proc.pid}")
                await self._kill_process_tree(proc.pid, is_windows)
                exit_code = -9
                stdout_str = ""
                stderr_str = f"Execution timed out after {timeout_seconds} seconds"

        except Exception as e:
            logger.error(f"命令启动或执行异常: {e}", exc_info=True)
            return SafeHarnessCommandResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.perf_counter() - start_time) * 1000),
                timed_out=False,
            )

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return SafeHarnessCommandResult(
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )

    async def _kill_process_tree(self, pid: int, is_windows: bool) -> None:
        """跨平台强制终结整棵子进程树，防止僵尸进程遗留。"""
        try:
            if is_windows:
                # Windows 使用 taskkill 强制递归终结进程树
                kill_proc = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
            else:
                import signal
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception as e:
            logger.debug(f"清理进程树 PID {pid} 忽略: {e}")

    def cleanup_workspace(self) -> None:
        """任务结束后安全清理临时工作区。"""
        try:
            if self.workspace_dir.exists():
                shutil.rmtree(self.workspace_dir, ignore_errors=True)
                logger.debug(f"已清理工作区沙箱: {self.workspace_dir}")
        except Exception as e:
            logger.warning(f"清理工作区失败: {e}")
