"""P2-2 企业管理完善测试。

覆盖：
1. 成员列表 + 移除
2. 角色变更（admin ↔ member）
3. 企业删除（软删除）
4. 邀请链接使用次数限制
5. 邀请记录表
6. Schema 字段验证
"""
import pytest
from sqlalchemy import select

from app.models.enterprise import Enterprise
from app.models.invitation import Invitation
from app.models.user import User
from app.utils.error_codes import ErrorCode
from app.schemas.enterprise import (
    EnterpriseResponse,
    MemberResponse,
    InvitationResponse,
    UpdateRoleRequest,
)


# ==================== 辅助函数 ====================


def _save_cookies(client):
    """保存当前 client 的 Cookie 状态，用于多用户切换。"""
    return [(c.name, c.value, c.domain, c.path) for c in client.cookies.jar]


def _restore_cookies(client, cookies):
    """恢复 client 的 Cookie 状态。"""
    client.cookies.clear()
    for name, value, domain, path in cookies:
        client.cookies.set(name, value, domain=domain or None, path=path or "/")


async def _register_user(client, email: str, name: str = "测试用户", password: str = "Test1234!"):
    """注册用户（Cookie 由 httpx 自动保存）。"""
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": name,
        "password": password,
    })
    assert resp.status_code == 201, f"注册失败: {resp.text}"


async def _create_enterprise(client, name: str = "测试企业"):
    """创建企业并返回企业 ID（依赖 Cookie 认证）。"""
    resp = await client.post("/api/v1/enterprises", json={"name": name})
    assert resp.status_code == 201, f"创建企业失败: {resp.text}"
    return resp.json()["data"]["id"]


async def _setup_admin_with_enterprise(client, email: str = "admin@test.com"):
    """注册用户 → 创建企业 → 返回 enterprise_id。"""
    await _register_user(client, email, name="管理员")
    return await _create_enterprise(client)


async def _invite_and_join(client, enterprise_id: str, new_email: str):
    """管理员生成邀请 → 新用户通过邀请注册。

    调用后 client 恢复为管理员身份，返回新用户的 Cookie 状态。
    """
    # 生成邀请
    resp = await client.post(f"/api/v1/enterprises/{enterprise_id}/invite")
    assert resp.status_code == 200, f"生成邀请失败: {resp.text}"
    invite_token = resp.json()["data"]["invite_token"]

    # 保存管理员 Cookie
    admin_cookies = _save_cookies(client)

    # 新用户通过邀请注册
    resp = await client.post(
        "/api/v1/auth/register-with-invite",
        params={"invite_token": invite_token},
        json={"email": new_email, "name": "成员", "password": "Test1234!"},
    )
    assert resp.status_code == 201, f"邀请注册失败: {resp.text}"

    # 保存新用户 Cookie 并恢复管理员身份
    member_cookies = _save_cookies(client)
    _restore_cookies(client, admin_cookies)
    return member_cookies


# ==================== Schema 测试 ====================


class TestSchema:
    """验证 schema 字段。"""

    def test_enterprise_response_has_new_fields(self):
        """EnterpriseResponse 应包含 is_active, invite_max_uses, invite_used_count。"""
        fields = EnterpriseResponse.model_fields
        assert "is_active" in fields
        assert "invite_max_uses" in fields
        assert "invite_used_count" in fields

    def test_member_response_fields(self):
        """MemberResponse 应包含所有必要字段。"""
        fields = MemberResponse.model_fields
        for f in ["id", "email", "name", "role", "is_active", "last_login_at", "created_at"]:
            assert f in fields, f"MemberResponse 缺少字段 {f}"

    def test_invitation_response_fields(self):
        """InvitationResponse 应包含所有必要字段。"""
        fields = InvitationResponse.model_fields
        for f in ["id", "enterprise_id", "invited_by_user_id", "token", "email",
                  "status", "expires_at", "created_at", "used_at", "used_by_user_id"]:
            assert f in fields, f"InvitationResponse 缺少字段 {f}"

    def test_update_role_request_validates_role(self):
        """UpdateRoleRequest 应只接受 admin 或 member。"""
        assert UpdateRoleRequest(role="admin").role == "admin"
        assert UpdateRoleRequest(role="member").role == "member"
        with pytest.raises(Exception):
            UpdateRoleRequest(role="invalid")

    def test_enterprise_response_defaults(self):
        """EnterpriseResponse 新字段应有合理默认值。"""
        fields = EnterpriseResponse.model_fields
        # 验证默认值
        assert fields["is_active"].default is True
        assert fields["invite_max_uses"].default == 10
        assert fields["invite_used_count"].default == 0


