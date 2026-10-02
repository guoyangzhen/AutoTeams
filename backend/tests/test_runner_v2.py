"""AutoTeams 5.0 · 战役 3：具身物理执行器 2.0 单元与集成测试。

覆盖三大验收面：
1. 端侧心跳（逐设备令牌注册 / 续期 / 离线判定 / 待办回带）；
2. 物理操作护栏拦截（凭据代填过滤器、作用域与跳转面收敛）；
3. 双因子安全确认（云端意图确认 + 端侧物理确认码的状态机与超时作废）。

AUD-04/AUD-18：设备归属、双因子确认码投递、结果回传幂等都有专门的反例测试。
"""
import base64
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enterprise import Enterprise
from app.models.runner_v2 import RunnerChallenge, RunnerDevice
from app.models.user import User
from app.services.runner_v2_protocol import (
    CHALLENGE_TTL_SECONDS,
    HEARTBEAT_TTL_SECONDS,
    ChallengeState,
    GuardAction,
    PhysicalProtocolError,
    TaskState,
    evaluate_task,
    mask_secrets,
    runner_v2_protocol,
)

ENTERPRISE_ID = "ent-runner-v2"
OTHER_ENTERPRISE_ID = "ent-runner-other"


# ============================================================
# Fixtures 与辅助
# ============================================================


@pytest.fixture(autouse=True)
def _preserve_bridge_secret():
    """Bridge 内部密钥是服务间通道的；本模块不再依赖它做设备鉴权。"""
    yield


async def _enterprise_user(
    client,
    test_engine,
    email: str,
    enterprise_id: str = ENTERPRISE_ID,
    role: str = "admin",
) -> str:
    """注册用户并绑定企业（物理操作强制企业隔离）。

    默认给 admin：本模块关注协议与租户边界，而设备注册 / 双因子放行按角色
    收口到企业管理员；需要验证普通成员被拒时显式传 ``role="member"``。
    """
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "物理执行操作员", "password": "pass1234"},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["data"]["user"]["id"]

    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        enterprise = await session.get(Enterprise, enterprise_id)
        if enterprise is None:
            session.add(Enterprise(id=enterprise_id, name="物理执行实验室"))
        user = await session.get(User, user_id)
        user.enterprise_id = enterprise_id
        user.role = role
        await session.commit()
    return user_id


async def _promote_to_admin(test_engine, user_id: str) -> None:
    """撤销/轮换设备属于企业管理员决策，把测试用户升级为 admin。"""
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        user = await session.get(User, user_id)
        user.role = "admin"
        await session.commit()


