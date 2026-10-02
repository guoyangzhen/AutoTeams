"""P0-S3: access token 黑名单测试。"""
from datetime import timedelta

import pytest

from app.utils.security import create_access_token
from app.utils.token_blacklist import revoke_access_token, is_token_revoked


class TestTokenBlacklist:
    """access token 吊销与校验测试。"""

    def test_revoke_and_check_access_token(self):
        """吊销后的 access token 应被识别为已吊销。"""
        token = create_access_token(data={"sub": "user-123", "email": "test@test.com"})
        assert is_token_revoked(token) is False

        revoke_access_token(token)
        assert is_token_revoked(token) is True

    def test_revoke_ignores_expired_token(self):
        """已过期的 token 无需加入黑名单。"""
        token = create_access_token(
            data={"sub": "user-123"},
            expires_delta=timedelta(seconds=-1),
        )
        # 先吊销（应静默忽略）
        revoke_access_token(token)
        # 已过期 token decode 会失败，is_token_revoked 返回 False
        assert is_token_revoked(token) is False

    def test_revoke_ignores_refresh_token(self):
        """refresh token 不应被 access token 黑名单机制吊销。"""
        from app.utils.security import create_refresh_token

        token = create_refresh_token(data={"sub": "user-123"})
        revoke_access_token(token)
        assert is_token_revoked(token) is False

    def test_token_without_jti_not_revokable(self):
        """无 jti 的 token 无法吊销，视为未吊销（向后兼容）。"""
        import jwt
        from app.config import settings

        token = jwt.encode(
            {"sub": "user-123", "type": "access", "exp": 9999999999},
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        revoke_access_token(token)
        assert is_token_revoked(token) is False

    def test_access_token_default_expiry_is_short(self):
        """P0-S3: access token 默认有效期应不超过 1 小时。"""
        from datetime import datetime, timezone

        token = create_access_token(data={"sub": "user-123"})
        payload = decode_token(token)
        exp = datetime.fromtimestamp(payload["exp"], timezone.utc)
        delta = (exp - datetime.now(timezone.utc)).total_seconds()
        assert 0 < delta <= 3600


class _FakePolicyRedis:
    """只实现 `config_get` 的 Redis 替身。"""

    def __init__(self, value=None, error: Exception | None = None) -> None:
        self._value = value
        self._error = error

    def config_get(self, _key):  # noqa: ANN001, ANN201 - 测试替身
        if self._error is not None:
            raise self._error
        return {} if self._value is None else {"maxmemory-policy": self._value}


class TestEvictionPolicyAcceptance:
    """生产撤销存储必须是独占的 noeviction 实例（AUD-09）。"""

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        from app.utils import token_blacklist

        monkeypatch.setattr(token_blacklist.settings, "DEBUG", False)
        monkeypatch.setattr(token_blacklist, "_eviction_policy_checked", False)
        yield
        monkeypatch.setattr(token_blacklist, "_eviction_policy_checked", False)

    def test_noeviction_is_accepted(self):
        from app.utils.token_blacklist import _check_eviction_policy

        _check_eviction_policy(_FakePolicyRedis("noeviction"))

    @pytest.mark.parametrize("policy", ["allkeys-lru", "volatile-lru", "volatile-ttl"])
    def test_evicting_policies_are_rejected(self, policy):
        """带 TTL 的撤销键会被 volatile-* 与 allkeys-* 提前淘汰。"""
        from app.utils.token_blacklist import (
            TokenRevocationStoreUnavailable,
            _check_eviction_policy,
        )

        with pytest.raises(TokenRevocationStoreUnavailable):
            _check_eviction_policy(_FakePolicyRedis(policy))

    def test_unreadable_policy_fails_closed(self):
        """CONFIG 被禁用时不得"告警放行"：无法证明安全就不能放行。"""
        from app.utils.token_blacklist import (
            TokenRevocationStoreUnavailable,
            _check_eviction_policy,
        )

        with pytest.raises(TokenRevocationStoreUnavailable):
            _check_eviction_policy(
                _FakePolicyRedis(error=RuntimeError("NOPERM this user has no permissions"))
            )

    def test_empty_policy_fails_closed(self):
        from app.utils.token_blacklist import (
            TokenRevocationStoreUnavailable,
            _check_eviction_policy,
        )

        with pytest.raises(TokenRevocationStoreUnavailable):
            _check_eviction_policy(_FakePolicyRedis(None))

    def test_connection_uses_bounded_socket_timeouts(self, monkeypatch):
        """撤销检查在请求线程内同步执行，必须有读超时避免无限挂起。"""
        from app.utils import token_blacklist

        captured: dict = {}

        class _FakeClient:
            def ping(self):
                return True

            def config_get(self, _key):  # noqa: ANN001, ANN201 - 测试替身
                return {"maxmemory-policy": "noeviction"}

        def _from_url(url, **kwargs):  # noqa: ANN001, ANN202 - 测试替身
            captured.update(kwargs)
            return _FakeClient()

        # 该模块缓存全局客户端：必须前后都清空，否则替身会泄漏给其他用例。
        monkeypatch.setattr(token_blacklist, "_redis_client", None)
        monkeypatch.setattr(token_blacklist, "_eviction_policy_checked", False)
        monkeypatch.setattr(token_blacklist.redis, "from_url", _from_url)
        monkeypatch.setattr(token_blacklist, "_blacklist_redis_url", lambda: "redis://x/0")
        try:
            token_blacklist._get_redis()
        finally:
            monkeypatch.setattr(token_blacklist, "_redis_client", None)
            monkeypatch.setattr(token_blacklist, "_eviction_policy_checked", False)

        assert captured["socket_connect_timeout"] > 0
        assert captured["socket_timeout"] > 0



# 避免循环导入，在底部导入 decode_token
from app.utils.security import decode_token  # noqa: E402
