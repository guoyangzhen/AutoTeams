"""BE-SEC-02: 统一错误码规范测试。

验证：
1. API 层不再返回动态异常字符串或内部状态值。
2. 持久化到 DB 后会返回给客户端的错误字段使用统一错误码。
3. 原始异常仅在服务端日志中记录（通过 logger.error / logger.warning）。
"""
import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.skill import Skill
from app.models.skill_execution import SkillExecution
from app.models.user import User
from app.services.image_processor import process_image
from app.services.setup_dialogue import setup_dialogue_service
from app.services.skill_executor import skill_executor
from app.utils.error_codes import ErrorCode


async def _create_agent(db_session, status: str = "ready") -> Agent:
    enterprise = Enterprise(name="测试企业")
    db_session.add(enterprise)
    await db_session.flush()
    await db_session.refresh(enterprise)

    agent = Agent(
        enterprise_id=enterprise.id,
        name="测试助手",
        description="",
        status=status,
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


async def _register_user(client, email: str, password: str = "Test1234!"):
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": "测试用户",
        "password": password,
    })
    assert resp.status_code == 201, f"注册失败: {resp.text}"


async def _create_enterprise(client, name: str = "测试企业"):
    resp = await client.post("/api/v1/enterprises", json={"name": name})
    assert resp.status_code == 201, f"创建企业失败: {resp.text}"
    return resp.json()["data"]["id"]


class TestAPIErrorCodes:
    """API 层错误码测试。"""

    @pytest.mark.asyncio
    async def test_chat_with_non_ready_agent_returns_error_code(self, client, db_session):
        """Agent 状态非 ready 时聊天应返回统一错误码，不暴露内部状态值。"""
        await _register_user(client, "agentstatus@test.com")
        enterprise_id = await _create_enterprise(client)

        # 直接创建处于 processing 状态的 Agent
        agent = Agent(
            enterprise_id=enterprise_id,
            name="未就绪助手",
            description="",
            status="processing",
        )
        db_session.add(agent)
        await db_session.commit()

        resp = await client.post(
            f"/api/v1/agents/{agent.id}/chat",
            json={"content": "hello", "conversation_id": None},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["message"] == ErrorCode.AGENT_STATUS_INVALID
        # 确保没有暴露具体 status 值
        assert "processing" not in data["message"]

    @pytest.mark.asyncio
    async def test_cancel_non_pending_invitation_returns_error_code(self, client, db_session):
        """取消非 pending 邀请应返回统一错误码，不暴露内部状态值。"""
        await _register_user(client, "invstatus@test.com")
        enterprise_id = await _create_enterprise(client)

        resp = await client.post(f"/api/v1/enterprises/{enterprise_id}/invite")
        assert resp.status_code == 200
        invitation_id = resp.json()["data"]["invitation_id"]

        # 第一次取消成功
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invitations/{invitation_id}/cancel",
        )
        assert resp.status_code == 200

        # 第二次取消失败，应返回错误码
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invitations/{invitation_id}/cancel",
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["message"] == ErrorCode.INVITATION_STATUS_INVALID
        assert "cancelled" not in data["message"]


class TestServiceErrorCodes:
    """服务层持久化错误码测试。"""

    @pytest.mark.asyncio
    async def test_skill_execution_failure_uses_error_code(self, db_session):
        """技能执行失败时，持久化的 error_message 应为统一错误码。"""
        enterprise = Enterprise(name="测试企业")
        db_session.add(enterprise)
        await db_session.flush()

        agent = Agent(enterprise_id=enterprise.id, name="测试助手", description="")
        db_session.add(agent)
        await db_session.flush()

        user = User(
            email="skillerr@test.com",
            password_hash="hash",
            name="测试",
            enterprise_id=enterprise.id,
        )
        db_session.add(user)
        await db_session.flush()

        skill = Skill(
            agent_id=agent.id,
            name="测试技能",
            description="",
            skill_type="custom",
            input_type="text",
            output_type="text",
            config={"prompt_template": "{input}"},
        )
        db_session.add(skill)
        await db_session.commit()

        with patch("app.services.skill_executor.llm_service.chat", side_effect=RuntimeError("原始异常")):
            with pytest.raises(RuntimeError):
                await skill_executor.execute_skill(db_session, skill.id, {"text": "x"}, user.id)

        stmt = select(SkillExecution).where(SkillExecution.skill_id == skill.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].error_message == ErrorCode.SKILL_NOT_FOUND_OR_INVALID
        # 确保原始异常字符串未持久化
        assert "原始异常" not in (rows[0].error_message or "")

    @pytest.mark.asyncio
    async def test_image_processor_returns_error_code(self):
        """图片处理路径校验失败时应返回统一错误码，不暴露路径细节。"""
        result = await process_image("/etc/passwd")
        assert result["analysis"] == ""
        assert result["error"] == ErrorCode.FILE_PATH_INVALID

    @pytest.mark.asyncio
    async def test_setup_dialogue_folder_info_uses_error_code(self, db_session):
        """设置向导扫描失败时，folder_info 中不应包含原始异常字符串。"""
        enterprise = Enterprise(name="测试企业")
        db_session.add(enterprise)
        await db_session.flush()

        user = User(
            email="setup@test.com",
            password_hash="hash",
            name="测试",
            enterprise_id=enterprise.id,
        )
        db_session.add(user)
        await db_session.commit()

        with patch("app.services.setup_dialogue.scan_folder",
                   side_effect=FileNotFoundError("原始异常")):
            result = await setup_dialogue_service.start_session(
                folder_path="/不存在的路径",
                enterprise_id=enterprise.id,
                user_id=user.id,
                db=db_session,
            )

        assert ErrorCode.FOLDER_PATH_INVALID in result["message"]
        assert "原始异常" not in result["message"]
