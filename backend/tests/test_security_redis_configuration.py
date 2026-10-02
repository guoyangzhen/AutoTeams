"""安全库环境变量必须真的进入 Settings，不能被 extra=ignore 静默丢弃。"""
from app.config import Settings


def test_security_redis_environment_is_loaded_independently(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://cache.example:6379/0")
    monkeypatch.setenv("TOKEN_BLACKLIST_REDIS_URL", "redis://security.example:6379/1")
    configured = Settings(_env_file=None)
    assert configured.REDIS_URL == "redis://cache.example:6379/0"
    assert configured.TOKEN_BLACKLIST_REDIS_URL == "redis://security.example:6379/1"
