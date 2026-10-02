"""影子模式 API 测试（产品完善方案_v3.2 补1）。

覆盖：创建任务 / 状态机转换（record-ai → evaluate → promote / demote）/ 汇总。
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models.enterprise import Enterprise
from app.models.user import User


@pytest_asyncio.fixture
async def shadow_admin_client(client, test_engine):
    """已登录且绑定企业、角色为 admin 的测试客户端。"""

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        ent = Enterprise(id="ent-shadow", name="影子测试企业")
        s.add(ent)
        await s.commit()

    await register_and_login(client, "shadow@test.com", "影子管理员")
    async with factory() as s:
        result = await s.execute(select(User).where(User.email == "shadow@test.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        user.enterprise_id = "ent-shadow"
        user.role = "admin"
        await s.commit()

    yield client, "ent-shadow"


async def register_and_login(client, email: str, name: str):
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": name,
        "password": "pass1234",
    })
    assert resp.status_code == 201, f"注册失败: {resp.text}"
    resp = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "pass1234",
    })
    assert resp.status_code == 200, f"登录失败: {resp.text}"


pytestmark = pytest.mark.asyncio


async def test_create_task_and_list(shadow_admin_client):
    client, ent_id = shadow_admin_client
    resp = await client.post(
        "/api/v1/shadow/tasks",
        json={
            "enterprise_id": ent_id,
            "task_type": "inquiry",
            "question": "客户询价：SL-T100 最低报价多少？",
            "human_answer": "按价目表 8 折报价。",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "shadowing"
    assert data["eval_result"] == "pending"
    task_id = data["id"]

    resp = await client.get(
        "/api/v1/shadow/tasks", params={"enterprise_id": ent_id}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["id"] == task_id


async def test_state_machine_record_evaluate_promote(shadow_admin_client):
    client, ent_id = shadow_admin_client
    resp = await client.post(
        "/api/v1/shadow/tasks",
        json={
            "enterprise_id": ent_id,
            "task_type": "quotation",
            "question": "生成报价单",
            "human_answer": "基准确认",
        },
    )
    task_id = resp.json()["data"]["id"]

    # shadowing -> evaluating
    resp = await client.post(
        f"/api/v1/shadow/tasks/{task_id}/record-ai",
        json={"ai_answer": "AI 报价", "confidence": 0.9},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "evaluating"

    # evaluating -> qualified（命中 + auto_qualify）
    resp = await client.post(
        f"/api/v1/shadow/tasks/{task_id}/evaluate",
        json={"match": True, "auto_qualify": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "qualified"
    assert resp.json()["data"]["eval_result"] == "match"

    # qualified -> autonomous
    resp = await client.post(f"/api/v1/shadow/tasks/{task_id}/promote")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "autonomous"
    assert resp.json()["data"]["promoted_at"] is not None


async def test_invalid_transition_rejected(shadow_admin_client):
    client, ent_id = shadow_admin_client
    resp = await client.post(
        "/api/v1/shadow/tasks",
        json={
            "enterprise_id": ent_id,
            "task_type": "inquiry",
            "question": "问题",
        },
    )
    task_id = resp.json()["data"]["id"]

    # 尚未记录 AI 回答，直接 promote 应为非法状态转换
    resp = await client.post(f"/api/v1/shadow/tasks/{task_id}/promote")
    assert resp.status_code == 400, resp.text


async def test_demote_autonomous_to_evaluating(shadow_admin_client):
    client, ent_id = shadow_admin_client
    resp = await client.post(
        "/api/v1/shadow/tasks",
        json={"enterprise_id": ent_id, "task_type": "inquiry", "question": "Q"},
    )
    task_id = resp.json()["data"]["id"]
    await client.post(
        f"/api/v1/shadow/tasks/{task_id}/record-ai",
        json={"ai_answer": "A", "confidence": 0.8},
    )
    await client.post(
        f"/api/v1/shadow/tasks/{task_id}/evaluate",
        json={"match": True, "auto_qualify": True},
    )
    await client.post(f"/api/v1/shadow/tasks/{task_id}/promote")
    assert resp.status_code == 200

    # autonomous -> evaluating（异常降级）
    resp = await client.post(
        f"/api/v1/shadow/tasks/{task_id}/demote",
        params={"target": "evaluating"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "evaluating"


async def test_summary(shadow_admin_client):
    client, ent_id = shadow_admin_client
    for i in range(3):
        await client.post(
            "/api/v1/shadow/tasks",
            json={"enterprise_id": ent_id, "task_type": "inquiry", "question": f"Q{i}"},
        )
    resp = await client.get(
        "/api/v1/shadow/tasks/summary", params={"enterprise_id": ent_id}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total_count"] == 3
    shadow_stage = next(s for s in data["stages"] if s["status"] == "shadowing")
    assert shadow_stage["count"] == 3


async def test_tenant_isolation(shadow_admin_client):
    client, ent_id = shadow_admin_client
    resp = await client.get("/api/v1/shadow/tasks", params={"enterprise_id": ent_id})
    assert resp.status_code == 200, resp.text

    # 请求不属于本企业的 enterprise_id 应被拒绝（企业不存在 → 404）
    resp = await client.get(
        "/api/v1/shadow/tasks", params={"enterprise_id": "other-ent"}
    )
    assert resp.status_code == 404, resp.text
