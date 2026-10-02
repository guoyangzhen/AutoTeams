"""C4: 认证与授权加固 安全修复测试。

覆盖缺口：
- M9: 账户锁定 / 暴力破解检测（连续失败达阈值后锁定，成功登录重置计数）
- S9: RBAC require_admin_or_owner 依赖（管理员或资源所有者放行）
"""
import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from app.services.auth_service import (
    is_account_locked,
    record_failed_login,
    reset_failed_attempts,
    get_remaining_lockout_seconds,
    AccountLockedError,
)
from app.utils.rbac import require_admin_or_owner
from app.utils.error_codes import ErrorCode
from app.config import settings


# ---------------------------------------------------------------------------
# M9: 账户锁定单元测试（直接调用 service 层函数）
# ---------------------------------------------------------------------------

class TestM9AccountLockoutUnit:
    """M9: 账户锁定 / 暴力破解检测 — service 层单元测试。"""

    def setup_method(self):
        """每个测试前清理内存锁定状态，避免测试间污染。"""
        # 测试环境 REDIS_URL="" ，走内存回退
        reset_failed_attempts("locktest@test.com")
        reset_failed_attempts("LOCKTEST@test.com")

    def teardown_method(self):
        reset_failed_attempts("locktest@test.com")
        reset_failed_attempts("LOCKTEST@test.com")

    def test_not_locked_initially(self):
        """新邮箱不应处于锁定状态。"""
        assert is_account_locked("newuser@test.com") is False

    def test_failed_attempts_below_threshold_no_lock(self):
        """失败次数低于阈值时不应锁定。"""
        email = "locktest@test.com"
        threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD
        for i in range(threshold - 1):
            attempts = record_failed_login(email)
            assert attempts == i + 1
        assert is_account_locked(email) is False

    def test_lock_triggered_at_threshold(self):
        """达到阈值时应触发锁定。"""
        email = "locktest@test.com"
        threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD
        for _ in range(threshold):
            record_failed_login(email)
        assert is_account_locked(email) is True

    def test_lock_has_remaining_seconds(self):
        """锁定后应有剩余锁定时间。"""
        email = "locktest@test.com"
        for _ in range(settings.ACCOUNT_LOCKOUT_THRESHOLD):
            record_failed_login(email)
        remaining = get_remaining_lockout_seconds(email)
        assert remaining > 0

    def test_reset_clears_lockout(self):
        """reset_failed_attempts 应清除锁定状态。"""
        email = "locktest@test.com"
        for _ in range(settings.ACCOUNT_LOCKOUT_THRESHOLD):
            record_failed_login(email)
        assert is_account_locked(email) is True
        reset_failed_attempts(email)
        assert is_account_locked(email) is False
        assert get_remaining_lockout_seconds(email) == 0

    def test_email_case_insensitive(self):
        """锁定状态应以小写邮箱为键（大小写不敏感）。"""
        record_failed_login("CaseTest@test.com")
        # 内部存储为小写，查询也应不区分大小写
        assert is_account_locked("casetest@test.com") is False or \
               is_account_locked("CaseTest@test.com") is False
        # 达到阈值后两种写法都应反映锁定
        for _ in range(settings.ACCOUNT_LOCKOUT_THRESHOLD):
            record_failed_login("CaseTest@test.com")
        assert is_account_locked("casetest@test.com") is True

    def test_empty_email_no_op(self):
        """空邮箱不应触发任何状态变更。"""
        assert is_account_locked("") is False
        assert record_failed_login("") == 0
        reset_failed_attempts("")  # 不应报错


# ---------------------------------------------------------------------------
# M9: 登录端点集成测试（通过 HTTP 客户端验证完整流程）
# ---------------------------------------------------------------------------

