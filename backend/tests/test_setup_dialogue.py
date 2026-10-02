"""P3-5: app/services/setup_dialogue.py 覆盖率补充测试。"""
import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.config import settings
from app.models.setup_session import SetupSession
from app.services.setup_dialogue import setup_dialogue_service


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    """将 UPLOAD_ROOT 指向临时目录。"""
    monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
    return tmp_path


async def _create_user(db_session):
    from app.models.user import User
    user = User(email="setup@test.com", password_hash="hash", name="测试")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


class TestStartSession:
    """start_session 测试。"""

    @pytest.mark.asyncio
    async def test_start_session_scans_folder(self, db_session, upload_root):
        user = await _create_user(db_session)
        (upload_root / "doc.txt").write_text("hello")

        with patch("app.services.setup_dialogue.scan_folder") as mock_scan:
            from dataclasses import dataclass
            @dataclass
            class F:
                name: str
                path: str
                size: int
                file_type: str
                extension: str
            mock_scan.return_value = [F("doc.txt", str(upload_root / "doc.txt"), 5, "document", ".txt")]
            result = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        assert "session_id" in result
        assert "message" in result
        assert "folder_info" in result

    @pytest.mark.asyncio
    async def test_start_session_scan_fails_gracefully(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", side_effect=FileNotFoundError("no dir")):
            result = await setup_dialogue_service.start_session(
                str(upload_root / "missing"), "ent-1", user.id, db_session
            )
        assert "session_id" in result
        assert "文件夹扫描失败" in result["folder_info"]


class TestSendMessage:
    """send_message 测试。"""

    @pytest.mark.asyncio
    async def test_send_message(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", return_value=[]):
            start = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        with patch("app.services.setup_dialogue.llm_service.chat", return_value="好的，请问更多细节"):
            result = await setup_dialogue_service.send_message(
                start["session_id"], "我想做客服助手", db_session
            )

        assert result["message"] == "好的，请问更多细节"
        assert result["history_length"] == 3  # assistant + user + assistant

    @pytest.mark.asyncio
    async def test_send_message_session_not_found(self, db_session):
        with pytest.raises(ValueError, match="会话不存在或已过期"):
            await setup_dialogue_service.send_message("nonexistent", "hi", db_session)


class TestGetPlan:
    """get_plan 测试。"""

    @pytest.mark.asyncio
    async def test_get_plan_returns_existing(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", return_value=[]):
            start = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        plan = {"agent_name": "助手", "estimated_time": 60}
        stmt = select(SetupSession).where(SetupSession.id == start["session_id"])
        result = await db_session.execute(stmt)
        session = result.scalar_one()
        session.plan = plan
        await db_session.commit()

        result_plan = await setup_dialogue_service.get_plan(start["session_id"], db_session)
        assert result_plan["agent_name"] == "助手"

    @pytest.mark.asyncio
    async def test_get_plan_generates_new(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", return_value=[]):
            start = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        plan_json = '{"agent_name": "客服助手", "estimated_time": 120}'
        with patch("app.services.setup_dialogue.llm_service.chat", return_value=plan_json):
            result = await setup_dialogue_service.get_plan(start["session_id"], db_session)

        assert result["agent_name"] == "客服助手"
        assert result["session_id"] == start["session_id"]

    @pytest.mark.asyncio
    async def test_get_plan_invalid_json_fallback(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", return_value=[]):
            start = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        with patch("app.services.setup_dialogue.llm_service.chat", return_value="plain text"):
            result = await setup_dialogue_service.get_plan(start["session_id"], db_session)

        assert "raw_plan" in result

    @pytest.mark.asyncio
    async def test_get_plan_session_not_found(self, db_session):
        with pytest.raises(ValueError, match="会话不存在或已过期"):
            await setup_dialogue_service.get_plan("nonexistent", db_session)


class TestConfirmPlan:
    """confirm_plan 测试。"""

    @pytest.mark.asyncio
    async def test_confirm_plan(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", return_value=[]):
            start = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        plan_json = '{"agent_name": "客服助手"}'
        with patch("app.services.setup_dialogue.llm_service.chat", return_value=plan_json):
            await setup_dialogue_service.get_plan(start["session_id"], db_session)

        result = await setup_dialogue_service.confirm_plan(
            start["session_id"], True, {"agent_name": "新名字"}, db_session
        )
        assert result["status"] == "confirmed"
        assert result["plan"]["agent_name"] == "新名字"

    @pytest.mark.asyncio
    async def test_reject_plan(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", return_value=[]):
            start = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        plan_json = '{"agent_name": "客服助手"}'
        with patch("app.services.setup_dialogue.llm_service.chat", return_value=plan_json):
            await setup_dialogue_service.get_plan(start["session_id"], db_session)

        result = await setup_dialogue_service.confirm_plan(
            start["session_id"], False, None, db_session
        )
        assert result["status"] == "rejected"


class TestGetSession:
    """get_session 测试。"""

    @pytest.mark.asyncio
    async def test_get_session_exists(self, db_session, upload_root):
        user = await _create_user(db_session)
        with patch("app.services.setup_dialogue.scan_folder", return_value=[]):
            start = await setup_dialogue_service.start_session(
                str(upload_root), "ent-1", user.id, db_session
            )

        result = await setup_dialogue_service.get_session(start["session_id"], db_session)
        assert result is not None
        assert result["session_id"] == start["session_id"]

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, db_session):
        result = await setup_dialogue_service.get_session("nonexistent", db_session)
        assert result is None
