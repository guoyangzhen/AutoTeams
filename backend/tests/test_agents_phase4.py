"""Phase 4 架构修复测试：agents.py 串行修复。

覆盖：
- 3.2.4: list_agents 分页参数
- 3.2.1: knowledge_stats 合并查询（结果一致性）
- 3.3.6: _get_agent_or_404 合并后的 require_ready 参数
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.file import File
from app.models.user import User


async def _setup_enterprise_admin(client, test_engine, suffix: str):
    """创建企业并注册管理员，返回 (enterprise_id, user_id)。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ent_id = f"ent-p4-{suffix}"
    async with factory() as s:
        ent = Enterprise(id=ent_id, name=f"P4企业{suffix}")
        s.add(ent)
        await s.commit()

    resp = await client.post("/api/v1/auth/register", json={
        "email": f"p4admin{suffix}@test.com",
        "name": f"P4管理员{suffix}",
        "password": "pass1234",
    })
    assert resp.status_code == 201
    user_id = resp.json()["data"]["user"]["id"]

    # 设置为企业管理员
    async with factory() as s:
        user = await s.get(User, user_id)
        user.enterprise_id = ent_id
        user.role = "admin"
        await s.commit()

    return ent_id, user_id


async def _create_agent(test_engine, enterprise_id: str, name: str, status: str = "ready") -> Agent:
    """直接通过 DB 创建 Agent。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        agent = Agent(
            enterprise_id=enterprise_id,
            name=name,
            description="",
            status=status,
        )
        s.add(agent)
        await s.commit()
        await s.refresh(agent)
        return agent


async def _create_file(test_engine, agent_id: str, file_type: str, status: str, chunk_count: int):
    """直接通过 DB 创建 File 记录。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        f = File(
            agent_id=agent_id,
            original_name=f"test-{file_type}-{status}.txt",
            file_type=file_type,
            status=status,
            file_path=f"/tmp/test-{file_type}-{status}.txt",
            file_size=1024,
            chunk_count=chunk_count,
        )
        s.add(f)
        await s.commit()


class TestListAgentsPagination:
    """3.2.4: list_agents 分页参数测试。"""

    @pytest.mark.asyncio
    async def test_default_pagination(self, client, test_engine):
        """默认 limit=50，offset=0。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "default")

        # 创建 3 个 agent
        for i in range(3):
            await _create_agent(test_engine, ent_id, f"Agent-{i}")

        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 3

    @pytest.mark.asyncio
    async def test_limit_parameter(self, client, test_engine):
        """limit 参数限制返回数量。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "limit")

        # 创建 5 个 agent
        for i in range(5):
            await _create_agent(test_engine, ent_id, f"Agent-Limit-{i}")

        resp = await client.get("/api/v1/agents?limit=2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_offset_parameter(self, client, test_engine):
        """offset 参数跳过前 N 条。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "offset")

        # 创建 5 个 agent
        for i in range(5):
            await _create_agent(test_engine, ent_id, f"Agent-Offset-{i}")

        # 第一页：limit=2, offset=0
        resp1 = await client.get("/api/v1/agents?limit=2&offset=0")
        assert resp1.status_code == 200
        page1 = resp1.json()["data"]
        assert len(page1) == 2

        # 第二页：limit=2, offset=2
        resp2 = await client.get("/api/v1/agents?limit=2&offset=2")
        assert resp2.status_code == 200
        page2 = resp2.json()["data"]
        assert len(page2) == 2

        # 两页的 agent id 不重叠
        page1_ids = {a["id"] for a in page1}
        page2_ids = {a["id"] for a in page2}
        assert page1_ids.isdisjoint(page2_ids)

    @pytest.mark.asyncio
    async def test_limit_validation(self, client, test_engine):
        """limit 超出范围应返回 422。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "validation")

        # limit=0 应被拒绝（ge=1）
        resp = await client.get("/api/v1/agents?limit=0")
        assert resp.status_code == 422

        # limit=201 应被拒绝（le=200）
        resp = await client.get("/api/v1/agents?limit=201")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_no_enterprise_returns_empty(self, client, test_engine):
        """无企业用户应返回空列表。"""
        # 注册一个无企业的普通用户
        resp = await client.post("/api/v1/auth/register", json={
            "email": "noent-p4@test.com",
            "name": "无企业用户",
            "password": "pass1234",
        })
        assert resp.status_code == 201

        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == []


class TestKnowledgeStatsMergedQueries:
    """3.2.1: knowledge_stats 合并查询测试。

    验证合并后的查询结果与原 5 次查询一致。
    """

    @pytest.mark.asyncio
    async def test_knowledge_stats_basic(self, client, test_engine):
        """合并查询应正确返回文件统计。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "kstats")
        agent = await _create_agent(test_engine, ent_id, "知识统计Agent")

        # 创建不同类型和状态的文件
        await _create_file(test_engine, agent.id, "document", "completed", 10)
        await _create_file(test_engine, agent.id, "document", "processing", 5)
        await _create_file(test_engine, agent.id, "image", "completed", 3)
        await _create_file(test_engine, agent.id, "audio", "failed", 0)

        resp = await client.get(f"/api/v1/agents/{agent.id}/knowledge-stats")
        assert resp.status_code == 200
        data = resp.json()["data"]

        # 验证 totalFiles = 4
        assert data["totalFiles"] == 4

        # 验证 totalChunks = 10 + 5 + 3 + 0 = 18
        assert data["totalChunks"] == 18

        # 验证 fileTypeDistribution 包含 document 和 image
        type_names = {t["name"] for t in data["fileTypeDistribution"]}
        assert "文档" in type_names
        assert "图片" in type_names
        assert "音频" in type_names

        # document 类型应有 2 个文件（completed + processing）
        doc_dist = next(t for t in data["fileTypeDistribution"] if t["name"] == "文档")
        assert doc_dist["value"] == 2

        # 验证 fileStatus 包含 completed, processing, failed
        status_names = {s["name"] for s in data["fileStatus"]}
        assert "已完成" in status_names
        assert "处理中" in status_names
        assert "失败" in status_names

        # completed 状态应有 2 个文件（document + image）
        completed = next(s for s in data["fileStatus"] if s["name"] == "已完成")
        assert completed["value"] == 2

    @pytest.mark.asyncio
    async def test_knowledge_stats_empty(self, client, test_engine):
        """无文件的 Agent 应返回空统计。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "kstats-empty")
        agent = await _create_agent(test_engine, ent_id, "空Agent")

        resp = await client.get(f"/api/v1/agents/{agent.id}/knowledge-stats")
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert data["totalFiles"] == 0
        assert data["totalChunks"] == 0
        assert data["fileTypeDistribution"] == []
        assert data["fileStatus"] == []
        assert data["knowledgeTimeline"] == []


class TestGetAgentOr404Merged:
    """3.3.6: 合并后的 _get_agent_or_404 测试。"""

    @pytest.mark.asyncio
    async def test_require_ready_default_rejects_processing(self, client, test_engine):
        """默认 require_ready=True 时，processing 状态的 agent 应被拒绝。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "merged-ready")
        agent = await _create_agent(test_engine, ent_id, "ProcessingAgent", status="processing")

        # chat 端点使用 require_ready=True（默认）
        resp = await client.post(
            f"/api/v1/agents/{agent.id}/chat",
            json={"content": "hello", "conversation_id": None},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "AGENT_STATUS_INVALID"

    @pytest.mark.asyncio
    async def test_require_ready_false_allows_processing(self, client, test_engine):
        """require_ready=False 时，processing 状态的 agent 应可访问（watch 端点）。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "merged-watch")
        agent = await _create_agent(test_engine, ent_id, "ProcessingAgent2", status="processing")

        # GET /agents/{id} 端点使用 require_ready=False（通过 _get_agent_or_404_for_watching）
        resp = await client.get(f"/api/v1/agents/{agent.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == agent.id

    @pytest.mark.asyncio
    async def test_require_ready_allows_ready(self, client, test_engine):
        """require_ready=True 时，ready 状态的 agent 应可访问。"""
        ent_id, _ = await _setup_enterprise_admin(client, test_engine, "merged-ok")
        agent = await _create_agent(test_engine, ent_id, "ReadyAgent", status="ready")

        resp = await client.get(f"/api/v1/agents/{agent.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == agent.id