class TestM9LoginLockoutIntegration:
    """M9: 登录端点集成测试 — 验证连续失败后返回 403 ACCOUNT_LOCKED。"""

    @pytest.mark.asyncio
    async def test_login_failure_returns_401_before_threshold(self, client):
        """阈值前的失败登录应返回 401 INVALID_CREDENTIALS。"""
        # 先注册一个用户
        await client.post("/api/v1/auth/register", json={
            "email": "lockint@test.com",
            "name": "锁定测试",
            "password": "correctpass123",
        })
        # 清理可能的历史状态
        reset_failed_attempts("lockint@test.com")

        # 用错误密码登录（阈值-1次）
        threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD
        for i in range(threshold - 1):
            resp = await client.post("/api/v1/auth/login", json={
                "email": "lockint@test.com",
                "password": "wrongpassword",
            })
            assert resp.status_code == 401, f"第{i+1}次失败应返回401，实际{resp.status_code}"
            assert resp.json()["message"] == ErrorCode.INVALID_CREDENTIALS

        # 清理
        reset_failed_attempts("lockint@test.com")

    @pytest.mark.asyncio
    async def test_login_locked_after_threshold(self, client):
        """达到阈值后登录应返回 403 ACCOUNT_LOCKED。"""
        await client.post("/api/v1/auth/register", json={
            "email": "locktrigger@test.com",
            "name": "锁定触发",
            "password": "correctpass123",
        })
        reset_failed_attempts("locktrigger@test.com")

        threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD
        # 触发阈值次失败（最后一次触发锁定）
        for i in range(threshold):
            resp = await client.post("/api/v1/auth/login", json={
                "email": "locktrigger@test.com",
                "password": "wrongpassword",
            })
            if i < threshold - 1:
                assert resp.status_code == 401, f"第{i+1}次应返回401"
            else:
                # 第 threshold 次失败触发锁定，但仍返回 401（这次是失败本身）
                assert resp.status_code == 401

        # 再试一次（即使密码正确也应被锁定）
        resp = await client.post("/api/v1/auth/login", json={
            "email": "locktrigger@test.com",
            "password": "correctpass123",
        })
        assert resp.status_code == 403
        assert resp.json()["message"] == ErrorCode.ACCOUNT_LOCKED

        # 清理
        reset_failed_attempts("locktrigger@test.com")

    @pytest.mark.asyncio
    async def test_successful_login_resets_counter(self, client):
        """阈值前成功登录应重置失败计数。"""
        await client.post("/api/v1/auth/register", json={
            "email": "resetcounter@test.com",
            "name": "重置测试",
            "password": "correctpass123",
        })
        reset_failed_attempts("resetcounter@test.com")

        threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD
        # 失败 threshold-1 次
        for _ in range(threshold - 1):
            await client.post("/api/v1/auth/login", json={
                "email": "resetcounter@test.com",
                "password": "wrong",
            })

        # 成功登录一次（应重置计数）
        resp = await client.post("/api/v1/auth/login", json={
            "email": "resetcounter@test.com",
            "password": "correctpass123",
        })
        assert resp.status_code == 200

        # 再失败 threshold-1 次，不应锁定
        for _ in range(threshold - 1):
            await client.post("/api/v1/auth/login", json={
                "email": "resetcounter@test.com",
                "password": "wrong",
            })
        assert is_account_locked("resetcounter@test.com") is False

        # 清理
        reset_failed_attempts("resetcounter@test.com")

    @pytest.mark.asyncio
    async def test_nonexistent_user_records_failure(self, client):
        """不存在的邮箱也应记录失败次数（防止用户枚举）。"""
        reset_failed_attempts("nonexist@test.com")
        threshold = settings.ACCOUNT_LOCKOUT_THRESHOLD

        for _ in range(threshold):
            await client.post("/api/v1/auth/login", json={
                "email": "nonexist@test.com",
                "password": "whatever",
            })

        # 达到阈值后应锁定
        assert is_account_locked("nonexist@test.com") is True

        # 清理
        reset_failed_attempts("nonexist@test.com")


# ---------------------------------------------------------------------------
# S9: require_admin_or_owner 单元测试
# ---------------------------------------------------------------------------

class TestS9RequireAdminOrOwner:
    """S9: require_admin_or_owner RBAC 依赖测试。"""

    def _make_user(self, *, enterprise_id=None, role="member", user_id="user-123"):
        """构造测试用 User-like 对象（SimpleNamespace，避免 SQLAlchemy 实例化开销）。"""
        return SimpleNamespace(
            id=user_id,
            enterprise_id=enterprise_id,
            role=role,
            is_active=True,
        )

    @pytest.mark.asyncio
    async def test_super_admin_allowed(self):
        """系统超管（enterprise_id=None + role='admin'）应放行。

        安全修复回归：历史上 enterprise_id=None 用户无论 role 都放行，
        修复后必须 role=='admin' 才放行。
        """
        admin = self._make_user(enterprise_id=None, role="admin")
        result = await require_admin_or_owner(admin, "someone-else-id")
        assert result is admin

    @pytest.mark.asyncio
    async def test_public_register_member_rejected(self):
        """安全修复回归：/auth/register 创建的 enterprise_id=None + role=member 用户
        不能再绕过 require_admin_or_owner 检查。
        """
        member = self._make_user(enterprise_id=None, role="member")
        with pytest.raises(HTTPException) as exc_info:
            await require_admin_or_owner(member, "someone-else-id")
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_enterprise_admin_allowed(self):
        """企业管理员（role='admin'）应放行，即使不是资源所有者。"""
        admin = self._make_user(enterprise_id="ent-1", role="admin", user_id="admin-1")
        result = await require_admin_or_owner(admin, "owner-999")
        assert result is admin

    @pytest.mark.asyncio
    async def test_resource_owner_allowed(self):
        """资源所有者（user.id == owner_id）应放行。"""
        owner = self._make_user(enterprise_id="ent-1", role="member", user_id="owner-1")
        result = await require_admin_or_owner(owner, "owner-1")
        assert result is owner

    @pytest.mark.asyncio
    async def test_non_owner_non_admin_denied(self):
        """非所有者非管理员应被拒绝（403）。"""
        from fastapi import HTTPException
        other = self._make_user(enterprise_id="ent-1", role="member", user_id="user-1")
        with pytest.raises(HTTPException) as exc_info:
            await require_admin_or_owner(other, "owner-999")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == ErrorCode.FORBIDDEN

    @pytest.mark.asyncio
    async def test_owner_id_string_comparison(self):
        """owner_id 与 user.id 的字符串比较应正确（UUID vs str）。"""
        import uuid
        uid = uuid.uuid4()
        owner = self._make_user(enterprise_id="ent-1", role="member", user_id=uid)
        # 传入 str(uid) 应匹配
        result = await require_admin_or_owner(owner, str(uid))
        assert result is owner