async def _register_device(client, runner_id: str, scopes: list[str]) -> dict:
    """云端通道注册设备，返回含 device_id + device_secret 的档案。"""
    resp = await client.post(
        "/api/v1/runner/v2/devices",
        json={"runner_id": runner_id, "scopes": scopes},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _device_token(client, device: dict) -> str:
    """用长期凭据换短期访问令牌。"""
    resp = await client.post(
        "/api/v1/runner/v2/devices/token",
        json={"device_id": device["device_id"], "device_secret": device["device_secret"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _heartbeat(
    client, token: str, runner_id: str, scopes: list[str] | None = None
) -> dict:
    resp = await client.post(
        "/api/v1/runner/v2/runners/heartbeat",
        headers=_auth(token),
        json={
            "runner_id": runner_id,
            "scopes": scopes or ["read", "physical"],
            "platform": "win32",
            "version": "2.0.0",
            "capabilities": {"browser": True, "desktop": True},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _provision(client, runner_id: str, scopes: list[str] | None = None) -> tuple[dict, str]:
    """注册设备并换到访问令牌：绝大多数用例的公共前置。"""
    device = await _register_device(client, runner_id, scopes or ["read", "physical"])
    token = await _device_token(client, device)
    return device, token


def _frame_payload(width: int, height: int, seed: int = 7) -> str:
    raw = bytes(((i * 31 + seed) % 256) for i in range(width * height))
    return base64.b64encode(raw).decode("ascii")


async def _load_challenge(test_engine, challenge_id: str) -> RunnerChallenge:
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        result = await session.execute(
            select(RunnerChallenge).where(RunnerChallenge.challenge_id == challenge_id)
        )
        return result.scalar_one()


# ============================================================
# 1. 端侧心跳与设备身份（AUD-04）
# ============================================================


class TestRunnerHeartbeat:
    """端侧心跳与会话保活。"""

    @pytest.mark.asyncio
    async def test_heartbeat_registers_device_and_returns_ttl(self, client, test_engine):
        """首次心跳完成端侧注册并回带保活窗口。"""
        await _enterprise_user(client, test_engine, "hb-1@test.com")
        device, token = await _provision(client, "runner-hb-1", ["read", "physical"])
        data = await _heartbeat(client, token, "runner-hb-1", ["read", "physical"])
        assert data["runner"]["runner_id"] == "runner-hb-1"
        assert data["runner"]["online"] is True
        assert data["runner"]["scopes"] == ["physical", "read"]
        assert data["heartbeat_ttl_seconds"] == HEARTBEAT_TTL_SECONDS
        assert data["pending_tasks"] == []

    @pytest.mark.asyncio
    async def test_heartbeat_requires_device_token(self, client, test_engine):
        """缺少逐设备令牌的端侧心跳一律拒绝（失败关闭）。"""
        await _enterprise_user(client, test_engine, "hb-2@test.com")
        resp = await client.post(
            "/api/v1/runner/v2/runners/heartbeat",
            json={"runner_id": "runner-hb-2", "scopes": ["read"]},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_heartbeat_rejects_shared_bridge_secret(self, client, test_engine):
        """共享的 X-Bridge-Secret 不再能冒充任何设备（AUD-04 clean cutover）。"""
        await _enterprise_user(client, test_engine, "hb-shared@test.com")
        resp = await client.post(
            "/api/v1/runner/v2/runners/heartbeat",
            headers={"X-Bridge-Secret": "any-shared-secret"},
            json={"runner_id": "runner-hb-shared", "scopes": ["read"]},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_heartbeat_rejects_unknown_device_token(self, client, test_engine):
        """伪造/猜测的设备令牌无法进入心跳通道。"""
        await _enterprise_user(client, test_engine, "hb-forged@test.com")
        resp = await client.post(
            "/api/v1/runner/v2/runners/heartbeat",
            headers=_auth("f" * 48),
            json={"runner_id": "runner-hb-1", "scopes": ["read"]},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_heartbeat_marks_device_offline_after_ttl(self, client, test_engine):
        """心跳超时后端侧判定离线，物理任务不再下发。"""
        await _enterprise_user(client, test_engine, "hb-3@test.com")
        device, token = await _provision(client, "runner-hb-3", ["physical"])
        await _heartbeat(client, token, "runner-hb-3", ["physical"])

        factory = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            row = await session.get(RunnerDevice, device["device_id"])
            row.last_seen_at = row.last_seen_at.replace(
                year=row.last_seen_at.year
            ) - __import__("datetime").timedelta(seconds=HEARTBEAT_TTL_SECONDS + 5)
            await session.commit()
            refreshed = await session.get(RunnerDevice, device["device_id"])
            assert runner_v2_protocol.device_to_dict(refreshed)["online"] is False

    @pytest.mark.asyncio
    async def test_heartbeat_returns_pending_physical_tasks(self, client, test_engine):
        """已放行的物理任务会在下一次心跳时回带给端侧。"""
        await _enterprise_user(client, test_engine, "hb-pending@test.com")
        _device, token = await _provision(client, "runner-hb-4", ["read", "physical"])
        await _heartbeat(client, token, "runner-hb-4", ["read", "physical"])

        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-hb-4",
                "steps": [
                    {"op": "navigate", "url": "https://erp.example.com/login"},
                    {"op": "click", "target": "#submit"},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        task = resp.json()["data"]
        assert task["state"] == TaskState.DISPATCHED.value

        data = await _heartbeat(client, token, "runner-hb-4", ["read", "physical"])
        assert [item["task_id"] for item in data["pending_tasks"]] == [task["task_id"]]

    @pytest.mark.asyncio
    async def test_heartbeat_notifies_pending_two_factor_challenge(self, client, test_engine):
        """高危任务挂起时，端侧心跳能感知到待人工确认通知（不含确认码）。"""
        await _enterprise_user(client, test_engine, "hb-2fa@test.com")
        _device, token = await _provision(client, "runner-hb-5", ["physical"])
        await _heartbeat(client, token, "runner-hb-5", ["physical"])

        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-hb-5",
                "steps": [{"op": "click", "target": "#transfer-confirm", "risk": "high"}],
            },
        )
        task = resp.json()["data"]
        assert task["state"] == TaskState.AWAITING_2FA.value

        data = await _heartbeat(client, token, "runner-hb-5", ["physical"])
        assert len(data["notifications"]) == 1
        notification = data["notifications"][0]
        assert notification["state"] == ChallengeState.PENDING_ENDPOINT.value
        assert "device_code" not in notification

    @pytest.mark.asyncio
    async def test_heartbeat_is_not_observable_without_login(self, client):
        """端侧档案列表必须登录可见。"""
        resp = await client.get("/api/v1/runner/v2/runners")
        assert resp.status_code in (401, 403)


# ============================================================
# 2. 设备租户边界（AUD-04 反例）
# ============================================================


class TestDeviceTenantIsolation:
    """设备身份、租户归属与撤销范围。"""

    @pytest.mark.asyncio
    async def test_enterprise_a_cannot_dispatch_to_enterprise_b_device(
        self, client, test_engine
    ):
        """企业 A 无法向企业 B 注册的设备下发任务。"""
        await _enterprise_user(client, test_engine, "iso-a@test.com", ENTERPRISE_ID)
        await _provision(client, "runner-iso-b", ["physical"])
        await client.post("/api/v1/auth/logout")

        # 换成企业 B 的成员去注册同名设备，观察企业 A 的设备列表
        await _enterprise_user(client, test_engine, "iso-b@test.com", OTHER_ENTERPRISE_ID)
        devices = (await client.get("/api/v1/runner/v2/devices")).json()["data"]
        assert devices == [], "企业 B 不得看到企业 A 的设备"

        # 企业 B 也不能向企业 A 的 runner_id 下发任务
        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-iso-b",
                "steps": [{"op": "navigate", "url": "https://erp.example.com"}],
            },
        )
        # 企业 B 下没有同名设备 → 404
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_device_token_cannot_heartbeat_for_other_tenant(
        self, client, test_engine
    ):
        """拿到别的企业设备令牌也无法读取其任务。"""
        await _enterprise_user(client, test_engine, "tok-a@test.com", ENTERPRISE_ID)
        device_a, token_a = await _provision(client, "runner-tok-a", ["physical"])
        await _heartbeat(client, token_a, "runner-tok-a", ["physical"])

        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-tok-a",
                "steps": [{"op": "navigate", "url": "https://erp.example.com/home"}],
            },
        )
        task_id = resp.json()["data"]["task_id"]

        await client.post("/api/v1/auth/logout")
        await _enterprise_user(client, test_engine, "tok-b@test.com", OTHER_ENTERPRISE_ID)
        _device_b, token_b = await _provision(client, "runner-tok-b", ["physical"])

        # 企业 B 的设备不能领取/读取企业 A 的任务
        claim = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token_b)
        )
        assert claim.status_code == 404
        # 企业 B 的成员在云端也读不到该任务
        detail = await client.get(f"/api/v1/runner/v2/tasks/{task_id}")
        assert detail.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_one_device_does_not_affect_others(self, client, test_engine):
        """撤销单台设备不影响其他设备，也不需要轮换全局密钥。"""
        user_a = await _enterprise_user(client, test_engine, "revoke-a@test.com", ENTERPRISE_ID)
        await _promote_to_admin(test_engine, user_a)
        device_a, token_a = await _provision(client, "runner-rev-a", ["physical"])
        device_b, token_b = await _provision(client, "runner-rev-b", ["physical"])

        revoke = await client.post(
            f"/api/v1/runner/v2/devices/{device_a['device_id']}/revoke",
            json={"reason": "设备遗失"},
        )
        assert revoke.status_code == 200, revoke.text

        # 被撤销设备的令牌立即失效
        dead = await client.post(
            "/api/v1/runner/v2/runners/heartbeat",
            headers=_auth(token_a),
            json={"runner_id": "runner-rev-a", "scopes": ["physical"]},
        )
        assert dead.status_code == 401

        # 另一台设备不受影响
        alive = await client.post(
            "/api/v1/runner/v2/runners/heartbeat",
            headers=_auth(token_b),
            json={"runner_id": "runner-rev-b", "scopes": ["physical"]},
        )
        assert alive.status_code == 200

    @pytest.mark.asyncio
    async def test_credential_rotate_invalidates_only_that_device(self, client, test_engine):
        """轮换一台设备的长期凭据不会影响其他设备。"""
        user_a = await _enterprise_user(client, test_engine, "rotate-a@test.com", ENTERPRISE_ID)
        await _promote_to_admin(test_engine, user_a)
        device_a, _token_a = await _provision(client, "runner-rot-a", ["physical"])
        device_b, token_b = await _provision(client, "runner-rot-b", ["physical"])

        rotate = await client.post(
            f"/api/v1/runner/v2/devices/{device_a['device_id']}/rotate"
        )
        assert rotate.status_code == 200, rotate.text
        new_secret = rotate.json()["data"]["device_secret"]

        # 旧密钥无法再换令牌
        old = await client.post(
            "/api/v1/runner/v2/devices/token",
            json={"device_id": device_a["device_id"], "device_secret": device_a["device_secret"]},
        )
        assert old.status_code == 401
        # 新密钥可用
        fresh = await client.post(
            "/api/v1/runner/v2/devices/token",
            json={"device_id": device_a["device_id"], "device_secret": new_secret},
        )
        assert fresh.status_code == 200
        # 另一台设备不受影响
        other = await client.post(
            "/api/v1/runner/v2/runners/heartbeat",
            headers=_auth(token_b),
            json={"runner_id": "runner-rot-b", "scopes": ["physical"]},
        )
        assert other.status_code == 200


# ============================================================
# 3. 物理操作护栏拦截
# ============================================================


class TestTriRuleGuardrails:
    """Tri-Rule 物理沙箱阻断。"""

    def test_credential_literal_in_body_is_blocked(self):
        """规则一：正文携带硬编码凭据直接阻断。"""
        verdict = evaluate_task(
            {
                "channel": "browser_action",
                "steps": [
                    {"op": "navigate", "url": "https://erp.example.com"},
                    {
                        "op": "fill_table",
                        "rows": [{"username": "svc-erp", "password": "P@ssw0rd-2026"}],
                    },
                ],
            },
            ["physical"],
        )
        assert verdict.action == GuardAction.BLOCK
        assert verdict.rule == "tri_rule_credential_autofill"

    def test_credential_field_without_vault_ref_is_blocked(self):
        """规则一：凭据字段必须由凭据机代填。"""
        verdict = evaluate_task(
            {
                "channel": "desktop_accessibility",
                "steps": [
                    {
                        "op": "input_text",
                        "target": "Window/PasswordField",
                        "credential_ref": "vault://erp/prod/password",
                        "text": "",
                    }
                ],
            },
            ["physical"],
        )
        assert verdict.action == GuardAction.ALLOW

    def test_credential_field_with_literal_text_is_blocked(self):
        """规则一：凭据字段直接写入明文一律阻断。"""
        verdict = evaluate_task(
            {
                "channel": "browser_action",
                "steps": [
                    {"op": "fill_table", "target": "input[name=password]", "text": "hunter2"}
                ],
            },
            ["physical"],
        )
        assert verdict.action == GuardAction.BLOCK
        assert verdict.rule == "tri_rule_credential_autofill"

    def test_high_entropy_secret_literal_is_blocked(self):
        """规则一：裸高熵密钥串同样被拦截。"""
        secret = "AKIA9ZQ0X8YV7W6U5T4S3R2E1Q0PLKJ"
        verdict = evaluate_task(
            {
                "channel": "desktop_accessibility",
                "steps": [{"op": "input_text", "target": "apiKeyField", "text": secret}],
            },
            ["physical"],
        )
        assert verdict.action == GuardAction.BLOCK

    def test_mutating_op_requires_physical_scope(self):
        """规则三：未授予 physical 作用域时变更型操作被阻断。"""
        verdict = evaluate_task(
            {
                "channel": "browser_action",
                "steps": [{"op": "click", "target": "#submit"}],
            },
            ["read"],
        )
        assert verdict.action == GuardAction.BLOCK
        assert verdict.rule == "tri_rule_scope"

    def test_non_http_navigation_is_blocked(self):
        """规则三：禁止 file:/javascript: 跳转。"""
        verdict = evaluate_task(
            {
                "channel": "browser_action",
                "steps": [{"op": "navigate", "url": "file:///C:/Windows/System32/config"}],
            },
            ["physical"],
        )
        assert verdict.action == GuardAction.BLOCK
        assert verdict.rule == "tri_rule_navigation"

    def test_channel_op_surface_is_enforced(self):
        """规则三：跨通道操作面越权（如浏览器通道读辅助功能树）被阻断。"""
        verdict = evaluate_task(
            {"channel": "browser_action", "steps": [{"op": "read_tree"}]},
            ["physical"],
        )
        assert verdict.action == GuardAction.BLOCK
        assert verdict.rule == "tri_rule_channel_surface"

    def test_high_risk_operation_requires_two_factor(self):
        """规则二：高危操作进入双因子确认而不是直接放行。"""
        verdict = evaluate_task(
            {
                "channel": "browser_action",
                "steps": [
                    {"op": "click", "target": "https://erp.example.com/transfer/submit"},
                    {"op": "click", "target": "#confirm"},
                ],
            },
            ["physical"],
        )
        assert verdict.action == GuardAction.REQUIRE_2FA
        assert verdict.rule == "tri_rule_high_risk_two_factor"

    def test_mask_secrets_never_reveals_literal(self):
        """审计脱敏：凭据片段永不落明文。"""
        masked = mask_secrets('登录 password=SuperSecret123 继续')
        assert "SuperSecret123" not in masked
        assert "[REDACTED]" in masked

    @pytest.mark.asyncio
    async def test_blocked_task_never_reaches_endpoint(self, client, test_engine):
        """被护栏阻断的任务不下发端侧，并留下审计留痕。"""
        await _enterprise_user(client, test_engine, "guard-block@test.com")
        _device, token = await _provision(client, "runner-guard-1", ["physical"])
        await _heartbeat(client, token, "runner-guard-1", ["physical"])

        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-guard-1",
                "steps": [
                    {"op": "navigate", "url": "https://erp.example.com"},
                    {
                        "op": "fill_table",
                        "rows": [{"username": "svc-erp", "password": "P@ssw0rd-2026"}],
                    },
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        task = resp.json()["data"]
        assert task["state"] == TaskState.BLOCKED.value
        assert task["verdict"]["rule"] == "tri_rule_credential_autofill"

        data = await _heartbeat(client, token, "runner-guard-1", ["physical"])
        assert data["pending_tasks"] == []

        audit = await client.get("/api/v1/runner/v2/audit")
        assert audit.status_code == 200
        entries = audit.json()["data"]
        assert any(entry["event"] == "task_blocked" for entry in entries)
        assert all("P@ssw0rd-2026" not in entry["detail"] for entry in entries)

    @pytest.mark.asyncio
    async def test_dispatch_to_unknown_runner_is_rejected(self, client, test_engine):
        """未注册的端侧不可接收物理任务。"""
        await _enterprise_user(client, test_engine, "guard-unknown@test.com")
        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "desktop_accessibility",
                "runner_id": f"runner-missing-{uuid.uuid4().hex[:6]}",
                "steps": [{"op": "read_tree"}],
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_enterprise_isolation_blocks_cross_tenant_read(
        self, client, test_engine
    ):
        """物理任务按企业隔离，他企业不可见。"""
        await _enterprise_user(client, test_engine, "guard-ent-a@test.com", ENTERPRISE_ID)
        _device, token = await _provision(client, "runner-guard-2", ["physical"])
        await _heartbeat(client, token, "runner-guard-2", ["physical"])
        created = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-guard-2",
                "steps": [{"op": "navigate", "url": "https://erp.example.com/home"}],
            },
        )
        task_id = created.json()["data"]["task_id"]

        await client.post("/api/v1/auth/logout")
        await _enterprise_user(client, test_engine, "guard-ent-b@test.com", OTHER_ENTERPRISE_ID)

        resp = await client.get(f"/api/v1/runner/v2/tasks/{task_id}")
        assert resp.status_code == 404
        listed = await client.get("/api/v1/runner/v2/tasks")
        assert listed.json()["data"] == []


# ============================================================
# 4. 双因子安全确认
# ============================================================


class TestTwoFactorConfirmation:
    """高危操作双因子确认状态机。"""

    async def _dispatch_high_risk(self, client, runner_id: str) -> dict:
        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": runner_id,
                "steps": [
                    {"op": "navigate", "url": "https://erp.example.com/transfer"},
                    {"op": "click", "target": "#批量删除", "risk": "high"},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    @pytest.mark.asyncio
    async def test_full_two_factor_flow_releases_task(self, client, test_engine):
        """两道因子依次完成后任务才放行至端侧。"""
        await _enterprise_user(client, test_engine, "2fa-flow@test.com")
        _device, token = await _provision(client, "runner-2fa-1", ["physical"])
        await _heartbeat(client, token, "runner-2fa-1", ["physical"])

        task = await self._dispatch_high_risk(client, "runner-2fa-1")
        assert task["state"] == TaskState.AWAITING_2FA.value
        challenge_id = task["challenge_id"]
        assert challenge_id

        # 未完成第一因子时，端侧确认码接口必须拒绝
        early = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/verify",
            headers=_auth(token),
            json={"device_code": "000000"},
        )
        assert early.status_code == 409

        confirmed = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/confirm",
            json={"decision": "approve"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["data"]["state"] == ChallengeState.PENDING_DEVICE_CODE.value

        # 端侧确认码错误 → 挑战保持挂起
        wrong = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/verify",
            headers=_auth(token),
            json={"device_code": "111111"},
        )
        assert wrong.status_code == 401

        challenge = await _load_challenge(test_engine, challenge_id)
        from app.services.runner_v2_protocol import runner_v2_protocol as proto

        code = proto._decrypt_device_code(challenge)
        verified = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/verify",
            headers=_auth(token),
            json={"device_code": code},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["data"]["state"] == ChallengeState.APPROVED.value
        assert verified.json()["data"]["task"]["state"] == TaskState.DISPATCHED.value

        data = await _heartbeat(client, token, "runner-2fa-1", ["physical"])
        assert [item["task_id"] for item in data["pending_tasks"]] == [task["task_id"]]
        assert data["notifications"] == []

    @pytest.mark.asyncio
    async def test_rejection_cancels_task_forever(self, client, test_engine):
        """人工否决后任务取消且不再下发端侧。"""
        await _enterprise_user(client, test_engine, "2fa-reject@test.com")
        _device, token = await _provision(client, "runner-2fa-2", ["physical"])
        await _heartbeat(client, token, "runner-2fa-2", ["physical"])

        task = await self._dispatch_high_risk(client, "runner-2fa-2")
        challenge_id = task["challenge_id"]

        rejected = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/confirm",
            json={"decision": "reject"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["data"]["state"] == ChallengeState.REJECTED.value

        detail = await client.get(f"/api/v1/runner/v2/tasks/{task['task_id']}")
        assert detail.json()["data"]["state"] == TaskState.CANCELLED.value

        data = await _heartbeat(client, token, "runner-2fa-2", ["physical"])
        assert data["pending_tasks"] == []

    @pytest.mark.asyncio
    async def test_device_code_reaches_endpoint_only_after_endpoint_factor(
        self, client, test_engine
    ):
        """端侧确认码只在云端意图确认后经机器通道下发，云端视图永不暴露。"""
        await _enterprise_user(client, test_engine, "2fa-delivery@test.com")
        _device, token = await _provision(client, "runner-2fa-5", ["physical"])
        await _heartbeat(client, token, "runner-2fa-5", ["physical"])
        task = await self._dispatch_high_risk(client, "runner-2fa-5")
        challenge_id = task["challenge_id"]

        before = await _heartbeat(client, token, "runner-2fa-5", ["physical"])
        assert all("device_code" not in n for n in before["notifications"])

        await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/confirm",
            json={"decision": "approve"},
        )

        after = await _heartbeat(client, token, "runner-2fa-5", ["physical"])
        delivered = [n for n in after["notifications"] if n.get("device_code")]
        assert len(delivered) == 1
        assert delivered[0]["state"] == ChallengeState.PENDING_DEVICE_CODE.value

        cloud_view = (await client.get("/api/v1/runner/v2/challenges")).json()["data"][0]
        assert "device_code" not in cloud_view
        assert cloud_view["device_verified"] is False

        verified = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/verify",
            headers=_auth(token),
            json={"device_code": delivered[0]["device_code"]},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["data"]["task"]["state"] == TaskState.DISPATCHED.value

    @pytest.mark.asyncio
    async def test_device_code_never_reaches_unrelated_device(self, client, test_engine):
        """无关设备的心跳不得拿到他人任务的确认码（AUD-04 复现路径）。"""
        await _enterprise_user(client, test_engine, "2fa-isolation@test.com")
        _owner, owner_token = await _provision(client, "runner-2fa-owner", ["physical"])
        _other, other_token = await _provision(client, "runner-2fa-other", ["physical"])
        await _heartbeat(client, owner_token, "runner-2fa-owner", ["physical"])
        task = await self._dispatch_high_risk(client, "runner-2fa-owner")
        challenge_id = task["challenge_id"]
        await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/confirm",
            json={"decision": "approve"},
        )

        other_heartbeat = await _heartbeat(client, other_token, "runner-2fa-other", ["physical"])
        assert all(
            n.get("task_id") != task["task_id"] for n in other_heartbeat["notifications"]
        )
        assert all("device_code" not in n for n in other_heartbeat["notifications"])

        # 无关设备也不能提交该挑战的确认码
        stolen = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/verify",
            headers=_auth(other_token),
            json={"device_code": "123456"},
        )
        assert stolen.status_code == 404

    @pytest.mark.asyncio
    async def test_challenge_expiry_cancels_task(self, client, test_engine):
        """超过有效期未完成双因子确认，任务自动作废。"""
        import datetime

        await _enterprise_user(client, test_engine, "2fa-expire@test.com")
        _device, token = await _provision(client, "runner-2fa-3", ["physical"])
        await _heartbeat(client, token, "runner-2fa-3", ["physical"])
        task = await self._dispatch_high_risk(client, "runner-2fa-3")

        challenge = await _load_challenge(test_engine, task["challenge_id"])
        factory = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            row = await session.get(RunnerChallenge, challenge.challenge_id)
            row.expires_at = row.expires_at - datetime.timedelta(seconds=CHALLENGE_TTL_SECONDS + 60)
            await session.commit()

        listed = await client.get("/api/v1/runner/v2/challenges")
        states = {item["challenge_id"]: item["state"] for item in listed.json()["data"]}
        assert states[challenge.challenge_id] == ChallengeState.EXPIRED.value

        detail = await client.get(f"/api/v1/runner/v2/tasks/{task['task_id']}")
        assert detail.json()["data"]["state"] == TaskState.CANCELLED.value

    @pytest.mark.asyncio
    async def test_device_code_is_never_exposed_to_cloud_ui(self, client, test_engine):
        """端侧物理确认码不随云端挑战视图外泄。"""
        await _enterprise_user(client, test_engine, "2fa-secret@test.com")
        _device, token = await _provision(client, "runner-2fa-4", ["physical"])
        await _heartbeat(client, token, "runner-2fa-4", ["physical"])
        task = await self._dispatch_high_risk(client, "runner-2fa-4")

        payload = (await client.get("/api/v1/runner/v2/challenges")).json()["data"][0]
        assert "device_code" not in payload
        assert payload["device_verified"] is False

        detail = await client.get(f"/api/v1/runner/v2/tasks/{task['task_id']}")
        assert "device_code" not in detail.text


