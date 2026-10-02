"""AutoTeams 4.0 安全执行沙箱（SafeHarness）单元测试。"""
import pytest
import sys
from pathlib import Path
from app.services.runtime.safe_harness import SafeHarness, RuntimeLock


@pytest.mark.asyncio
async def test_safe_harness_workspace_and_path_security(tmp_path):
    """测试工作区目录隔离与路径穿越拦截。"""
    session_id = "test-session-001"
    harness = SafeHarness(session_id, base_workspace_dir=str(tmp_path))

    # 1. 验证工作区创建
    assert harness.workspace_dir.exists()

    # 2. 正常写入与读取
    harness.write_file("sub/hello.txt", "Hello AutoTeams")
    content = harness.read_file("sub/hello.txt")
    assert content == "Hello AutoTeams"

    # 3. 路径穿越拦截
    with pytest.raises(PermissionError):
        harness.resolve_safe_path("../outside.txt")

    with pytest.raises(PermissionError):
        harness.write_file("../../hack.txt", "evil")

    # 4. 清理工作区
    harness.cleanup_workspace()
    assert not harness.workspace_dir.exists()


@pytest.mark.asyncio
async def test_safe_harness_command_execution(tmp_path):
    """测试安全命令执行。"""
    session_id = "test-session-002"
    harness = SafeHarness(session_id, base_workspace_dir=str(tmp_path))

    # 执行简单 Python 打印命令
    cmd = [sys.executable, "-c", "print('AutoTeams Sandboxed Command')"]
    result = await harness.execute_command(cmd, timeout_seconds=5.0)

    assert result.exit_code == 0
    assert "AutoTeams Sandboxed Command" in result.stdout
    assert not result.timed_out
    assert result.duration_ms > 0

    harness.cleanup_workspace()


@pytest.mark.asyncio
async def test_safe_harness_command_timeout(tmp_path):
    """测试超时自动强制终止进程树。"""
    session_id = "test-session-003"
    harness = SafeHarness(session_id, base_workspace_dir=str(tmp_path))

    # 执行故意超时的 Python sleep 命令
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    result = await harness.execute_command(cmd, timeout_seconds=0.5)

    assert result.timed_out is True
    assert result.exit_code != 0

    harness.cleanup_workspace()


@pytest.mark.asyncio
async def test_runtime_lock():
    """测试会话锁互斥性。"""
    session_id = "session-lock-test"
    lock1 = RuntimeLock.get_lock(session_id)
    lock2 = RuntimeLock.get_lock(session_id)
    assert lock1 is lock2

    async with lock1:
        assert lock2.locked()

    RuntimeLock.release_session(session_id)
