"""P3-5: app/services/agent_builder.py 覆盖率补充测试。"""
import pytest
from dataclasses import dataclass
from sqlalchemy import select

from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.models.skill import Skill
from app.services.agent_builder import _detect_file_types, _create_default_skills


@dataclass
class _FakeFile:
    file_type: str


class TestDetectFileTypes:
    """_detect_file_types 测试。"""

    def test_counts_types(self):
        files = [_FakeFile("document"), _FakeFile("document"), _FakeFile("image")]
        counts = _detect_file_types(files)
        assert counts == {"document": 2, "image": 1}

    def test_empty_list(self):
        assert _detect_file_types([]) == {}


class TestCreateDefaultSkills:
    """_create_default_skills 测试。"""

    @pytest.mark.asyncio
    async def test_creates_image_and_doc_skills(self, db_session):
        enterprise = Enterprise(name="测试企业")
        db_session.add(enterprise)
        await db_session.flush()
        await db_session.refresh(enterprise)

        agent = Agent(enterprise_id=enterprise.id, name="助手", description="")
        db_session.add(agent)
        await db_session.flush()
        await db_session.refresh(agent)

        file_types = {"image": 2, "video": 1, "document": 3, "spreadsheet": 1, "presentation": 1}
        skills = await _create_default_skills(db_session, agent.id, file_types)

        assert len(skills) == 4  # image + video + doc_summary + data_extraction
        stmt = select(Skill).where(Skill.agent_id == agent.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 4

    @pytest.mark.asyncio
    async def test_creates_no_skills_for_empty_types(self, db_session):
        enterprise = Enterprise(name="测试企业")
        db_session.add(enterprise)
        await db_session.flush()
        await db_session.refresh(enterprise)

        agent = Agent(enterprise_id=enterprise.id, name="助手", description="")
        db_session.add(agent)
        await db_session.flush()
        await db_session.refresh(agent)

        skills = await _create_default_skills(db_session, agent.id, {})
        assert skills == []
