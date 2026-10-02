"""设置向导 API 测试。

覆盖：
1. POST /setup/start - 启动设置会话
2. POST /setup/{session_id}/message - 发送消息
3. GET /setup/{session_id}/plan - 获取计划
4. POST /setup/{session_id}/confirm - 确认计划
5. 权限校验
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models.enterprise import Enterprise
from app.models.user import User


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()


@pytest_asyncio.fixture
async def auth_client(client, test_engine):
    """注册用户并绑定企业（setup/start 要求当前用户已绑定企业）。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        ent = Enterprise(id="ent-setup-1", name="设置测试企业")
        s.add(ent)
        await s.commit()

    # 注册后 Cookie 中已包含 access_token
    await client.post("/api/v1/auth/register", json={
        "email": "test_setup@test.com",
        "name": "测试",
        "password": "pass1234",
    })

    # register 创建的用户无企业归属，手动绑定企业 ID
    async with factory() as s:
        result = await s.execute(select(User).where(User.email == "test_setup@test.com"))
        user = result.scalar_one()
        user.enterprise_id = "ent-setup-1"
        await s.commit()

    return client


class TestSetupApi:
    """设置向导 API 测试。"""

    @pytest.mark.asyncio
    async def test_start_setup_success(self, auth_client):
        """POST /setup/start 传入 folder_path 应返回 session_id。"""
        resp = await auth_client.post("/api/v1/setup/start", json={
            "folder_path": "./test_data",
            "enterprise_id": "ent-setup-1",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["session_id"]

    @pytest.mark.asyncio
    async def test_start_setup_missing_folder(self, auth_client):
        """缺少 folder_path 应返回 422。"""
        resp = await auth_client.post("/api/v1/setup/start", json={
            "enterprise_id": "ent-setup-1",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_send_message_to_nonexistent_session(self, auth_client):
        """向不存在的 session 发送消息应返回 404。"""
        resp = await auth_client.post(
            "/api/v1/setup/nonexistent-session-id/message",
            json={"content": "你好"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_plan_nonexistent_session(self, auth_client):
        """获取不存在 session 的计划应返回 404。"""
        resp = await auth_client.get("/api/v1/setup/nonexistent-session-id/plan")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_confirm_nonexistent_session(self, auth_client):
        """确认不存在 session 的计划应返回 404。"""
        resp = await auth_client.post(
            "/api/v1/setup/nonexistent-session-id/confirm",
            json={"confirmed": True},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthorized_access(self, client):
        """未认证访问设置端点应返回 401/403。"""
        resp = await client.post("/api/v1/setup/start", json={
            "folder_path": "./test_data",
            "enterprise_id": "ent-setup-1",
        })
        assert resp.status_code in (401, 403)
