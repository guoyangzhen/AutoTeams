from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.cognition import _verify_enterprise_access, _verify_enterprise_admin
from app.api.compiler import _verify_enterprise_admin as verify_compiler_admin
from app.api.enterprise import _verify_enterprise_admin as verify_enterprise_admin
from app.api.agents._helpers import _get_agent_or_404
from app.api.loop import _verify_agent_access
from app.api.process import _verify_task_access


def make_user(enterprise_id=None, role="member"):
    return SimpleNamespace(id="user-1", enterprise_id=enterprise_id, role=role)


@pytest.mark.parametrize(
    "guard",
    [verify_enterprise_admin, verify_compiler_admin, _verify_enterprise_admin],
)
def test_public_registered_member_cannot_bypass_admin_guards(guard):
    with pytest.raises(HTTPException) as exc_info:
        guard(make_user(), "enterprise-2")
    assert exc_info.value.status_code == 403


def test_public_registered_member_cannot_read_enterprise_cognition():
    with pytest.raises(HTTPException) as exc_info:
        _verify_enterprise_access(make_user(), "enterprise-2")
    assert exc_info.value.status_code == 403


def test_system_admin_can_cross_enterprise_guards():
    admin = make_user(role="admin")
    verify_enterprise_admin(admin, "enterprise-2")
    verify_compiler_admin(admin, "enterprise-2")
    _verify_enterprise_admin(admin, "enterprise-2")
    _verify_enterprise_access(admin, "enterprise-2")


@pytest.mark.asyncio
async def test_public_registered_member_cannot_query_agent(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await _get_agent_or_404(db_session, "agent-1", make_user(), require_ready=False)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_public_registered_member_cannot_query_loop_agent(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await _verify_agent_access(db_session, "agent-1", make_user())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_public_registered_member_cannot_query_processing_task(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await _verify_task_access(db_session, "task-1", make_user())
    assert exc_info.value.status_code == 403