# ==================== 成员管理测试 ====================


class TestMemberManagement:
    """成员管理测试。"""

    @pytest.mark.asyncio
    async def test_list_members(self, client):
        """管理员应能查看成员列表。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "listmem@test.com")

        # 邀请一个成员加入
        await _invite_and_join(client, enterprise_id, "mem1@test.com")

        # 查看成员列表
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/members",
        )
        assert resp.status_code == 200
        members = resp.json()["data"]
        assert len(members) == 2
        emails = {m["email"] for m in members}
        assert "listmem@test.com" in emails
        assert "mem1@test.com" in emails

    @pytest.mark.asyncio
    async def test_list_members_member_can_view(self, client):
        """普通成员也可以查看成员列表。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "adminview@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "memberview@test.com")

        # 普通成员查看成员列表
        _restore_cookies(client, member_cookies)
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/members",
        )
        assert resp.status_code == 200
        members = resp.json()["data"]
        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_remove_member(self, client):
        """管理员应能移除成员。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "rmadmin@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "rmuser@test.com")

        # 获取成员 ID
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/members",
        )
        members = resp.json()["data"]
        target = next(m for m in members if m["email"] == "rmuser@test.com")

        # 移除成员
        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}/members/{target['id']}",
        )
        assert resp.status_code == 200

        # 被移除的用户应无法使用 API（is_active=False）
        _restore_cookies(client, member_cookies)
        resp = await client.get(
            "/api/v1/auth/me",
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_remove_self(self, client):
        """管理员不能移除自己。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "selfrm@test.com")

        # 获取自己的 user id
        resp = await client.get(
            "/api/v1/auth/me",
        )
        my_id = resp.json()["data"]["id"]

        # 尝试移除自己
        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}/members/{my_id}",
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_only_admin_can_remove(self, client):
        """普通成员不能移除其他成员。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "adminonly@test.com")
        member1_cookies = await _invite_and_join(client, enterprise_id, "mem1only@test.com")
        member2_cookies = await _invite_and_join(client, enterprise_id, "mem2only@test.com")

        # 获取 member2 的 ID
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/members",
        )
        members = resp.json()["data"]
        target = next(m for m in members if m["email"] == "mem2only@test.com")

        # member1 尝试移除 member2 → 应失败
        admin_cookies = _save_cookies(client)
        _restore_cookies(client, member1_cookies)
        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}/members/{target['id']}",
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_remove_nonexistent_member(self, client):
        """移除不存在的成员应返回 404。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "rmnonexist@test.com")

        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}/members/nonexistent-user-id",
        )
        assert resp.status_code == 404


# ==================== 角色变更测试 ====================


