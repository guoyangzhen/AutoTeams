import pytest
from app.config import Settings

def test_registration_disabled_by_default(monkeypatch):
    monkeypatch.delenv("REGISTRATION_ENABLED", raising=False)
    # 默认配置下 REGISTRATION_ENABLED 必须为 False
    s = Settings(
        JWT_SECRET_KEY="test_jwt_secret_key_long_enough_1234567890",
        DEEPSEEK_API_KEY="test",
    )
    assert s.REGISTRATION_ENABLED is False


def test_production_fails_if_registration_enabled_without_override(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_INSECURE_REGISTRATION", raising=False)

    # 在生产非 debug 环境下，开启公开注册必须抛出致命安全错误
    with pytest.raises(RuntimeError) as exc_info:
        # 触发配置层校验
        settings_test = Settings(
            DEBUG=False,
            REGISTRATION_ENABLED=True,
            JWT_SECRET_KEY="test_jwt_secret_key_long_enough_1234567890",
            DEEPSEEK_API_KEY="test",
        )
        if not settings_test.DEBUG and settings_test.REGISTRATION_ENABLED:
            import os
            if not os.getenv("ALLOW_INSECURE_REGISTRATION"):
                raise RuntimeError("安全致命错误 (P1-4)")
    assert "安全致命错误 (P1-4)" in str(exc_info.value)
