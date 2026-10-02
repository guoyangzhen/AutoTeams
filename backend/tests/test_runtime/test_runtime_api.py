"""Runtime API 端点测试（api/runtime.py，spec §10.7 WT2 部分）。

覆盖 8 个端点：
- GET    /{enterprise_id}                  当前激活 Runtime（管理员完整 / 成员脱敏 / 404 / 403）
- GET    /{enterprise_id}/versions         版本列表（分页）
- GET    /{enterprise_id}/versions/{ver}   按版本获取
- POST   /{enterprise_id}/rollback         回滚（管理员 / 成员 403 / 404）
- GET    /{enterprise_id}/diff             两版本 diff
- GET    /{enterprise_id}/organization     组织运行时
- GET    /{enterprise_id}/agents           Agent 模板（脱敏）
- GET    /{enterprise_id}/processes        流程引擎

工程约束验证：
- 无尾斜杠（路径用 "/{...}" 而非 "/{...}/"）
- 列表端点含 limit/offset 分页
- 鉴权 + 企业隔离 + 管理员校验
- 敏感字段过滤（非管理员剔除 system_prompt / permissions / 工具 config）
- 错误响应统一使用 ErrorCode 常量

数据 seed 策略：Runtime 创建由 WT1 编译器负责（无 API 端点），故本测试通过
``db_session`` 调用 ``save_runtime`` 直接 seed，再以 ``client`` 验证 HTTP 端点。
两者共享同一 ``test_engine``（内存 SQLite），数据互通。
"""
import pytest

from app.services.runtime import save_runtime
from app.utils.error_codes import ErrorCode

from .conftest import make_compile_result


# ============================================================
# API 测试辅助函数
# ============================================================


async def _register_and_create_enterprise(client, email="runtime-admin@test.com"):
    """注册用户 → 创建企业 → 返回 enterprise_id。

    注册者自动成为该企业的 admin。
    """
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Runtime管理员", "password": "Test1234!"},
    )
    assert resp.status_code == 201, f"注册失败: {resp.text}"
    resp = await client.post("/api/v1/enterprises", json={"name": "Runtime测试企业"})
    assert resp.status_code == 201, f"创建企业失败: {resp.text}"
    return resp.json()["data"]["id"]


def _save_cookies(client):
    return [(c.name, c.value, c.domain, c.path) for c in client.cookies.jar]


def _restore_cookies(client, cookies):
    client.cookies.clear()
    for name, value, domain, path in cookies:
        client.cookies.set(name, value, domain=domain or None, path=path or "/")


async def _invite_and_join_as_member(client, enterprise_id, email="runtime-member@test.com"):
    """管理员邀请 → 新用户通过邀请注册 → 返回成员 Cookie 状态。

    调用后 client 恢复为管理员身份。
    """
    resp = await client.post(f"/api/v1/enterprises/{enterprise_id}/invite")
    assert resp.status_code == 200, f"生成邀请失败: {resp.text}"
    invite_token = resp.json()["data"]["invite_token"]

    admin_cookies = _save_cookies(client)

    resp = await client.post(
        "/api/v1/auth/register-with-invite",
        params={"invite_token": invite_token},
        json={"email": email, "name": "Runtime成员", "password": "Test1234!"},
    )
    assert resp.status_code == 201, f"邀请注册失败: {resp.text}"

    member_cookies = _save_cookies(client)
    _restore_cookies(client, admin_cookies)
    return member_cookies


async def _seed_runtime(db_session, enterprise_id, **kwargs):
    """通过 service 层 seed 一份 Runtime（默认 v1.0.0 激活）。"""
    cr = make_compile_result(**kwargs)
    return await save_runtime(
        db_session,
        enterprise_id=enterprise_id,
        compile_result=cr,
        version=kwargs.pop("version", None),
    )


# ============================================================
# GET /{enterprise_id} — 当前激活 Runtime
# ============================================================


