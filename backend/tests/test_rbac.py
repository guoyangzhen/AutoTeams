"""P3-5: app/utils/rbac.py 覆盖率补充测试。"""
import pytest
from fastapi import HTTPException

from app.models.user import User
from app.utils.rbac import require_admin


class MockUser:
    """轻量级用户对象，用于 RBAC 测试。"""

    def __init__(self, enterprise_id=None, role="member", is_active=True):
        self.enterprise_id = enterprise_id
        self.role = role
        self.is_active = is_active


class TestRequireAdmin:
    """require_admin 测试。"""

    @pytest.mark.asyncio
    async def test_super_admin_allowed(self):
        # 安全修复：系统超管必须是 enterprise_id=None + role="admin"
        user = MockUser(enterprise_id=None, role="admin")
        result = await require_admin(user)
        assert result == user

    @pytest.mark.asyncio
    async def test_enterprise_admin_allowed(self):
        user = MockUser(enterprise_id="ent-1", role="admin")
        result = await require_admin(user)
        assert result == user

    @pytest.mark.asyncio
    async def test_member_rejected(self):
        user = MockUser(enterprise_id="ent-1", role="member")
        with pytest.raises(HTTPException) as exc:
            await require_admin(user)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_public_register_member_rejected(self):
        """安全修复回归测试：/auth/register 创建的 enterprise_id=None + role=member 用户
        不能再绕过 admin 权限检查（历史漏洞：require_admin 自动放行 enterprise_id=None）。
        """
        user = MockUser(enterprise_id=None, role="member")
        with pytest.raises(HTTPException) as exc:
            await require_admin(user)
        assert exc.value.status_code == 403