class TestRoleChange:
    """角色变更测试。"""

    @pytest.mark.asyncio
    async def test_change_role(self, client):
        """管理员应能变更成员角色。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "roleadmin@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "rolemem@test.com")

        # 获取成员 ID
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/members",
        )
        members = resp.json()["data"]
        target = next(m for m in members if m["email"] == "rolemem@test.com")
        assert target["role"] == "member"

        # 提升为 admin
        resp = await client.put(
            f"/api/v1/enterprises/{enterprise_id}/members/{target['id']}/role",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "admin"

        # 再降级为 member
        resp = await client.put(
            f"/api/v1/enterprises/{enterprise_id}/members/{target['id']}/role",
            json={"role": "member"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "member"

    @pytest.mark.asyncio
    async def test_cannot_change_own_role(self, client):
        """管理员不能变更自己的角色。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "selfrole@test.com")

        resp = await client.get(
            "/api/v1/auth/me",
        )
        my_id = resp.json()["data"]["id"]

        resp = await client.put(
            f"/api/v1/enterprises/{enterprise_id}/members/{my_id}/role",
            json={"role": "member"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_only_admin_can_change_role(self, client):
        """普通成员不能变更他人角色。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "adminchg@test.com")
        member1_cookies = await _invite_and_join(client, enterprise_id, "mem1chg@test.com")
        member2_cookies = await _invite_and_join(client, enterprise_id, "mem2chg@test.com")

        # 获取 member2 的 ID
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/members",
        )
        members = resp.json()["data"]
        target = next(m for m in members if m["email"] == "mem2chg@test.com")

        # member1 尝试变更 member2 的角色 → 应失败
        admin_cookies = _save_cookies(client)
        _restore_cookies(client, member1_cookies)
        resp = await client.put(
            f"/api/v1/enterprises/{enterprise_id}/members/{target['id']}/role",
            json={"role": "admin"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_role_rejected(self, client):
        """非法角色值应被拒绝。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invrole@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "invrolemem@test.com")

        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/members",
        )
        members = resp.json()["data"]
        target = next(m for m in members if m["email"] == "invrolemem@test.com")

        # 非法角色值
        resp = await client.put(
            f"/api/v1/enterprises/{enterprise_id}/members/{target['id']}/role",
            json={"role": "superadmin"},
        )
        assert resp.status_code == 422


# ==================== 企业软删除测试 ====================