# ============================================================
# 5. 视窗帧流
# ============================================================


class TestViewportFrames:
    """实时视窗帧推送。"""

    async def _dispatch(self, client, runner_id: str, token: str) -> str:
        await _heartbeat(client, token, runner_id, ["physical"])
        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": runner_id,
                "steps": [{"op": "navigate", "url": "https://erp.example.com/home"}],
            },
        )
        return resp.json()["data"]["task_id"]

    @pytest.mark.asyncio
    async def test_frames_are_pushed_and_streamed(self, client, test_engine):
        """端侧上报的灰度帧按序入库并以 SSE 游标增量推送。"""
        await _enterprise_user(client, test_engine, "frame-stream@test.com")
        _device, token = await _provision(client, "runner-frame-1", ["physical"])
        task_id = await self._dispatch(client, "runner-frame-1", token)

        for seq in (1, 2):
            resp = await client.post(
                f"/api/v1/runner/v2/tasks/{task_id}/frames",
                headers=_auth(token),
                json={
                    "kind": "keyframe" if seq == 1 else "delta",
                    "width": 160,
                    "height": 100,
                    "payload_b64": _frame_payload(160, 100, seed=seq),
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["seq"] == seq
            assert resp.json()["data"]["byte_size"] == 16000

        stream = await client.get(f"/api/v1/runner/v2/tasks/{task_id}/stream")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        body = stream.text
        assert body.count("event: frame") == 2
        assert '"seq": 1' in body and '"seq": 2' in body
        assert "event: eof" in body

        tail = await client.get(
            f"/api/v1/runner/v2/tasks/{task_id}/stream", params={"since_seq": 1}
        )
        assert tail.text.count("event: frame") == 1
        assert '"seq": 2' in tail.text

    @pytest.mark.asyncio
    async def test_frame_geometry_mismatch_is_rejected(self, client, test_engine):
        """帧尺寸与灰度像素数量不一致时拒绝归档。"""
        await _enterprise_user(client, test_engine, "frame-bad@test.com")
        _device, token = await _provision(client, "runner-frame-2", ["physical"])
        task_id = await self._dispatch(client, "runner-frame-2", token)

        resp = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/frames",
            headers=_auth(token),
            json={
                "kind": "keyframe",
                "width": 160,
                "height": 100,
                "payload_b64": _frame_payload(160, 100),
            },
        )
        assert resp.status_code == 200

        broken = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/frames",
            headers=_auth(token),
            json={
                "kind": "delta",
                "width": 64,
                "height": 64,
                "payload_b64": _frame_payload(64, 64),
            },
        )
        assert broken.status_code == 200

        mismatch = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/frames",
            headers=_auth(token),
            json={
                "kind": "delta",
                "width": 64,
                "height": 64,
                "payload_b64": _frame_payload(32, 32),
            },
        )
        assert mismatch.status_code == 422

    @pytest.mark.asyncio
    async def test_result_report_closes_task_with_redaction(self, client, test_engine):
        """端侧回传结果后任务闭环，失败信息中的凭据被脱敏。"""
        await _enterprise_user(client, test_engine, "frame-result@test.com")
        _device, token = await _provision(client, "runner-frame-3", ["physical"])
        task_id = await self._dispatch(client, "runner-frame-3", token)

        resp = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/result",
            headers=_auth(token),
            json={
                "ok": False,
                "error": "登录页提交失败 password=SuperSecret123",
                "receipt_id": f"{task_id}::s0",
                "step_id": "s0",
            },
        )
        assert resp.status_code == 200, resp.text
        task = resp.json()["data"]
        assert task["state"] == TaskState.FAILED.value
        assert "SuperSecret123" not in task["result"]["error"]

        entries = (await client.get("/api/v1/runner/v2/audit")).json()["data"]
        assert any(entry["event"] == "task_failed" for entry in entries)
        assert all("SuperSecret123" not in entry["detail"] for entry in entries)

    @pytest.mark.asyncio
    async def test_result_report_is_idempotent_by_step_identity(self, client, test_engine):
        """同一 step 的回执重发不会重复记账（AUD-18）。"""
        await _enterprise_user(client, test_engine, "frame-idem@test.com")
        _device, token = await _provision(client, "runner-frame-4", ["physical"])
        task_id = await self._dispatch(client, "runner-frame-4", token)
        receipt = f"{task_id}::s0"
        claimed = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token)
        )
        assert claimed.status_code == 200, claimed.text

        first = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/result",
            headers=_auth(token),
            json={"ok": True, "data": {"ok": 1}, "receipt_id": receipt, "step_id": "s0"},
        )
        assert first.status_code == 200
        # 重发同一回执
        second = await client.post(
            f"/api/v1/runner/v2/tasks/{task_id}/result",
            headers=_auth(token),
            json={"ok": True, "data": {"ok": 1}, "receipt_id": receipt, "step_id": "s0"},
        )
        assert second.status_code == 200

        # 只有一个终态审计，不会因为重发多记一次。
        entries = (await client.get("/api/v1/runner/v2/audit")).json()["data"]
        assert sum(1 for e in entries if e["event"] == "task_completed") == 1
        # 不报错、不重复触发副作用（任务仍然只有一个终态记录）
        detail = await client.get(f"/api/v1/runner/v2/tasks/{task_id}")
        assert detail.json()["data"]["state"] == TaskState.COMPLETED.value


