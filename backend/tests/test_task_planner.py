"""P3-5: app/services/task_planner.py 覆盖率补充测试。"""
import pytest
import pytest_asyncio
from unittest.mock import patch

from app.models.user import User
from app.services.task_planner import task_planner, TaskStatus


async def _create_user(db_session):
    user = User(email="planner@test.com", password_hash="hash", name="测试")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


class TestCreatePlan:
    """create_plan 测试。"""

    @pytest.mark.asyncio
    async def test_create_plan_with_llm_json(self, db_session):
        user = await _create_user(db_session)
        llm_response = '{"steps": [{"title": "步骤1", "description": "描述", "tool": "llm_service"}], "estimated_time": "1h", "suggestions": ["建议"]}'

        with patch("app.services.task_planner.llm_service.chat", return_value=llm_response):
            result = await task_planner.create_plan("目标", user.id, db_session)

        assert result["goal"] == "目标"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["status"] == TaskStatus.PENDING.value
        assert result["suggestions"] == ["建议"]
        assert "id" in result

    @pytest.mark.asyncio
    async def test_create_plan_invalid_json_uses_defaults(self, db_session):
        user = await _create_user(db_session)

        with patch("app.services.task_planner.llm_service.chat", return_value="不是JSON"):
            result = await task_planner.create_plan("目标", user.id, db_session)

        assert result["goal"] == "目标"
        assert len(result["steps"]) >= 4
        assert result["steps"][0]["status"] == TaskStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_create_plan_llm_exception_uses_defaults(self, db_session):
        user = await _create_user(db_session)

        with patch("app.services.task_planner.llm_service.chat", side_effect=RuntimeError("LLM失败")):
            result = await task_planner.create_plan("目标", user.id, db_session)

        assert result["goal"] == "目标"
        assert len(result["steps"]) >= 4


class TestGetPlan:
    """get_plan / list_plans 测试。"""

    @pytest.mark.asyncio
    async def test_get_plan_exists(self, db_session):
        user = await _create_user(db_session)
        with patch("app.services.task_planner.llm_service.chat", return_value="{}"):
            created = await task_planner.create_plan("目标", user.id, db_session)

        result = await task_planner.get_plan(created["id"], db_session)
        assert result is not None
        assert result["id"] == created["id"]

    @pytest.mark.asyncio
    async def test_get_plan_not_found(self, db_session):
        result = await task_planner.get_plan("nonexistent", db_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_plans(self, db_session):
        user = await _create_user(db_session)
        with patch("app.services.task_planner.llm_service.chat", return_value="{}"):
            await task_planner.create_plan("目标1", user.id, db_session)
            await task_planner.create_plan("目标2", user.id, db_session)

        plans = await task_planner.list_plans(user.id, db_session)
        assert len(plans) == 2
        assert plans[0]["goal"] == "目标2"


class TestStepLifecycle:
    """步骤状态流转测试。"""

    @pytest_asyncio.fixture
    async def plan(self, db_session):
        user = await _create_user(db_session)
        llm_response = '{"steps": [{"title": "步骤1", "description": "描述", "tool": "llm_service"}, {"title": "步骤2", "description": "描述2", "tool": "folder_scanner"}]}'
        with patch("app.services.task_planner.llm_service.chat", return_value=llm_response):
            result = await task_planner.create_plan("目标", user.id, db_session)
        return result

    @pytest.mark.asyncio
    async def test_start_step(self, db_session, plan):
        step = await task_planner.start_step(plan["id"], plan["steps"][0]["step_id"], db_session)
        assert step is not None
        assert step["status"] == TaskStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_start_step_not_found(self, db_session, plan):
        result = await task_planner.start_step(plan["id"], "no-such-step", db_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_complete_step(self, db_session, plan):
        step_id = plan["steps"][0]["step_id"]
        step = await task_planner.complete_step(plan["id"], step_id, "完成输出", db_session)
        assert step is not None
        assert step["status"] == TaskStatus.COMPLETED.value
        assert step["output"] == "完成输出"

    @pytest.mark.asyncio
    async def test_complete_all_steps_marks_plan_completed(self, db_session, plan):
        for s in plan["steps"]:
            step = await task_planner.complete_step(plan["id"], s["step_id"], "ok", db_session)
            print("completed step", s["step_id"], "result", step)

        updated = await task_planner.get_plan(plan["id"], db_session)
        print("updated plan", updated)
        assert updated["status"] == TaskStatus.COMPLETED.value
        assert updated["progress"] == 100

    @pytest.mark.asyncio
    async def test_fail_step(self, db_session, plan):
        step_id = plan["steps"][0]["step_id"]
        step = await task_planner.fail_step(plan["id"], step_id, "出错了", db_session)
        assert step is not None
        assert step["status"] == TaskStatus.FAILED.value
        assert step["error"] == "出错了"

    @pytest.mark.asyncio
    async def test_fail_step_not_found(self, db_session, plan):
        result = await task_planner.fail_step(plan["id"], "no-such-step", "err", db_session)
        assert result is None