class TestEnterpriseSoftDelete:
    """企业软删除测试。"""

    @pytest.mark.asyncio
    async def test_soft_delete(self, client, db_session):
        """管理员软删除企业应成功，is_active=False。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "deladmin@test.com")

        # 邀请一个成员
        await _invite_and_join(client, enterprise_id, "delmem@test.com")

        # 软删除
        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}",
        )
        assert resp.status_code == 200

        # 直接查 DB 验证
        result = await db_session.execute(
            select(Enterprise).where(Enterprise.id == enterprise_id)
        )
        enterprise = result.scalar_one_or_none()
        assert enterprise is not None
        assert enterprise.is_active is False

        # 所有成员应被禁用
        result = await db_session.execute(
            select(User).where(User.enterprise_id == enterprise_id)
        )
        users = result.scalars().all()
        assert len(users) >= 2
        for u in users:
            assert u.is_active is False

    @pytest.mark.asyncio
    async def test_cannot_access_after_delete(self, client):
        """软删除后，原管理员 token 应失效（is_active=False）。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "delaccess@test.com")

        # 软删除
        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}",
        )
        assert resp.status_code == 200

        # 尝试访问企业 → 应失败（账号已被禁用）
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}",
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_only_admin_can_delete(self, client):
        """普通成员不能删除企业。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "adminsd@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "memsd@test.com")

        # 普通成员尝试删除企业
        admin_cookies = _save_cookies(client)
        _restore_cookies(client, member_cookies)
        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}",
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_enterprise_returns_404_after_delete(self, client, db_session):
        """另一个 admin 视角下，已软删除的企业 GET 应返回 404。"""
        # 第一个 admin 创建企业并删除
        enterprise_id = await _setup_admin_with_enterprise(client, "del1@test.com")
        await _invite_and_join(client, enterprise_id, "del2@test.com")

        # 软删除企业（删除后 admin1 也被禁用）
        resp = await client.delete(
            f"/api/v1/enterprises/{enterprise_id}",
        )
        assert resp.status_code == 200

        # 用新注册的独立用户访问已软删除的企业 → 应 404（因为先检查 is_active）
        await _register_user(client, "outsider@test.com")
        outsider_cookies = _save_cookies(client)
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}",
        )
        assert resp.status_code == 404


# ==================== 邀请记录测试 ====================


class TestInvitation:
    """邀请记录管理测试。"""

    @pytest.mark.asyncio
    async def test_create_invitation(self, client, db_session):
        """生成邀请应创建 Invitation 记录。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invcreate@test.com")

        # 生成邀请
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite",
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["invite_token"]
        assert data["invitation_id"]
        assert data["max_uses"] == 10  # 默认值
        assert data["used_count"] == 0

        # DB 中应有 Invitation 记录
        result = await db_session.execute(
            select(Invitation).where(Invitation.enterprise_id == enterprise_id)
        )
        invitations = result.scalars().all()
        assert len(invitations) == 1
        assert invitations[0].token == data["invite_token"]
        assert invitations[0].status == "pending"

    @pytest.mark.asyncio
    async def test_create_invitation_with_max_uses(self, client):
        """应支持自定义 max_uses 参数。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invmax@test.com")

        # 使用自定义 max_uses
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite?max_uses=5",
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["max_uses"] == 5

    @pytest.mark.asyncio
    async def test_create_invitation_with_email(self, client, db_session):
        """应支持指定邮箱邀请。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invemail@test.com")

        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite?email=specific@test.com",
        )
        assert resp.status_code == 200

        # 验证 Invitation 记录的 email 字段
        result = await db_session.execute(
            select(Invitation).where(Invitation.enterprise_id == enterprise_id)
        )
        inv = result.scalar_one()
        assert inv.email == "specific@test.com"

    @pytest.mark.asyncio
    async def test_invalid_max_uses_rejected(self, client):
        """max_uses < 1 应被拒绝。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invinv@test.com")

        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite?max_uses=0",
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_invitations(self, client):
        """管理员应能查看邀请记录列表。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invlist@test.com")

        # 生成 2 个邀请
        await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite",
        )
        await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite",
        )

        # 查看邀请记录
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/invitations",
        )
        assert resp.status_code == 200
        invitations = resp.json()["data"]
        assert len(invitations) == 2
        for inv in invitations:
            assert inv["status"] == "pending"
            assert inv["token"]

    @pytest.mark.asyncio
    async def test_cancel_invitation(self, client, db_session):
        """管理员应能取消 pending 状态的邀请。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invcancel@test.com")

        # 生成邀请
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite",
        )
        invitation_id = resp.json()["data"]["invitation_id"]

        # 取消邀请
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invitations/{invitation_id}/cancel",
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "cancelled"

        # DB 验证
        result = await db_session.execute(
            select(Invitation).where(Invitation.id == invitation_id)
        )
        inv = result.scalar_one()
        assert inv.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_non_pending_invitation_fails(self, client):
        """取消非 pending 状态的邀请应失败。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invrecancel@test.com")

        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite",
        )
        invitation_id = resp.json()["data"]["invitation_id"]

        # 第一次取消
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invitations/{invitation_id}/cancel",
        )
        assert resp.status_code == 200

        # 第二次取消应失败
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invitations/{invitation_id}/cancel",
        )
        assert resp.status_code == 400
        # BE-SEC-02: 错误码不应暴露内部状态值
        assert resp.json()["message"] == ErrorCode.INVITATION_STATUS_INVALID

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_invitation(self, client):
        """取消不存在的邀请应返回 404。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invnonexist@test.com")

        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invitations/nonexistent-id/cancel",
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_only_admin_can_list_invitations(self, client):
        """普通成员不能查看邀请记录。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invmem@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "invmemuser@test.com")

        # 普通成员尝试查看邀请记录
        admin_cookies = _save_cookies(client)
        _restore_cookies(client, member_cookies)
        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}/invitations",
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_only_admin_can_cancel_invitation(self, client):
        """普通成员不能取消邀请。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invmemcancel@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "invmemcanceluser@test.com")

        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite",
        )
        invitation_id = resp.json()["data"]["invitation_id"]

        # 普通成员尝试取消邀请
        _restore_cookies(client, member_cookies)
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invitations/{invitation_id}/cancel",
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_max_uses_limit_default(self, client, db_session):
        """验证 invite_max_uses 默认值为 10。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invlimit@test.com")

        # 生成邀请（使用默认 max_uses）
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite",
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["max_uses"] == 10

        # DB 验证 Enterprise.invite_max_uses
        result = await db_session.execute(
            select(Enterprise).where(Enterprise.id == enterprise_id)
        )
        enterprise = result.scalar_one()
        assert enterprise.invite_max_uses == 10
        assert enterprise.invite_used_count == 0

    @pytest.mark.asyncio
    async def test_max_uses_custom_value(self, client, db_session):
        """验证自定义 max_uses 被持久化。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "invcustom@test.com")

        # 使用自定义 max_uses=3
        resp = await client.post(
            f"/api/v1/enterprises/{enterprise_id}/invite?max_uses=3",
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["max_uses"] == 3

        # DB 验证
        result = await db_session.execute(
            select(Enterprise).where(Enterprise.id == enterprise_id)
        )
        enterprise = result.scalar_one()
        assert enterprise.invite_max_uses == 3


# ==================== 企业查询新字段测试 ====================


class TestEnterpriseResponseNewFields:
    """验证 EnterpriseResponse 返回新字段。"""

    @pytest.mark.asyncio
    async def test_get_enterprise_returns_new_fields(self, client):
        """GET /enterprises/{id} 应返回 is_active, invite_max_uses, invite_used_count。"""
        enterprise_id = await _setup_admin_with_enterprise(client, "fields@test.com")

        resp = await client.get(
            f"/api/v1/enterprises/{enterprise_id}",
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_active"] is True
        assert data["invite_max_uses"] == 10
        assert data["invite_used_count"] == 0

    @pytest.mark.asyncio
    async def test_get_enterprise_hides_invite_token_for_member(self, client):
        """安全：普通成员 GET /enterprises/{id} 不应看到 invite_token 等敏感字段。

        防止普通成员越权获取邀请 token 后邀请任意用户加入企业（权限提升）。
        管理员仍可看到完整字段。
        """
        enterprise_id = await _setup_admin_with_enterprise(client, "hideadmin@test.com")
        member_cookies = await _invite_and_join(client, enterprise_id, "hidemember@test.com")

        # 切换为普通成员身份
        admin_cookies = _save_cookies(client)
        _restore_cookies(client, member_cookies)

        resp = await client.get(f"/api/v1/enterprises/{enterprise_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 普通成员不应看到邀请 token 等敏感信息
        assert data["invite_token"] is None
        assert data["invite_expires_at"] is None
        assert data["invite_max_uses"] is None
        assert data["invite_used_count"] is None

        # 切回管理员，应能看到完整字段
        _restore_cookies(client, admin_cookies)
        resp = await client.get(f"/api/v1/enterprises/{enterprise_id}")
        assert resp.status_code == 200
        admin_data = resp.json()["data"]
        assert admin_data["invite_token"] is not None
        assert admin_data["invite_max_uses"] == 10

    @pytest.mark.asyncio
    async def test_create_enterprise_returns_new_fields(self, client):
        """创建企业时应返回新字段。"""
        await _register_user(client, "createfields@test.com")
        resp = await client.post(
            "/api/v1/enterprises",
            json={"name": "字段测试企业"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["is_active"] is True
        assert data["invite_max_uses"] == 10
        assert data["invite_used_count"] == 0