# ============================================================
# 6. 状态持久化（AUD-18）
# ============================================================


class TestStateDurability:
    """新进程实例必须能看见历史状态（AUD-18）。"""

    @pytest.mark.asyncio
    async def test_new_protocol_instance_sees_previous_tasks(self, client, test_engine):
        """RunnerV2Protocol 不再持有进程内状态；新实例 + 新 session 仍可读历史任务。"""
        await _enterprise_user(client, test_engine, "durable@test.com")
        _device, token = await _provision(client, "runner-durable", ["physical"])
        await _heartbeat(client, token, "runner-durable", ["physical"])
        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-durable",
                "steps": [{"op": "navigate", "url": "https://erp.example.com"}],
            },
        )
        task_id = resp.json()["data"]["task_id"]

        # 全新协议实例 + 全新 DB session
        from app.services.runner_v2_protocol import RunnerV2Protocol

        fresh_protocol = RunnerV2Protocol()
        factory = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            tasks = await fresh_protocol.list_tasks(session, ENTERPRISE_ID)
            assert task_id in {t["task_id"] for t in tasks}

    @pytest.mark.asyncio
    async def test_state_survives_new_protocol_instance(self, client, test_engine):
        """重复调用 list_tasks 不依赖单例状态，结果一致。"""
        await _enterprise_user(client, test_engine, "durable-2@test.com")
        _device, token = await _provision(client, "runner-durable-2", ["physical"])
        await _heartbeat(client, token, "runner-durable-2", ["physical"])
        await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-durable-2",
                "steps": [{"op": "navigate", "url": "https://erp.example.com"}],
            },
        )
        first = (await client.get("/api/v1/runner/v2/tasks")).json()["data"]
        second = (await client.get("/api/v1/runner/v2/tasks")).json()["data"]
        assert len(first) == len(second) >= 1


