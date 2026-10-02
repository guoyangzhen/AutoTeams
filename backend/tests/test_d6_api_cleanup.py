"""D6: API 端点清理与对话增强 测试。

覆盖：
- R5: loop.py 已废弃端点硬删除后应返回 404
- M11: /skills/upload 的 package_url URL 拉取能力
- M12: /conversations/messages/{id}/implicit-feedback 隐式满意度采集
"""
import io
import os
import socket
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.enterprise import Enterprise
from app.models.message import Message


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()


async def _create_agent(test_engine, name: str = "测试企业", enterprise_id: str | None = None) -> tuple[str, str]:
    """创建测试用 Enterprise + Agent，返回 (enterprise_id, agent_id)。

    enterprise_id 指定时复用该企业（若已存在则不重复创建，用于与
    enterprise_authenticated_client 绑定到同一企业）；否则自动生成随机企业。
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        if enterprise_id:
            ent = await s.get(Enterprise, enterprise_id)
            if ent is None:
                ent = Enterprise(id=enterprise_id, name=name)
                s.add(ent)
                await s.commit()
        else:
            ent = Enterprise(name=name)
            s.add(ent)
            await s.commit()
        agent = Agent(enterprise_id=ent.id, name="测试Agent", status="ready")
        s.add(agent)
        await s.commit()
        return str(ent.id), str(agent.id)


async def _create_message(test_engine, user_id: str, agent_id: str, role: str = "assistant") -> tuple[str, str]:
    """创建对话 + 消息，返回 (conversation_id, message_id)。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        conv = Conversation(user_id=user_id, agent_id=agent_id, title="测试对话")
        s.add(conv)
        await s.commit()
        msg = Message(
            conversation_id=conv.id,
            role=role,
            content="测试内容",
        )
        s.add(msg)
        await s.commit()
        return str(conv.id), str(msg.id)


