import os
import sys
import pytest
from app.services.runtime.safe_harness import SafeHarness, _build_sanitized_env


def test_build_sanitized_env_filters_secrets():
    # 模拟容器内敏感环境变量
    os.environ["OPENAI_API_KEY"] = "sk-test-secret-key-123456"
    os.environ["DATABASE_URL"] = "postgresql://admin:superpassword@localhost:5432/autoteams"
    os.environ["JWT_SECRET_KEY"] = "jwt-secret-very-confidential"
    os.environ["CUSTOM_SAFE_VAR"] = "1"

    sanitized = _build_sanitized_env(custom_env={"SAFE_PARAM": "abc", "LEAK_TOKEN": "forbidden"})

    # 敏感变量必须被剥离
    assert "OPENAI_API_KEY" not in sanitized
    assert "DATABASE_URL" not in sanitized
    assert "JWT_SECRET_KEY" not in sanitized
    assert "LEAK_TOKEN" not in sanitized
    assert sanitized.get("SAFE_PARAM") == "abc"


@pytest.mark.asyncio
async def test_safe_harness_no_shell_injection(tmp_path):
    harness = SafeHarness(session_id="test_sec_harness", base_workspace_dir=str(tmp_path))

    # 尝试注入恶意 shell 元字符
    os.environ["SECRET_ENV_FLAG"] = "FLAG{NEVER_LEAK_ME}"

    # 打印环境变量的 python 脚本
    code = "import os; print('ENV_COUNT:', len([k for k in os.environ if 'SECRET' in k]))"
    cmd = [sys.executable, "-c", code]

    res = await harness.execute_command(cmd)
    assert res.exit_code == 0
    assert "ENV_COUNT: 0" in res.stdout

    harness.cleanup_workspace()