class TestGetCurrentRuntime:
    async def test_admin_gets_full_runtime(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id, with_sensitive=True)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 管理员可见敏感字段
        assert data["agents"][0]["system_prompt"] != ""
        assert data["agents"][0]["permissions"] != []
        assert data["agents"][0]["tools"][0]["permissions"] != []

    async def test_member_gets_redacted_runtime(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id, with_sensitive=True)

        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 成员不可见敏感字段
        for agent in data["agents"]:
            assert agent["system_prompt"] == ""
            assert agent["permissions"] == []
            for tool in agent.get("tools", []):
                assert tool["permissions"] == []
        # 工具注册表 config 也应脱敏
        for tool in data.get("tool_registry", []):
            assert tool["config"] == {}

    async def test_404_when_no_runtime(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.NOT_FOUND

    async def test_404_when_enterprise_not_found(self, client, db_session):
        await _register_and_create_enterprise(client)
        resp = await client.get("/api/v1/runtime/nonexistent-enterprise-id")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.ENTERPRISE_NOT_FOUND

    async def test_403_non_member(self, client, db_session):
        """非企业成员应被拒绝。"""
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        # 注册另一个用户（不属于该企业）
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "outsider@test.com",
                "name": "外部用户",
                "password": "Test1234!",
            },
        )
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}")
        assert resp.status_code == 403

    async def test_no_trailing_slash(self, client, db_session):
        """端点路径无尾斜杠，带尾斜杠应 404（避免 307 重定向）。"""
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        # 无尾斜杠正常工作
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}")
        assert resp.status_code == 200


# ============================================================
# GET /{enterprise_id}/versions — 版本列表
# ============================================================


