"""对话 CRUD API 测试。

覆盖：
1. 对话 CRUD（create/get/list/update/delete）
2. 消息创建
3. 权限校验

测试模式参考 test_skill_system.py：
- 每个测试函数用独立的内存数据库（test_engine）
- Agent 通过直接 DB 写入创建（避免依赖 build_via_graph 的真实文件夹）
- 用户通过 /auth/register 注册（无企业归属）
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models.agent import Agent
from app.models.enterprise import Enterprise


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()


async def _create_agent(test_engine) -> str:
    """创建测试用 Enterprise + Agent，返回 agent_id。

    Conversation 需要 agent_id（FK），Agent 需要 enterprise_id（FK），
    而 build_via_graph 需要真实文件夹，故直接在 DB 中创建。
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        ent = Enterprise(name="对话测试企业")
        s.add(ent)
        await s.commit()
        agent = Agent(enterprise_id=ent.id, name="测试Agent", status="ready")
        s.add(agent)
        await s.commit()
        return str(agent.id)





class TestConversationCRUD:
    """对话 CRUD API 测试。"""

    @pytest.mark.asyncio
    async def test_create_conversation_success(self, authenticated_client, test_engine):
        """POST /conversations 应创建新对话。"""
        agent_id = await _create_agent(test_engine)

        resp = await authenticated_client.post("/api/v1/conversations", json={
            "agent_id": agent_id,
            "title": "测试对话",
        })
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["id"]
        assert data["agent_id"] == agent_id
        assert data["title"] == "测试对话"
        assert data["user_id"]

    @pytest.mark.asyncio
    async def test_list_conversations(self, authenticated_client, test_engine):
        """GET /conversations 应返回当前用户的对话列表。"""
        agent_id = await _create_agent(test_engine)

        # 创建 2 个对话
        for i in range(2):
            resp = await authenticated_client.post("/api/v1/conversations", json={
                "agent_id": agent_id,
                "title": f"对话{i}",
            })
            assert resp.status_code == 201

        resp = await authenticated_client.get("/api/v1/conversations")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_get_conversation(self, authenticated_client, test_engine):
        """GET /conversations/{id} 应返回对话详情（含消息列表）。"""
        agent_id = await _create_agent(test_engine)

        create_resp = await authenticated_client.post("/api/v1/conversations", json={
            "agent_id": agent_id,
            "title": "详情测试",
        })
        conversation_id = create_resp.json()["data"]["id"]

        resp = await authenticated_client.get(f"/api/v1/conversations/{conversation_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == conversation_id
        assert data["title"] == "详情测试"
        # 新对话的 messages 应为空列表
        assert data["messages"] == []

    @pytest.mark.asyncio
    async def test_update_conversation_title(self, authenticated_client, test_engine):
        """PUT /conversations/{id} 应更新对话标题（P1-8）。"""
        agent_id = await _create_agent(test_engine)

        create_resp = await authenticated_client.post("/api/v1/conversations", json={
            "agent_id": agent_id,
            "title": "原标题",
        })
        conversation_id = create_resp.json()["data"]["id"]

        resp = await authenticated_client.put(f"/api/v1/conversations/{conversation_id}", json={
            "title": "新标题",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["title"] == "新标题"
        assert data["id"] == conversation_id

    @pytest.mark.asyncio
    async def test_delete_conversation(self, authenticated_client, test_engine):
        """DELETE /conversations/{id} 应删除对话（P1-8）。"""
        agent_id = await _create_agent(test_engine)

        create_resp = await authenticated_client.post("/api/v1/conversations", json={
            "agent_id": agent_id,
            "title": "待删除",
        })
        conversation_id = create_resp.json()["data"]["id"]

        resp = await authenticated_client.delete(f"/api/v1/conversations/{conversation_id}")
        assert resp.status_code == 204

        # 验证已删除
        resp = await authenticated_client.get(f"/api/v1/conversations/{conversation_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_message(self, authenticated_client, test_engine):
        """POST /conversations/{id}/messages 应创建新消息。"""
        agent_id = await _create_agent(test_engine)

        create_resp = await authenticated_client.post("/api/v1/conversations", json={
            "agent_id": agent_id,
            "title": "消息测试",
        })
        conversation_id = create_resp.json()["data"]["id"]

        resp = await authenticated_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "你好"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["id"]
        assert data["conversation_id"] == conversation_id
        assert data["role"] == "user"
        assert data["content"] == "你好"


class TestConversationAuth:
    """对话 API 权限校验测试。"""

    @pytest.mark.asyncio
    async def test_unauthorized_access(self, client):
        """未带 token 访问对话列表应返回 401/403。"""
        resp = await client.get("/api/v1/conversations")
        assert resp.status_code in (401, 403)