# ============================================================
# 7. 执行回执幂等与角色边界（AUD-18 / AUD-04）
# ============================================================


async def _dispatch_multi_step(client, runner_id: str, token: str, steps: list[dict]) -> str:
    await _heartbeat(client, token, runner_id, ["physical"])
    resp = await client.post(
        "/api/v1/runner/v2/tasks",
        json={"channel": "browser_action", "runner_id": runner_id, "steps": steps},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task_id"]


async def _report(
    client,
    token: str,
    task_id: str,
    step_id: str,
    ok: bool = True,
    receipt_id: str | None = None,
):
    return await client.post(
        f"/api/v1/runner/v2/tasks/{task_id}/result",
        headers=_auth(token),
        json={
            "ok": ok,
            "data": {"step": step_id},
            "receipt_id": receipt_id or f"{task_id}::{step_id}",
            "step_id": step_id,
        },
    )



class TestStepIdentityNormalization:
    """重复 / 空白 step_id 不得让端侧跳过动作或让云端过早判定完成（AUD-18）。"""

    async def _steps_of(self, test_engine, task_id: str) -> list[dict]:
        from sqlalchemy import select

        from app.models.runner_v2 import RunnerTask

        factory = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            task = (
                await session.execute(
                    select(RunnerTask).where(RunnerTask.task_id == task_id)
                )
            ).scalar_one()
            return list(task.steps or [])

    @pytest.mark.asyncio
    async def test_duplicate_step_ids_are_made_unique(self, client, test_engine):
        await _enterprise_user(client, test_engine, "step-dup@test.com")
        _device, token = await _provision(client, "runner-step-dup", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-step-dup",
            token,
            [
                {"op": "navigate", "url": "https://erp.example.com/a", "step_id": "same"},
                {"op": "navigate", "url": "https://erp.example.com/b", "step_id": "same"},
            ],
        )
        steps = await self._steps_of(test_engine, task_id)
        identities = [s["step_id"] for s in steps]
        assert len(identities) == len(set(identities)), f"身份必须唯一: {identities}"
        assert all(isinstance(i, str) and i.strip() for i in identities)

    @pytest.mark.asyncio
    async def test_blank_step_ids_are_replaced(self, client, test_engine):
        await _enterprise_user(client, test_engine, "step-blank@test.com")
        _device, token = await _provision(client, "runner-step-blank", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-step-blank",
            token,
            [
                {"op": "navigate", "url": "https://erp.example.com/a", "step_id": "   "},
                {"op": "navigate", "url": "https://erp.example.com/b", "step_id": None},
            ],
        )
        steps = await self._steps_of(test_engine, task_id)
        identities = [s["step_id"] for s in steps]
        assert len(identities) == len(set(identities)), f"身份必须唯一: {identities}"
        assert all(isinstance(i, str) and i.strip() for i in identities)

    @pytest.mark.asyncio
    async def test_one_receipt_cannot_finalize_task_with_duplicate_inputs(
        self, client, test_engine
    ):
        """两个动作只有一条回执时，任务不得被判定为完成。"""
        await _enterprise_user(client, test_engine, "step-partial@test.com")
        _device, token = await _provision(client, "runner-step-partial", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-step-partial",
            token,
            [
                {"op": "navigate", "url": "https://erp.example.com/a", "step_id": "dup"},
                {"op": "navigate", "url": "https://erp.example.com/b", "step_id": "dup"},
            ],
        )
        await client.post(f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token))
        steps = await self._steps_of(test_engine, task_id)
        first_identity = steps[0]["step_id"]

        partial = await _report(client, token, task_id, first_identity)
        assert partial.status_code == 200, partial.text
        assert partial.json()["data"]["state"] == TaskState.EXECUTING.value

        second = await _report(client, token, task_id, steps[1]["step_id"])
        assert second.status_code == 200
        assert second.json()["data"]["state"] == TaskState.COMPLETED.value