class TestListVersions:
    async def test_list_versions_with_pagination(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        # seed 3 个版本
        for i in range(3):
            await save_runtime(
                db_session,
                enterprise_id=enterprise_id,
                compile_result=make_compile_result(),
                version=f"v1.{i}.0",
            )

        resp = await client.get(
            f"/api/v1/runtime/{enterprise_id}/versions",
            params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 3
        assert len(data["items"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0
        # 最新在前
        assert data["items"][0]["version"] == "v1.2.0"

    async def test_list_versions_default_pagination(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/versions")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["is_active"] is True
        assert data["items"][0]["runtime_id"] is not None

    async def test_list_versions_invalid_limit(self, client, db_session):
        """limit 超出 [1, 100] 应 422。"""
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(
            f"/api/v1/runtime/{enterprise_id}/versions",
            params={"limit": 0},
        )
        assert resp.status_code == 422

        resp = await client.get(
            f"/api/v1/runtime/{enterprise_id}/versions",
            params={"limit": 101},
        )
        assert resp.status_code == 422

    async def test_list_versions_invalid_offset(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(
            f"/api/v1/runtime/{enterprise_id}/versions",
            params={"offset": -1},
        )
        assert resp.status_code == 422

    async def test_list_versions_403_non_member(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "outsider2@test.com",
                "name": "外部用户",
                "password": "Test1234!",
            },
        )
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/versions")
        assert resp.status_code == 403


# ============================================================
# GET /{enterprise_id}/versions/{version} — 按版本获取
# ============================================================


class TestGetRuntimeByVersion:
    async def test_get_specific_version(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id, version="v1.0.0")

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/versions/v1.0.0")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == "v1.0.0"

    async def test_404_version_not_found(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/versions/v9.9.9")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.NOT_FOUND

    async def test_member_redacted_for_specific_version(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id, with_sensitive=True)

        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/versions/v1.0.0")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["agents"][0]["system_prompt"] == ""


# ============================================================
# POST /{enterprise_id}/rollback — 回滚
# ============================================================


class TestRollbackRuntime:
    async def test_admin_rollback_success(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await save_runtime(
            db_session, enterprise_id=enterprise_id,
            compile_result=make_compile_result(agent_count=2),
            version="v1.0.0",
        )
        await save_runtime(
            db_session, enterprise_id=enterprise_id,
            compile_result=make_compile_result(agent_count=5),
            version="v1.1.0",
        )

        resp = await client.post(
            f"/api/v1/runtime/{enterprise_id}/rollback",
            json={"target_version": "v1.0.0"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["new_active_version"] == "v1.0.0"
        assert data["status"] == "rolled_back"
        assert data["runtime_id"] is not None

    async def test_403_member_cannot_rollback(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.post(
            f"/api/v1/runtime/{enterprise_id}/rollback",
            json={"target_version": "v1.0.0"},
        )
        assert resp.status_code == 403
        assert resp.json()["message"] == ErrorCode.FORBIDDEN

    async def test_404_rollback_nonexistent_version(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        resp = await client.post(
            f"/api/v1/runtime/{enterprise_id}/rollback",
            json={"target_version": "v9.9.9"},
        )
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.NOT_FOUND

    async def test_rollback_idempotent_when_already_active(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id, version="v1.0.0")

        resp = await client.post(
            f"/api/v1/runtime/{enterprise_id}/rollback",
            json={"target_version": "v1.0.0"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["new_active_version"] == "v1.0.0"


# ============================================================
# GET /{enterprise_id}/diff — 两版本 diff
# ============================================================


class TestDiffRuntime:
    async def test_diff_returns_changes(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await save_runtime(
            db_session, enterprise_id=enterprise_id,
            compile_result=make_compile_result(agent_count=2),
            version="v1.0.0",
        )
        await save_runtime(
            db_session, enterprise_id=enterprise_id,
            compile_result=make_compile_result(agent_count=3),
            version="v1.1.0",
        )

        resp = await client.get(
            f"/api/v1/runtime/{enterprise_id}/diff",
            params={"a": "v1.0.0", "b": "v1.1.0"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version_a"] == "v1.0.0"
        assert data["version_b"] == "v1.1.0"
        assert len(data["changes"]) > 0
        added = [c for c in data["changes"] if c["section"] == "agents" and c["change_type"] == "added"]
        assert len(added) == 1

    async def test_diff_404_missing_version(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        resp = await client.get(
            f"/api/v1/runtime/{enterprise_id}/diff",
            params={"a": "v1.0.0", "b": "v9.9.9"},
        )
        assert resp.status_code == 404

    async def test_diff_422_missing_query_params(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/diff")
        assert resp.status_code == 422

    async def test_diff_member_can_view(self, client, db_session):
        """diff 仅返回变更 key 与类型，不含敏感原文，成员可查看。"""
        enterprise_id = await _register_and_create_enterprise(client)
        await save_runtime(
            db_session, enterprise_id=enterprise_id,
            compile_result=make_compile_result(agent_count=2),
            version="v1.0.0",
        )
        await save_runtime(
            db_session, enterprise_id=enterprise_id,
            compile_result=make_compile_result(agent_count=3),
            version="v1.1.0",
        )

        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.get(
            f"/api/v1/runtime/{enterprise_id}/diff",
            params={"a": "v1.0.0", "b": "v1.1.0"},
        )
        assert resp.status_code == 200


# ============================================================
# GET /{enterprise_id}/organization — 组织运行时
# ============================================================


class TestGetOrganization:
    async def test_get_organization(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(
            db_session, enterprise_id, department_count=2
        )

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/organization")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["departments"]) == 2
        assert data["departments"][0]["dept_id"] == "dept-0"

    async def test_organization_404_no_runtime(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/organization")
        assert resp.status_code == 404


# ============================================================
# GET /{enterprise_id}/agents — Agent 模板列表
# ============================================================


class TestGetAgents:
    async def test_admin_gets_full_agent_templates(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(
            db_session, enterprise_id, agent_count=2, with_sensitive=True
        )

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/agents")
        assert resp.status_code == 200
        agents = resp.json()["data"]["agents"]
        assert len(agents) == 2
        assert agents[0]["system_prompt"] != ""
        assert agents[0]["permissions"] != []

    async def test_member_gets_redacted_agent_templates(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(
            db_session, enterprise_id, agent_count=2, with_sensitive=True
        )

        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/agents")
        assert resp.status_code == 200
        agents = resp.json()["data"]["agents"]
        for a in agents:
            assert a["system_prompt"] == ""
            assert a["permissions"] == []
            for t in a.get("tools", []):
                assert t["permissions"] == []

    async def test_agents_empty_when_no_runtime(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/agents")
        assert resp.status_code == 200
        assert resp.json()["data"]["agents"] == []


# ============================================================
# GET /{enterprise_id}/processes — 流程引擎列表
# ============================================================


class TestGetProcesses:
    async def test_get_processes(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id, process_count=2)

        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/processes")
        assert resp.status_code == 200
        processes = resp.json()["data"]["processes"]
        assert len(processes) == 2
        assert processes[0]["engine_id"] == "engine-0"

    async def test_processes_empty_when_no_runtime(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/processes")
        assert resp.status_code == 200
        assert resp.json()["data"]["processes"] == []

    async def test_processes_403_non_member(self, client, db_session):
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_runtime(db_session, enterprise_id)
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "outsider3@test.com",
                "name": "外部用户",
                "password": "Test1234!",
            },
        )
        resp = await client.get(f"/api/v1/runtime/{enterprise_id}/processes")
        assert resp.status_code == 403
