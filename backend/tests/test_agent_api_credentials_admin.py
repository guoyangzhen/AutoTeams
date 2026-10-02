"""管理员机器凭证（Agent API Credentials）管理端点测试。

覆盖：
1. 非企业管理员访问凭证管理端点被拒绝（403）
2. 管理员创建凭证 —— 明文 key 只在创建响应返回一次，列表不回放
3. 创建的密钥可调用机器 API（X-AutoTeams-Agent-Key）
4. 撤销后机器调用立即失效（401）
5. allowed_agent_ids 引用不存在/跨企业 Agent 时被拒绝（400）
"""
import pytest


def _save_cookies(client):
    """保存当前 client 的 Cookie 状态，用于多用户切换。"""
    return [(c.name, c.value, c.domain, c.path) for c in client.cookies.jar]


def _restore_cookies(client, cookies):
    """恢复 client 的 Cookie 状态。"""
    client.cookies.clear()
    for name, value, domain, path in cookies:
        client.cookies.set(name, value, domain=domain or None, path=path or "/")


async def _register(client, email: str, name: str = "测试用户"):
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": name,
        "password": "Test1234!",
    })
    assert resp.status_code == 201, f"注册失败: {resp.text}"


async def _setup_enterprise_admin(client, email: str) -> str:
    """注册用户并创建企业（创建者自动成为该企业管理员），返回 enterprise_id。"""
    await _register(client, email, name="管理员")
    resp = await client.post("/api/v1/enterprises", json={"name": "凭证测试企业"})
    assert resp.status_code == 201, f"创建企业失败: {resp.text}"
    return resp.json()["data"]["id"]


async def _create_credential(client, **overrides):
    payload = {
        "name": "集成测试凭证",
        "scopes": ["agent:read", "agent:chat"],
        "allowed_agent_ids": [],
        "expires_at": None,
    }
    payload.update(overrides)
    return await client.post("/api/v1/agent-api/credentials", json=payload)


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_credentials(client, test_engine):
    """无企业的普通成员不能创建/列出凭证。"""
    await _register(client, "plainmember@test.com")

    resp = await client.get("/api/v1/agent-api/credentials")
    assert resp.status_code == 403

    resp = await _create_credential(client)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_returns_key_once_and_list_masks_it(client):
    """创建响应包含一次性明文 key；列表只显示 prefix，绝不回放完整密钥。"""
    await _setup_enterprise_admin(client, "credadmin@test.com")

    resp = await _create_credential(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    created = body["data"]
    assert created["api_key"].startswith("at_sk_")
    assert "key_hash" not in created
    assert created["key_prefix"].startswith("at_sk_")
    assert created["is_active"] is True
    assert created["revoked_at"] is None

    resp = await client.get("/api/v1/agent-api/credentials")
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert len(items) == 1
    listed = items[0]
    assert listed["id"] == created["id"]
    assert "api_key" not in listed
    assert "key_hash" not in listed
    assert listed["key_prefix"] == created["key_prefix"]


@pytest.mark.asyncio
async def test_created_key_calls_machine_api_and_revocation_kills_access(client):
    """创建的密钥可调用机器 API；撤销后同一密钥立即 401。"""
    await _setup_enterprise_admin(client, "revokeadmin@test.com")

    resp = await _create_credential(client)
    assert resp.status_code == 201
    api_key = resp.json()["data"]["api_key"]
    credential_id = resp.json()["data"]["id"]

    headers = {"X-AutoTeams-Agent-Key": api_key}
    resp = await client.get("/api/v1/agent-api/v1/agents", headers=headers)
    assert resp.status_code == 200, resp.text

    # 管理员撤销
    resp = await client.post(f"/api/v1/agent-api/credentials/{credential_id}/revoke")
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/v1/agent-api/v1/agents", headers=headers)
    assert resp.status_code == 401

    # 列表中凭证已标记撤销
    resp = await client.get("/api/v1/agent-api/credentials")
    items = resp.json()["data"]
    target = next(item for item in items if item["id"] == credential_id)
    assert target["is_active"] is False
    assert target["revoked_at"] is not None


@pytest.mark.asyncio
async def test_pre_rename_credential_and_header_keep_revocation(client, monkeypatch):
    """Existing hashed keys work after renaming, and still obey revocation."""
    from app.config import settings
    from app.utils.branding import LEGACY_AGENT_KEY_PREFIX

    await _setup_enterprise_admin(client, "legacy-credential@test.com")
    with monkeypatch.context() as issued_before_rename:
        issued_before_rename.setattr(settings, "AGENT_API_KEY_PREFIX", LEGACY_AGENT_KEY_PREFIX)
        response = await _create_credential(client)
    assert response.status_code == 201, response.text
    credential = response.json()["data"]
    key = credential["api_key"]
    assert key.startswith(LEGACY_AGENT_KEY_PREFIX)
    endpoint = "/api/v1/agent-api/v1/agents"
    for header in ("X-AutoTeams-Agent-Key", "X-AutoFDE-Agent-Key"):
        assert (await client.get(endpoint, headers={header: key})).status_code == 200
    # Invalid or blank primary headers never fall back to a valid legacy key.
    for invalid in ("at_sk_invalid", "", "   "):
        assert (await client.get(endpoint, headers={
            "X-AutoTeams-Agent-Key": invalid,
            "X-AutoFDE-Agent-Key": key,
        })).status_code == 401
    response = await client.post(f"/api/v1/agent-api/credentials/{credential['id']}/revoke")
    assert response.status_code == 200
    for header in ("X-AutoTeams-Agent-Key", "X-AutoFDE-Agent-Key"):
        assert (await client.get(endpoint, headers={header: key})).status_code == 401


@pytest.mark.asyncio
async def test_create_rejects_unknown_allowed_agent_ids(client):
    """allow-list 引用不存在或跨企业 Agent 时返回 400。"""
    await _setup_enterprise_admin(client, "allowlistadmin@test.com")

    resp = await _create_credential(client, allowed_agent_ids=["ghost-agent"])
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_credential_is_enterprise_scoped(client):
    """A 企业管理员看不到/管不到 B 企业的凭证。"""
    admin_a_cookies = None
    await _setup_enterprise_admin(client, "ent-a@test.com")
    resp = await _create_credential(client, name="A企业凭证")
    assert resp.status_code == 201
    credential_id = resp.json()["data"]["id"]
    admin_a_cookies = _save_cookies(client)

    # 切换到 B 企业管理员
    client.cookies.clear()
    await _setup_enterprise_admin(client, "ent-b@test.com")

    resp = await client.get("/api/v1/agent-api/credentials")
    ids = [item["id"] for item in resp.json()["data"]]
    assert credential_id not in ids

    resp = await client.post(f"/api/v1/agent-api/credentials/{credential_id}/revoke")
    assert resp.status_code == 404

    _restore_cookies(client, admin_a_cookies)
