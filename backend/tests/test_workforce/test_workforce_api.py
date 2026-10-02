"""Workforce API 端点测试（api/workforce.py，spec §10.7 WT3 部分）。

覆盖 6 个端点：
- POST   /generate                          触发生成（管理员 / 成员 403 / 404 企业）
- POST   /confirm                            确认推荐（批量创建 / 无效 position / 空列表）
- GET    /{enterprise_id}                    列出 Workforce（分页 / 企业隔离 / 403）
- GET    /{agent_id}/lifecycle               生命周期状态（404 / 成功）
- POST   /{agent_id}/transition              阶段转换（管理员 / 成员 403 / 400 非法转换）
- GET    /{agent_id}/memory/{conversation_id} 查询记忆（三层 / 404）

工程约束验证：
- 无尾斜杠
- 列表端点含 limit/offset 分页
- 鉴权 + 企业隔离 + 管理员校验
- 错误响应统一使用 ErrorCode 常量
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.utils.error_codes import ErrorCode
from app.models.agent import Agent


# ============================================================
# API 测试辅助函数
# ============================================================


async def _register_and_create_enterprise(client, email="wf-admin@test.com"):
    """注册用户 → 创建企业 → 返回 enterprise_id。"""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Workforce管理员", "password": "Test1234!"},
    )
    assert resp.status_code == 201, f"注册失败: {resp.text}"
    resp = await client.post("/api/v1/enterprises", json={"name": "Workforce测试企业"})
    assert resp.status_code == 201, f"创建企业失败: {resp.text}"
    return resp.json()["data"]["id"]


def _save_cookies(client):
    return [(c.name, c.value, c.domain, c.path) for c in client.cookies.jar]


def _restore_cookies(client, cookies):
    client.cookies.clear()
    for name, value, domain, path in cookies:
        client.cookies.set(name, value, domain=domain or None, path=path or "/")


async def _invite_and_join_as_member(client, enterprise_id, email="wf-member@test.com"):
    """管理员邀请 → 新用户通过邀请注册 → 返回成员 Cookie 状态。"""
    resp = await client.post(f"/api/v1/enterprises/{enterprise_id}/invite")
    assert resp.status_code == 200, f"生成邀请失败: {resp.text}"
    invite_token = resp.json()["data"]["invite_token"]

    admin_cookies = _save_cookies(client)

    resp = await client.post(
        "/api/v1/auth/register-with-invite",
        params={"invite_token": invite_token},
        json={"email": email, "name": "Workforce成员", "password": "Test1234!"},
    )
    assert resp.status_code == 201, f"邀请注册失败: {resp.text}"

    member_cookies = _save_cookies(client)
    _restore_cookies(client, admin_cookies)
    return member_cookies


async def _seed_agent(db_session, enterprise_id, **kwargs):
    """直接创建 Agent 记录（用于生命周期/记忆测试）。"""
    agent = Agent(
        enterprise_id=enterprise_id,
        name=kwargs.get("name", "测试AI员工"),
        description="测试用",
        system_prompt=kwargs.get("system_prompt", "你是测试AI员工"),
        status="ready",
        version="1.0.0",
        config={},
        lifecycle_stage=kwargs.get("lifecycle_stage", "recruit"),
        position_id=kwargs.get("position_id", "pos_sales"),
        file_count=kwargs.get("file_count", 10),
        knowledge_count=kwargs.get("knowledge_count", 50),
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


# ============================================================
# POST /generate — 触发生成
# ============================================================


class TestGenerateWorkforce:
    async def test_admin_generate_success(self, client, db_session):
        """管理员触发生成成功。"""
        enterprise_id = await _register_and_create_enterprise(client)

        resp = await client.post(
            "/api/v1/workforce/generate",
            json={"enterprise_id": enterprise_id},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 10  # MVP 10 个核心岗位（5 P0 + 5 P1，见 generator._default_mvp_capability_matrix）
        assert len(data["recommendations"]) == 10

    async def test_member_generate_forbidden(self, client, db_session):
        """非管理员触发生成返回 403。"""
        enterprise_id = await _register_and_create_enterprise(client)
        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.post(
            "/api/v1/workforce/generate",
            json={"enterprise_id": enterprise_id},
        )
        assert resp.status_code == 403
        assert resp.json()["message"] == ErrorCode.FORBIDDEN

    async def test_generate_nonexistent_enterprise(self, client, db_session):
        """企业不存在返回 404。"""
        await _register_and_create_enterprise(client)
        resp = await client.post(
            "/api/v1/workforce/generate",
            json={"enterprise_id": "nonexistent-enterprise-id"},
        )
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.ENTERPRISE_NOT_FOUND

    async def test_generate_non_member_forbidden(self, client, db_session):
        """非企业成员触发生成返回 403。"""
        enterprise_id = await _register_and_create_enterprise(client)
        # 注册另一个不属于该企业的用户
        await client.post(
            "/api/v1/auth/register",
            json={"email": "outsider@wf.com", "name": "外部用户", "password": "Test1234!"},
        )
        resp = await client.post(
            "/api/v1/workforce/generate",
            json={"enterprise_id": enterprise_id},
        )
        assert resp.status_code == 403


# ============================================================
# POST /confirm — 确认推荐
# ============================================================


class TestConfirmWorkforce:
    async def test_admin_confirm_success(self, client, db_session):
        """管理员确认推荐并创建 Agent。"""
        enterprise_id = await _register_and_create_enterprise(client)
        # 先生成推荐
        resp = await client.post(
            "/api/v1/workforce/generate",
            json={"enterprise_id": enterprise_id},
        )
        recs = resp.json()["data"]["recommendations"]
        position_ids = [r["position_id"] for r in recs[:2]]

        # 确认前 2 个
        resp = await client.post(
            "/api/v1/workforce/confirm",
            json={
                "enterprise_id": enterprise_id,
                "confirmed_position_ids": position_ids,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["created_agents"]) == 2
        assert len(data["failed"]) == 0

    async def test_confirm_invalid_position(self, client, db_session):
        """确认无效 position_id 记入 failed。"""
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.post(
            "/api/v1/workforce/confirm",
            json={
                "enterprise_id": enterprise_id,
                "confirmed_position_ids": ["nonexistent_pos"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["failed"]) == 1

    async def test_confirm_empty_list(self, client, db_session):
        """空确认列表。"""
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.post(
            "/api/v1/workforce/confirm",
            json={
                "enterprise_id": enterprise_id,
                "confirmed_position_ids": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["created_agents"] == []
        assert data["failed"] == []

    async def test_member_confirm_forbidden(self, client, db_session):
        """非管理员确认返回 403。"""
        enterprise_id = await _register_and_create_enterprise(client)
        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.post(
            "/api/v1/workforce/confirm",
            json={
                "enterprise_id": enterprise_id,
                "confirmed_position_ids": [],
            },
        )
        assert resp.status_code == 403


# ============================================================
# GET /{enterprise_id} — 列出 Workforce
# ============================================================


class TestListWorkforce:
    async def test_list_with_pagination(self, client, db_session):
        """分页列出 Workforce。"""
        enterprise_id = await _register_and_create_enterprise(client)
        for i in range(3):
            await _seed_agent(db_session, enterprise_id, name=f"员工{i}")

        resp = await client.get(
            f"/api/v1/workforce/{enterprise_id}",
            params={"limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 3
        assert len(data["items"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

    async def test_list_default_pagination(self, client, db_session):
        """默认分页。"""
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_agent(db_session, enterprise_id)

        resp = await client.get(f"/api/v1/workforce/{enterprise_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1

    async def test_list_invalid_limit(self, client, db_session):
        """limit 超出 [1, 100] 返回 422。"""
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(
            f"/api/v1/workforce/{enterprise_id}",
            params={"limit": 0},
        )
        assert resp.status_code == 422

        resp = await client.get(
            f"/api/v1/workforce/{enterprise_id}",
            params={"limit": 101},
        )
        assert resp.status_code == 422

    async def test_list_invalid_offset(self, client, db_session):
        """offset < 0 返回 422。"""
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(
            f"/api/v1/workforce/{enterprise_id}",
            params={"offset": -1},
        )
        assert resp.status_code == 422

    async def test_list_non_member_forbidden(self, client, db_session):
        """非企业成员返回 403。"""
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_agent(db_session, enterprise_id)
        await client.post(
            "/api/v1/auth/register",
            json={"email": "outsider2@wf.com", "name": "外部用户", "password": "Test1234!"},
        )
        resp = await client.get(f"/api/v1/workforce/{enterprise_id}")
        assert resp.status_code == 403

    async def test_list_empty_enterprise(self, client, db_session):
        """无 Agent 的企业返回空列表。"""
        enterprise_id = await _register_and_create_enterprise(client)
        resp = await client.get(f"/api/v1/workforce/{enterprise_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_no_sensitive_fields(self, client, db_session):
        """响应不含敏感字段（system_prompt/permissions）。"""
        enterprise_id = await _register_and_create_enterprise(client)
        await _seed_agent(db_session, enterprise_id, system_prompt="敏感系统提示词")

        resp = await client.get(f"/api/v1/workforce/{enterprise_id}")
        data = resp.json()["data"]
        for item in data["items"]:
            assert "system_prompt" not in item
            assert "permissions" not in item


# ============================================================
# GET /{agent_id}/lifecycle — 生命周期状态
# ============================================================


class TestGetLifecycle:
    async def test_get_lifecycle_success(self, client, db_session):
        """查询生命周期状态。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id, lifecycle_stage="recruit")

        resp = await client.get(f"/api/v1/workforce/{agent.id}/lifecycle")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["stage"] == "recruit"
        assert "stage_entered_at" in data
        assert isinstance(data["history"], list)

    async def test_get_lifecycle_agent_not_found(self, client, db_session):
        """Agent 不存在返回 404。"""
        await _register_and_create_enterprise(client)
        resp = await client.get("/api/v1/workforce/nonexistent-agent/lifecycle")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.AGENT_NOT_FOUND

    async def test_get_lifecycle_non_member_forbidden(self, client, db_session):
        """非企业成员返回 403。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id)
        await client.post(
            "/api/v1/auth/register",
            json={"email": "outsider3@wf.com", "name": "外部用户", "password": "Test1234!"},
        )
        resp = await client.get(f"/api/v1/workforce/{agent.id}/lifecycle")
        assert resp.status_code == 403


# ============================================================
# POST /{agent_id}/transition — 阶段转换
# ============================================================


class TestTransitionStage:
    async def test_admin_transition_success(self, client, db_session):
        """管理员执行阶段转换。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(
            db_session, enterprise_id, lifecycle_stage="recruit",
            system_prompt="你是AI员工",
        )

        resp = await client.post(
            f"/api/v1/workforce/{agent.id}/transition",
            json={"target_stage": "training", "reason": "配置已注入"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["new_stage"] == "training"
        assert data["status"] == "success"

    async def test_member_transition_forbidden(self, client, db_session):
        """非管理员转换返回 403。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id, lifecycle_stage="recruit")
        member_cookies = await _invite_and_join_as_member(client, enterprise_id)
        _restore_cookies(client, member_cookies)

        resp = await client.post(
            f"/api/v1/workforce/{agent.id}/transition",
            json={"target_stage": "training"},
        )
        assert resp.status_code == 403
        assert resp.json()["message"] == ErrorCode.FORBIDDEN

    async def test_transition_invalid_stage(self, client, db_session):
        """无效阶段返回 400。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id, lifecycle_stage="recruit")

        resp = await client.post(
            f"/api/v1/workforce/{agent.id}/transition",
            json={"target_stage": "invalid_stage"},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == ErrorCode.INVALID_REQUEST

    async def test_transition_agent_not_found(self, client, db_session):
        """Agent 不存在返回 404。"""
        await _register_and_create_enterprise(client)
        resp = await client.post(
            "/api/v1/workforce/nonexistent-agent/transition",
            json={"target_stage": "training"},
        )
        assert resp.status_code == 404

    async def test_transition_skipped_stage_blocked(self, client, db_session):
        """跳级转换返回 400（Recruit 不能直接到 Production）。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id, lifecycle_stage="recruit")

        resp = await client.post(
            f"/api/v1/workforce/{agent.id}/transition",
            json={"target_stage": "production"},
        )
        assert resp.status_code == 400


# ============================================================
# GET /{agent_id}/memory/{conversation_id} — 查询记忆
# ============================================================


class TestQueryMemory:
    async def test_query_memory_returns_three_layers(self, client, db_session):
        """查询记忆返回三层结构。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id)

        with patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(side_effect=RuntimeError("mock 不可用")),
        ):
            resp = await client.get(
                f"/api/v1/workforce/{agent.id}/memory/conv-test-1",
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "short_term" in data
        assert "entity" in data
        assert "long_term" in data
        assert isinstance(data["short_term"], list)
        assert isinstance(data["entity"], list)
        assert isinstance(data["long_term"], list)

    async def test_query_memory_agent_not_found(self, client, db_session):
        """Agent 不存在返回 404。"""
        await _register_and_create_enterprise(client)
        resp = await client.get("/api/v1/workforce/nonexistent-agent/memory/conv-1")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.AGENT_NOT_FOUND

    async def test_query_memory_non_member_forbidden(self, client, db_session):
        """非企业成员返回 403。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id)
        await client.post(
            "/api/v1/auth/register",
            json={"email": "outsider4@wf.com", "name": "外部用户", "password": "Test1234!"},
        )
        resp = await client.get(f"/api/v1/workforce/{agent.id}/memory/conv-1")
        assert resp.status_code == 403

    async def test_query_memory_with_current_task(self, client, db_session):
        """带 current_task 参数查询记忆。"""
        enterprise_id = await _register_and_create_enterprise(client)
        agent = await _seed_agent(db_session, enterprise_id)

        with patch(
            "app.services.memory.long_term.VectorStoreService.create",
            new=AsyncMock(side_effect=RuntimeError("mock 不可用")),
        ):
            resp = await client.get(
                f"/api/v1/workforce/{agent.id}/memory/conv-test-2",
                params={"current_task": "查询测温精度", "top_k": 3},
            )
        assert resp.status_code == 200
