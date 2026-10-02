"""核心安全功能测试：密码哈希、JWT token 创建与验证。"""
from datetime import timedelta

import pytest
import jwt

from app.config import settings
from app.utils.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)


class TestPasswordHashing:
    """密码哈希测试。"""

    def test_hash_password(self):
        hashed = get_password_hash("mysecret123")
        assert hashed != "mysecret123"
        assert hashed.startswith("$2")  # bcrypt 前缀

    def test_verify_correct_password(self):
        hashed = get_password_hash("correctpass")
        assert verify_password("correctpass", hashed) is True

    def test_verify_wrong_password(self):
        hashed = get_password_hash("correctpass")
        assert verify_password("wrongpass", hashed) is False

    def test_password_hash_is_unique(self):
        """同一密码两次哈希应不同（盐随机）。"""
        h1 = get_password_hash("samepass")
        h2 = get_password_hash("samepass")
        assert h1 != h2

    def test_long_password_truncated_to_72_bytes(self):
        """bcrypt 只取前 72 字节，超长密码应被截断且不报错。"""
        long_pw = "a" * 200
        hashed = get_password_hash(long_pw)
        assert verify_password(long_pw, hashed) is True

    def test_unicode_password(self):
        """支持中文等 Unicode 密码。"""
        pw = "密码密码abc123"
        hashed = get_password_hash(pw)
        assert verify_password(pw, hashed) is True


class TestJWTToken:
    """JWT token 创建与验证测试。"""

    def test_create_token_contains_sub(self):
        token = create_access_token(data={"sub": "user-123", "email": "test@test.com"})
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["email"] == "test@test.com"

    def test_token_has_expiry(self):
        token = create_access_token(data={"sub": "user-123"})
        payload = decode_token(token)
        assert "exp" in payload

    def test_token_with_custom_expiry(self):
        token = create_access_token(
            data={"sub": "user-123"},
            expires_delta=timedelta(hours=1),
        )
        payload = decode_token(token)
        # exp 应该在未来约 1 小时
        from datetime import datetime, timezone
        exp = datetime.fromtimestamp(payload["exp"], timezone.utc)
        delta = exp - datetime.now(timezone.utc)
        # 允许 5 秒误差
        assert 3500 < delta.total_seconds() < 3610

    def test_decode_invalid_token_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            decode_token("invalid.token.here")
        assert exc_info.value.status_code == 401

    def test_decode_token_wrong_secret_raises_401(self):
        """用错误密钥签发的 token 应被拒绝。"""
        token = jwt.encode(
            {"sub": "user-123", "exp": 9999999999},
            "wrong-secret",
            algorithm=settings.JWT_ALGORITHM,
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self):
        """过期 token 应被拒绝。"""
        token = create_access_token(
            data={"sub": "user-123"},
            expires_delta=timedelta(seconds=-1),
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401