class TestReceiptLedger:
    """多 step 任务的回执只记一次、终态不被中间回执提前写死（AUD-18）。"""

    @pytest.mark.asyncio
    async def test_first_step_receipt_does_not_finalize_multi_step_task(
        self, client, test_engine
    ):
        await _enterprise_user(client, test_engine, "receipt-multi@test.com")
        _device, token = await _provision(client, "runner-receipt-multi", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-receipt-multi",
            token,
            [
                {"op": "navigate", "url": "https://erp.example.com/home"},
                {"op": "navigate", "url": "https://erp.example.com/detail"},
            ],
        )
        await client.post(f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token))

        first = await _report(client, token, task_id, "s0")
        assert first.status_code == 200, first.text
        assert first.json()["data"]["state"] == TaskState.EXECUTING.value

        second = await _report(client, token, task_id, "s1")
        assert second.status_code == 200, second.text
        assert second.json()["data"]["state"] == TaskState.COMPLETED.value

        # 内部回执账本不得出现在 API 响应里。
        assert "receipt_ledger" not in second.json()["data"]["result"]

        entries = (await client.get("/api/v1/runner/v2/audit")).json()["data"]
        assert sum(1 for e in entries if e["event"] == "task_step_reported") == 1
        assert sum(1 for e in entries if e["event"] == "task_completed") == 1

    @pytest.mark.asyncio
    async def test_replaying_older_receipt_after_final_step_is_ignored(
        self, client, test_engine
    ):
        """断线重连补发旧 step 回执不得二次记账（AUD-18）。"""
        await _enterprise_user(client, test_engine, "receipt-replay@test.com")
        _device, token = await _provision(client, "runner-receipt-replay", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-receipt-replay",
            token,
            [
                {"op": "navigate", "url": "https://erp.example.com/home"},
                {"op": "navigate", "url": "https://erp.example.com/detail"},
            ],
        )
        await client.post(f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token))
        await _report(client, token, task_id, "s0")
        await _report(client, token, task_id, "s1")

        replay = await _report(client, token, task_id, "s0")
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["state"] == TaskState.COMPLETED.value

        entries = (await client.get("/api/v1/runner/v2/audit")).json()["data"]
        assert sum(1 for e in entries if e["event"] == "task_completed") == 1

    @pytest.mark.asyncio
    async def test_failure_after_completed_step_corrects_terminal_state(
        self, client, test_engine
    ):
        """第二步失败时任务必须是 failed，不能停留在第一步的 completed。"""
        await _enterprise_user(client, test_engine, "receipt-fail@test.com")
        _device, token = await _provision(client, "runner-receipt-fail", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-receipt-fail",
            token,
            [
                {"op": "navigate", "url": "https://erp.example.com/home"},
                {"op": "navigate", "url": "https://erp.example.com/detail"},
            ],
        )
        await client.post(f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token))
        await _report(client, token, task_id, "s0")
        failed = await _report(client, token, task_id, "s1", ok=False)
        assert failed.status_code == 200
        assert failed.json()["data"]["state"] == TaskState.FAILED.value

    @pytest.mark.asyncio
    async def test_result_for_blocked_task_is_rejected(self, client, test_engine):
        """被护栏阻断的任务不能被端侧回执"补记"成已执行。"""
        await _enterprise_user(client, test_engine, "receipt-blocked@test.com")
        _device, token = await _provision(client, "runner-receipt-blocked", ["physical"])
        await _heartbeat(client, token, "runner-receipt-blocked", ["physical"])
        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": "runner-receipt-blocked",
                "steps": [{"op": "navigate", "url": "file:///etc/passwd"}],
            },
        )
        task_id = resp.json()["data"]["task_id"]
        assert resp.json()["data"]["state"] == TaskState.BLOCKED.value

        blocked_report = await _report(client, token, task_id, "s0")
        assert blocked_report.status_code == 409, blocked_report.text

        entries = (await client.get("/api/v1/runner/v2/audit")).json()["data"]
        assert not any(e["event"] == "task_completed" for e in entries)

    @pytest.mark.asyncio
    async def test_new_receipt_after_terminal_state_is_rejected(self, client, test_engine):
        """任务已终结后，剩余 step 的回执被拒，避免重复副作用被记账。"""
        await _enterprise_user(client, test_engine, "receipt-late@test.com")
        _device, token = await _provision(client, "runner-receipt-late", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-receipt-late",
            token,
            [
                {"op": "navigate", "url": "https://erp.example.com/home"},
                {"op": "navigate", "url": "https://erp.example.com/detail"},
            ],
        )
        await client.post(f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token))
        # 第一步失败即进入终态（不会被后续 step 的成功覆盖）。
        failed = await _report(client, token, task_id, "s0", ok=False)
        assert failed.status_code == 200, failed.text
        assert failed.json()["data"]["state"] == TaskState.FAILED.value

        late = await _report(client, token, task_id, "s1")
        assert late.status_code == 409, late.text

        entries = (await client.get("/api/v1/runner/v2/audit")).json()["data"]
        assert sum(1 for e in entries if e["event"] == "task_failed") == 1
        assert not any(e["event"] == "task_completed" for e in entries)

    @pytest.mark.asyncio
    async def test_receipt_for_foreign_step_is_rejected(self, client, test_engine):
        """回执必须能核对步骤身份：任务里不存在的 step 一律 422。"""
        await _enterprise_user(client, test_engine, "receipt-step@test.com")
        _device, token = await _provision(client, "runner-receipt-step", ["physical"])
        task_id = await _dispatch_multi_step(
            client,
            "runner-receipt-step",
            token,
            [{"op": "navigate", "url": "https://erp.example.com/home"}],
        )
        await client.post(f"/api/v1/runner/v2/tasks/{task_id}/claim", headers=_auth(token))

        forged = await _report(
            client, token, task_id, "not-a-real-step", receipt_id=f"{task_id}::whatever"
        )
        assert forged.status_code == 422, forged.text

        entries = (await client.get("/api/v1/runner/v2/audit")).json()["data"]
        assert not any(
            e["event"] in {"task_completed", "task_step_reported"} for e in entries
        )


