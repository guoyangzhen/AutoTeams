"""外部 Agent 接入 API 测试（AUD-14：不得把模拟执行当作正式成功）。"""
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.external_agents import (
    ExternalAgentDisconnected,
    ExternalExecutionResult,
    has_executor,
    register_executor,
    unregister_executor,
)
from app.config import DEMO_MODE_ACKNOWLEDGEMENT_VALUE, settings
from app.models.audit_log import AuditLog

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_executors():
    """每个用例结束后清掉注册的执行器，避免跨用例污染。"""
    yield
    for agent_id in ("codex-cli", "claude-code", "mcp-local"):
        unregister_executor(agent_id)


@pytest.mark.asyncio
async def test_list_external_agents_reports_real_capabilities(authenticated_client):
    resp = await authenticated_client.get("/api/v1/external-agents")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 3
    ids = [a["id"] for a in data]
    assert "codex-cli" in ids
    assert "claude-code" in ids
    assert "mcp-local" in ids
    # 未注册执行器时不得声称"已连接"
    for item in data:
        assert item["has_executor"] is False
        assert item["status"] == "detected"


@pytest.mark.asyncio
async def test_dispatch_without_executor_is_unsupported_not_completed(authenticated_client, db_session):
    """AUD-14 复现：向 codex-cli/dispatch 提交任务不再得到 completed 回执。"""
    resp = await authenticated_client.post(
        "/api/v1/external-agents/codex-cli/dispatch",
        json={"prompt": "synthetic no-op task"},
    )
    assert resp.status_code == 501
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "unsupported"
    assert "completed" not in body
    assert "未配置真实执行器" in body["detail"]

    # 未配置执行器时也不能声称连接成功
    conn = await authenticated_client.post("/api/v1/external-agents/codex-cli/connect", json={})
    assert conn.status_code == 409
    assert conn.json()["status"] == "not_configured"

    # 分派失败必须进审计流水
    audits = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "external_agent_dispatch")))
        .scalars()
        .all()
    )
    assert [a.details["outcome"] for a in audits] == ["unsupported"]


@pytest.mark.asyncio
async def test_dispatch_with_real_executor_returns_completed_with_identity(authenticated_client, db_session):
    """completed 只能来自带执行器身份与关联 ID 的真实执行结果。"""
    seen: list[str] = []

    async def executor(req):
        seen.append(req.prompt)
        return ExternalExecutionResult(
            output="真实执行产物：patch.diff",
            executor_id="executor-7",
            correlation_id="corr-abc",
            artifacts=["patch.diff"],
        )

    register_executor("codex-cli", executor)
    assert has_executor("codex-cli") is True

    conn = await authenticated_client.post("/api/v1/external-agents/codex-cli/connect", json={})
    assert conn.status_code == 200
    assert conn.json()["agent"]["has_executor"] is True

    resp = await authenticated_client.post(
        "/api/v1/external-agents/codex-cli/dispatch",
        json={"prompt": "修复订单校验"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["executor_id"] == "executor-7"
    assert body["correlation_id"] == "corr-abc"
    assert body["demo"] is False
    assert body["artifacts"] == ["patch.diff"]
    assert seen == ["修复订单校验"]

    audit = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "external_agent_dispatch")))
        .scalars()
        .first()
    )
    assert audit.details["outcome"] == "completed"
    assert audit.details["executor_id"] == "executor-7"
    assert audit.details["correlation_id"] == "corr-abc"


@pytest.mark.asyncio
async def test_dispatch_rejects_result_without_identity(authenticated_client):
    """没有 executor_id / correlation_id 的结果不得被当作完成。"""
    async def executor(req):
        return ExternalExecutionResult(output="随便一段输出", executor_id="", correlation_id="")

    register_executor("codex-cli", executor)
    resp = await authenticated_client.post(
        "/api/v1/external-agents/codex-cli/dispatch", json={"prompt": "x"}
    )
    assert resp.status_code == 502
    assert resp.json()["status"] == "invalid_execution_result"


@pytest.mark.asyncio
async def test_disconnected_executor_never_succeeds(authenticated_client):
    """执行器断连时不得返回任何成功回执。"""
    async def executor(req):
        raise ExternalAgentDisconnected("codex 进程未运行")

    register_executor("codex-cli", executor)
    resp = await authenticated_client.post(
        "/api/v1/external-agents/codex-cli/dispatch", json={"prompt": "x"}
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "executor_disconnected"
    assert body["success"] is False


@pytest.mark.asyncio
async def test_dispatch_unknown_agent_returns_404(authenticated_client):
    resp = await authenticated_client.post(
        "/api/v1/external-agents/not-exist/dispatch", json={"prompt": "x"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_demo_mode_is_off_by_default_and_marked_when_on(authenticated_client, monkeypatch):
    """演示模式默认关闭；开启后回执与清单元数据都必须标注 DEMO。"""
    assert settings.DEMO_MODE_ENABLED is False
    assert DEMO_MODE_ACKNOWLEDGEMENT_VALUE == "I_UNDERSTAND_DEMO_RESULTS_ARE_NOT_DELIVERY"

    monkeypatch.setattr(settings, "DEMO_MODE_ENABLED", True)

    listed = await authenticated_client.get("/api/v1/external-agents")
    assert all(item["demo"] is True for item in listed.json())

    resp = await authenticated_client.post(
        "/api/v1/external-agents/codex-cli/dispatch", json={"prompt": "演示任务"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["demo"] is True
    assert body["status"] == "demo_simulated"
    assert body["success"] is False
    assert "DEMO" in body["result"]
    assert "completed" not in body


def test_production_refuses_demo_mode_without_acknowledgement():
    """AUD-14：生产环境开启演示模式必须显式确认，否则拒绝启动。"""
    base_env = {
        "PATH": "",
        "PYTHONIOENCODING": "utf-8",
        "DEBUG": "false",
        "JWT_SECRET_KEY": "prod-secret-key-0123456789abcdefghijklmnop",
        "COOKIE_SECURE": "true",
        "CORS_ALLOWED_ORIGINS": "https://autoteams.example.com",
        "REGISTRATION_ENABLED": "false",
        "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/autoteams",
        "BRIDGE_INTERNAL_SECRET": "bridge-secret",
        "METRICS_AUTH_TOKEN": "metrics-token",
        "DEMO_MODE_ENABLED": "true",
    }
    import os

    env = {**os.environ, **base_env}
    env.pop("USE_SQLITE", None)

    result = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode != 0
    assert "DEMO_MODE_ACKNOWLEDGEMENT" in result.stderr

    # 带确认串时可以通过启动检查
    env["DEMO_MODE_ACKNOWLEDGEMENT"] = DEMO_MODE_ACKNOWLEDGEMENT_VALUE
    ok = subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert ok.returncode == 0, ok.stderr
