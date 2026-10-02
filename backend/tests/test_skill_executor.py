"""P3-5: app/services/skill_executor.py 覆盖率补充测试。"""
import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.models.skill import Skill
from app.models.skill_execution import SkillExecution
from app.services.skill_executor import skill_executor
from app.utils.error_codes import ErrorCode


async def _create_context(db_session, user_enterprise=None):
    enterprise = Enterprise(name="测试企业")
    db_session.add(enterprise)
    await db_session.flush()
    await db_session.refresh(enterprise)

    agent = Agent(enterprise_id=enterprise.id, name="测试助手", description="")
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)

    user = User(
        email="skill@test.com",
        password_hash="hash",
        name="测试",
        enterprise_id=user_enterprise if user_enterprise is not None else enterprise.id,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    return enterprise, agent, user


async def _create_skill(db_session, agent, skill_type, config=None):
    skill = Skill(
        agent_id=agent.id,
        name="测试技能",
        description="描述",
        skill_type=skill_type,
        input_type="text",
        output_type="text",
        config=config or {},
    )
    db_session.add(skill)
    await db_session.commit()
    await db_session.refresh(skill)
    return skill


class TestSkillExecution:
    """技能执行主流程测试。"""

    @pytest.mark.asyncio
    async def test_execute_text_generation(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "text_generation", {"prompt_template": "生成：{input}"})

        with patch("app.services.skill_executor.llm_service.chat", return_value="生成结果"):
            result = await skill_executor.execute_skill(db_session, skill.id, {"text": "hello"}, user.id)

        assert result["status"] == "success"
        assert result["output_data"]["generated_text"] == "生成结果"

        stmt = select(SkillExecution).where(SkillExecution.skill_id == skill.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "success"

    @pytest.mark.asyncio
    async def test_execute_text_summarization(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "text_summarization")

        with patch("app.services.skill_executor.llm_service.chat", return_value="摘要"):
            result = await skill_executor.execute_skill(db_session, skill.id, {"text": "长文本"}, user.id)

        assert result["output_data"]["summary"] == "摘要"

    @pytest.mark.asyncio
    async def test_execute_text_classification(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "text_classification", {"categories": ["A", "B"]})

        with patch("app.services.skill_executor.llm_service.chat", return_value="A"):
            result = await skill_executor.execute_skill(db_session, skill.id, {"text": "x"}, user.id)

        assert result["output_data"]["classification"] == "A"

    @pytest.mark.asyncio
    async def test_execute_data_extraction(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "data_extraction", {"fields": ["名称"]})

        with patch("app.services.skill_executor.llm_service.chat", return_value='{"名称": "x"}'):
            result = await skill_executor.execute_skill(db_session, skill.id, {"text": "文本"}, user.id)

        assert "extracted_data" in result["output_data"]

    @pytest.mark.asyncio
    async def test_execute_translation(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "translation", {"target_language": "英文"})

        with patch("app.services.skill_executor.llm_service.chat", return_value="hello"):
            result = await skill_executor.execute_skill(db_session, skill.id, {"text": "你好"}, user.id)

        assert result["output_data"]["translation"] == "hello"

    @pytest.mark.asyncio
    async def test_execute_code_generation(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "code_generation", {"language": "Python"})

        with patch("app.services.skill_executor.llm_service.chat", return_value="print(1)"):
            result = await skill_executor.execute_skill(db_session, skill.id, {"description": "打印1"}, user.id)

        assert result["output_data"]["code"] == "print(1)"

    @pytest.mark.asyncio
    async def test_execute_custom(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "custom", {"prompt_template": "回答：{input}"})

        with patch("app.services.skill_executor.llm_service.chat", return_value="ok"):
            result = await skill_executor.execute_skill(db_session, skill.id, {"text": "问"}, user.id)

        assert result["output_data"]["result"] == "ok"

    @pytest.mark.asyncio
    async def test_execute_skill_not_found(self, db_session):
        _, _, user = await _create_context(db_session)
        with pytest.raises(ValueError, match="技能不存在"):
            await skill_executor.execute_skill(db_session, "nonexistent", {}, user.id)

    @pytest.mark.asyncio
    async def test_execute_unsupported_skill_type(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "unknown_type")

        with pytest.raises(ValueError, match="不支持的技能类型"):
            await skill_executor.execute_skill(db_session, skill.id, {}, user.id)

    @pytest.mark.asyncio
    async def test_execute_permission_denied(self, db_session):
        _, agent, user = await _create_context(db_session, user_enterprise="other-ent-id")
        skill = await _create_skill(db_session, agent, "custom", {"prompt_template": "{input}"})

        with pytest.raises(PermissionError, match="无权执行此技能"):
            await skill_executor.execute_skill(db_session, skill.id, {"text": "x"}, user.id)

    @pytest.mark.asyncio
    async def test_execute_failure_persists_record(self, db_session):
        _, agent, user = await _create_context(db_session)
        skill = await _create_skill(db_session, agent, "custom", {"prompt_template": "{input}"})

        with patch("app.services.skill_executor.llm_service.chat", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                await skill_executor.execute_skill(db_session, skill.id, {"text": "x"}, user.id)

        stmt = select(SkillExecution).where(SkillExecution.skill_id == skill.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        # BE-SEC-02: 持久化的 error_message 应为统一错误码，不暴露原始异常字符串
        assert rows[0].error_message == ErrorCode.SKILL_NOT_FOUND_OR_INVALID