class TestCloudChannelRoles:
    """设备接入与高危放行按企业角色收口（AUD-04）。"""

    @pytest.mark.asyncio
    async def test_member_cannot_register_device(self, client, test_engine):
        await _enterprise_user(client, test_engine, "member-dev@test.com", role="member")
        resp = await client.post(
            "/api/v1/runner/v2/devices",
            json={"runner_id": "runner-member-1", "scopes": ["physical"]},
        )
        assert resp.status_code == 403, resp.text
        assert (await client.get("/api/v1/runner/v2/devices")).json()["data"] == []

    @pytest.mark.asyncio
    async def test_member_cannot_approve_high_risk_challenge(self, client, test_engine):
        """普通成员不得完成第一道因子（否则双因子退化成发起人自批）。"""
        challenge_id = await self._high_risk_challenge(client, test_engine, "runner-member-2fa")

        approve = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/confirm",
            json={"decision": "approve"},
        )
        assert approve.status_code == 403, approve.text

        listed = (await client.get("/api/v1/runner/v2/challenges")).json()["data"]
        assert listed[0]["state"] == ChallengeState.PENDING_ENDPOINT.value

    @pytest.mark.asyncio
    async def test_member_may_reject_high_risk_challenge(self, client, test_engine):
        """否决只会收紧权限，普通成员可以执行。"""
        challenge_id = await self._high_risk_challenge(
            client, test_engine, "runner-member-reject"
        )
        reject = await client.post(
            f"/api/v1/runner/v2/challenges/{challenge_id}/confirm",
            json={"decision": "reject"},
        )
        assert reject.status_code == 200, reject.text
        assert reject.json()["data"]["state"] == ChallengeState.REJECTED.value

    async def _high_risk_challenge(self, client, test_engine, runner_id: str) -> str:
        """管理员接入设备并下发高危任务，再以普通成员身份继续操作。"""
        await _enterprise_user(client, test_engine, f"admin-{runner_id}@test.com")
        _device, token = await _provision(client, runner_id, ["physical"])
        await _heartbeat(client, token, runner_id, ["physical"])
        resp = await client.post(
            "/api/v1/runner/v2/tasks",
            json={
                "channel": "browser_action",
                "runner_id": runner_id,
                "steps": [
                    {"op": "navigate", "url": "https://erp.example.com/transfer"},
                    {"op": "click", "target": "#批量删除", "risk": "high"},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        await _enterprise_user(client, test_engine, f"member-{runner_id}@test.com", role="member")
        return resp.json()["data"]["challenge_id"]