async def _set_message_satisfaction(test_engine, message_id: str, satisfaction: str) -> None:
    """直接在 DB 中设置 message.satisfaction（用于测试 override 规则）。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        result = await s.execute(select(Message).where(Message.id == message_id))
        msg = result.scalar_one_or_none()
        if msg:
            msg.satisfaction = satisfaction
            await s.commit()


# ============================================================
# R5: loop.py deprecation headers
# ============================================================

class TestR5LoopHardDeleted:
    """R5: /loop/{id}/feedback、/knowledge-gaps、/rag-evaluation 已硬删除，应返回 404。"""

    @pytest.mark.asyncio
    async def test_feedback_endpoint_returns_404(self, authenticated_client, test_engine):
        """GET /loop/{agent_id}/feedback 应返回 404。"""
        _, agent_id = await _create_agent(test_engine)
        resp = await authenticated_client.get(f"/api/v1/loop/{agent_id}/feedback")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_knowledge_gaps_endpoint_returns_404(self, authenticated_client, test_engine):
        """GET /loop/{agent_id}/knowledge-gaps 应返回 404。"""
        _, agent_id = await _create_agent(test_engine)
        resp = await authenticated_client.get(f"/api/v1/loop/{agent_id}/knowledge-gaps")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rag_evaluation_endpoint_returns_404(self, authenticated_client, test_engine):
        """GET /loop/{agent_id}/rag-evaluation 应返回 404。"""
        _, agent_id = await _create_agent(test_engine)
        resp = await authenticated_client.get(f"/api/v1/loop/{agent_id}/rag-evaluation")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_stats_endpoint_still_exists(self, enterprise_authenticated_client, test_engine):
        """GET /loop/{agent_id}/stats 保留端点，存在 Agent 时应返回 200。"""
        _, agent_id = await _create_agent(test_engine, enterprise_id="ent-test")
        resp = await enterprise_authenticated_client.get(f"/api/v1/loop/{agent_id}/stats")
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") is None

    @pytest.mark.asyncio
    async def test_insights_endpoint_still_exists(self, enterprise_authenticated_client, test_engine):
        """GET /loop/{agent_id}/insights 聚合端点，存在 Agent 时应返回 200。"""
        _, agent_id = await _create_agent(test_engine, enterprise_id="ent-test")
        resp = await enterprise_authenticated_client.get(f"/api/v1/loop/{agent_id}/insights")
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") is None


# ============================================================
# R6: planner.py 已硬删除，所有 /planner/* 端点应返回 404
# ============================================================

class TestR6PlannerHardDeleted:
    """R6: /planner/* 端点已硬删除，应统一返回 404。"""

    @pytest.mark.asyncio
    async def test_planner_list_returns_404(self, authenticated_client):
        """GET /planner 应返回 404。"""
        resp = await authenticated_client.get("/api/v1/planner")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_planner_create_returns_404(self, authenticated_client):
        """POST /planner/create 应返回 404。"""
        resp = await authenticated_client.post("/api/v1/planner/create", json={"goal": "测试目标"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_planner_get_returns_404(self, authenticated_client):
        """GET /planner/{plan_id} 应返回 404。"""
        resp = await authenticated_client.get("/api/v1/planner/nonexistent-plan-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_planner_step_start_returns_404(self, authenticated_client):
        """POST /planner/step/start 应返回 404。"""
        resp = await authenticated_client.post(
            "/api/v1/planner/step/start",
            json={"plan_id": "p1", "step_id": "s1"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_planner_step_complete_returns_404(self, authenticated_client):
        """POST /planner/step/complete 应返回 404。"""
        resp = await authenticated_client.post(
            "/api/v1/planner/step/complete",
            json={"plan_id": "p1", "step_id": "s1", "output": "完成输出"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_planner_step_fail_returns_404(self, authenticated_client):
        """POST /planner/step/fail 应返回 404。"""
        resp = await authenticated_client.post(
            "/api/v1/planner/step/fail",
            json={"plan_id": "p1", "step_id": "s1", "error": "测试失败"},
        )
        assert resp.status_code == 404


# ============================================================
# M11: skills/upload URL fetch (package_url)
# ============================================================

class TestM11SkillsUploadUrlFetch:
    """M11: /skills/upload 增加 package_url URL 拉取能力。"""

    @pytest.mark.asyncio
    async def test_upload_requires_file_or_url(self, authenticated_client):
        """既未提供 file 也未提供 package_url 应返回 400。"""
        resp = await authenticated_client.post("/api/v1/skills/upload")
        assert resp.status_code == 400
        assert resp.json()["message"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_upload_url_rejects_http_scheme(self, authenticated_client):
        """package_url 使用 http:// 应返回 400 SKILL_PACKAGE_URL_INVALID。"""
        resp = await authenticated_client.post(
            "/api/v1/skills/upload",
            data={"package_url": "http://example.com/file.txt"},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "SKILL_PACKAGE_URL_INVALID"

    @pytest.mark.asyncio
    async def test_upload_url_rejects_localhost(self, authenticated_client):
        """package_url 指向 localhost 应返回 400 SKILL_PACKAGE_URL_INVALID（SSRF 防护）。"""
        resp = await authenticated_client.post(
            "/api/v1/skills/upload",
            data={"package_url": "https://localhost/file.txt"},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "SKILL_PACKAGE_URL_INVALID"

    @pytest.mark.asyncio
    async def test_upload_url_rejects_private_ip(self, authenticated_client):
        """package_url 指向私有 IP 应返回 400 SKILL_PACKAGE_URL_INVALID（SSRF 防护）。"""
        resp = await authenticated_client.post(
            "/api/v1/skills/upload",
            data={"package_url": "https://192.168.1.1/file.txt"},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "SKILL_PACKAGE_URL_INVALID"

    @pytest.mark.asyncio
    async def test_upload_url_rejects_missing_host(self, authenticated_client):
        """package_url 缺少 host 应返回 400。"""
        resp = await authenticated_client.post(
            "/api/v1/skills/upload",
            data={"package_url": "https:///file.txt"},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "SKILL_PACKAGE_URL_INVALID"

    @pytest.mark.asyncio
    async def test_upload_url_fetches_single_file(self, authenticated_client, upload_root):
        """package_url 拉取单文件应返回与 multipart 一致的响应 shape。"""
        # 模拟 httpx.AsyncClient.stream 返回的 response
        file_content = b"hello world from url"

        def fake_stream(self, method, url, **kwargs):
            class FakeResponse:
                status_code = 200
                headers = {"content-type": "text/plain"}

                async def aiter_bytes(self, chunk_size=None):
                    yield file_content

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            return FakeResponse()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.stream = lambda method, url, **kw: fake_stream(mock_instance, method, url, **kw)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            # 还需要 patch socket.getaddrinfo 避免真实 DNS 解析 example.com
            with patch("socket.getaddrinfo") as mock_dns:
                mock_dns.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
                ]
                resp = await authenticated_client.post(
                    "/api/v1/skills/upload",
                    data={"package_url": "https://example.com/test.txt"},
                )

        assert resp.status_code == 200, f"响应: {resp.text}"
        data = resp.json()["data"]
        assert data["original_name"] == "test.txt"
        assert data["file_size"] == len(file_content)
        assert data["file_type"] == "txt"
        assert data["file_path"]

        # 清理
        if os.path.exists(data["file_path"]):
            os.remove(data["file_path"])

    @pytest.mark.asyncio
    async def test_upload_url_fetches_zip(self, authenticated_client, upload_root):
        """package_url 拉取 zip 包应解压并返回 files 列表。"""
        # 构造一个 zip 文件内容
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("file1.txt", "content1")
            zf.writestr("file2.txt", "content2")
            # 跳过目录条目和 macOS 元数据
            zf.writestr("subdir/", "")
            zf.writestr("__MACOSX/_file1.txt", "should be skipped")
        zip_bytes = buf.getvalue()

        def fake_stream(self, method, url, **kwargs):
            class FakeResponse:
                status_code = 200
                headers = {"content-type": "application/zip"}

                async def aiter_bytes(self, chunk_size=None):
                    yield zip_bytes

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

            return FakeResponse()

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.stream = lambda method, url, **kw: fake_stream(mock_instance, method, url, **kw)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("socket.getaddrinfo") as mock_dns:
                mock_dns.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
                ]
                resp = await authenticated_client.post(
                    "/api/v1/skills/upload",
                    data={"package_url": "https://example.com/package.zip"},
                )

        assert resp.status_code == 200, f"响应: {resp.text}"
        data = resp.json()["data"]
        assert data["package_url"] == "https://example.com/package.zip"
        assert data["file_count"] == 2  # 只算 file1.txt 和 file2.txt
        assert len(data["files"]) == 2
        for f in data["files"]:
            assert f["original_name"] in ("file1.txt", "file2.txt")
            assert f["file_path"]
            if os.path.exists(f["file_path"]):
                os.remove(f["file_path"])

    @pytest.mark.asyncio
    async def test_upload_multipart_still_works(self, authenticated_client, upload_root):
        """M11 改造后原有 multipart 上传仍应正常工作（向后兼容）。"""
        content = b"multipart still works"
        resp = await authenticated_client.post(
            "/api/v1/skills/upload",
            files={"file": ("compat.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200, f"响应: {resp.text}"
        data = resp.json()["data"]
        assert data["original_name"] == "compat.txt"
        assert data["file_size"] == len(content)
        assert data["file_type"] == "txt"
        if os.path.exists(data["file_path"]):
            os.remove(data["file_path"])


# ============================================================
# M12: implicit satisfaction collection
# ============================================================

class TestM12ImplicitFeedback:
    """M12: /conversations/messages/{id}/implicit-feedback 隐式满意度采集。"""

    @pytest.mark.asyncio
    async def test_implicit_feedback_regenerate(self, authenticated_client, test_engine):
        """signal=regenerate 应将 satisfaction 设为 implicit:unsatisfied:regenerate。"""
        # authenticated_client 已经注册了一个用户，但我们需要 user_id
        # 通过查询 DB 获取
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            assert user is not None
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "regenerate"},
        )
        assert resp.status_code == 200, f"响应: {resp.text}"
        data = resp.json()["data"]
        assert data["satisfaction"] == "implicit:unsatisfied:regenerate"

    @pytest.mark.asyncio
    async def test_implicit_feedback_copy(self, authenticated_client, test_engine):
        """signal=copy 应将 satisfaction 设为 implicit:satisfied:copy。"""
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "copy"},
        )
        assert resp.status_code == 200, f"响应: {resp.text}"
        data = resp.json()["data"]
        assert data["satisfaction"] == "implicit:satisfied:copy"

    @pytest.mark.asyncio
    async def test_implicit_feedback_dwell_short(self, authenticated_client, test_engine):
        """signal=dwell, dwell_seconds=2 应将 satisfaction 设为 implicit:unsatisfied:dwell。"""
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "dwell", "dwell_seconds": 2},
        )
        assert resp.status_code == 200, f"响应: {resp.text}"
        data = resp.json()["data"]
        assert data["satisfaction"] == "implicit:unsatisfied:dwell"

    @pytest.mark.asyncio
    async def test_implicit_feedback_dwell_long_no_change(self, authenticated_client, test_engine):
        """signal=dwell, dwell_seconds=5 应不修改 satisfaction（停留时长足够）。"""
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "dwell", "dwell_seconds": 5},
        )
        assert resp.status_code == 200, f"响应: {resp.text}"
        data = resp.json()["data"]
        assert data["satisfaction"] is None  # 未修改

    @pytest.mark.asyncio
    async def test_implicit_feedback_dwell_missing_seconds(self, authenticated_client, test_engine):
        """signal=dwell 但缺少 dwell_seconds 应返回 400。"""
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "dwell"},  # 缺少 dwell_seconds
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_implicit_feedback_no_override_explicit(self, authenticated_client, test_engine):
        """已显式设置 satisfaction=satisfied 时，隐式反馈不应覆盖。"""
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)
        await _set_message_satisfaction(test_engine, message_id, "satisfied")

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "regenerate"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["satisfaction"] == "satisfied"  # 未被覆盖

    @pytest.mark.asyncio
    async def test_implicit_feedback_overrides_previous_implicit(self, authenticated_client, test_engine):
        """已设置 implicit:unsatisfied:regenerate 时，新的 copy 信号应覆盖。"""
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)
        await _set_message_satisfaction(test_engine, message_id, "implicit:unsatisfied:regenerate")

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "copy"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["satisfaction"] == "implicit:satisfied:copy"  # 被覆盖

    @pytest.mark.asyncio
    async def test_implicit_feedback_message_not_found(self, authenticated_client):
        """不存在的 message_id 应返回 404。"""
        resp = await authenticated_client.post(
            "/api/v1/conversations/messages/nonexistent-message-id/implicit-feedback",
            json={"signal": "regenerate"},
        )
        assert resp.status_code == 404
        assert resp.json()["message"] == "MESSAGE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_implicit_feedback_unauthorized(self, client):
        """未认证访问应返回 401/403。"""
        resp = await client.post(
            "/api/v1/conversations/messages/any-id/implicit-feedback",
            json={"signal": "regenerate"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_implicit_feedback_invalid_signal(self, authenticated_client, test_engine):
        """无效 signal 值应返回 422（Pydantic 校验）。"""
        from app.models.user import User
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email == "auth@test.com"))
            user = result.scalar_one_or_none()
            user_id = str(user.id)

        _, agent_id = await _create_agent(test_engine)
        _, message_id = await _create_message(test_engine, user_id, agent_id)

        resp = await authenticated_client.post(
            f"/api/v1/conversations/messages/{message_id}/implicit-feedback",
            json={"signal": "invalid_signal"},
        )
        assert resp.status_code == 422
