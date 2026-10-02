"""模型 API 配置（LLMApiConfig）与 Fernet 加密工具测试。"""
import pytest

from app.schemas.llm_config import LLMApiConfigUpdate
from app.services.llm_config import (
    get_config,
    resolve_enterprise_llm,
    to_view,
    upsert_config,
)
from app.utils import crypto


# ==================== 加密工具 ====================

class TestCrypto:
    def test_encrypt_decrypt_roundtrip(self):
        plain = "sk-proj-secret-value-12345"
        token = crypto.encrypt_secret(plain)
        assert token  # 有内容
        assert plain not in token  # 密文不含明文
        assert crypto.decrypt_secret(token) == plain

    def test_encrypt_empty_returns_empty(self):
        assert crypto.encrypt_secret("") == ""
        assert crypto.encrypt_secret(None) == ""

    def test_decrypt_invalid_returns_empty(self):
        assert crypto.decrypt_secret("garbage-not-a-token") == ""

    def test_mask_secret(self):
        assert crypto.mask_secret("sk-abcdef1234") == "sk-****1234"
        assert crypto.mask_secret("") == ""
        assert crypto.mask_secret("ab") == "****"

    def test_same_plaintext_encrypts_differently(self):
        """Fernet 具备随机 IV，同一明文两次加密结果不同。"""
        a = crypto.encrypt_secret("sk-x")
        b = crypto.encrypt_secret("sk-x")
        assert a != b
        assert crypto.decrypt_secret(a) == crypto.decrypt_secret(b) == "sk-x"


# ==================== 服务层 ====================

class TestLLMConfigService:
    async def test_upsert_and_resolve_openai(self, db_session):
        await upsert_config(
            db_session,
            "ent-1",
            LLMApiConfigUpdate(
                openai_enabled=True,
                openai_api_base="https://api.example.com/v1",
                openai_api_key="sk-openai-123",
                openai_model="gpt-4o",
            ),
        )
        config = await get_config(db_session, "ent-1")
        assert config is not None
        # 落库的是密文，不是明文
        assert config.openai_api_key != "sk-openai-123"
        assert "sk-openai-123" not in (config.openai_api_key or "")

        resolved = resolve_enterprise_llm(config)
        assert resolved["provider"] == "openai"
        assert resolved["api_key"] == "sk-openai-123"
        assert resolved["model"] == "openai/gpt-4o"  # 自动补 provider 前缀

        # 视图只含掩码
        view = to_view(config)
        assert view.openai_has_key is True
        assert "sk-openai-123" not in view.openai_api_key_masked

    async def test_resolve_anthropic(self, db_session):
        await upsert_config(
            db_session,
            "ent-2",
            LLMApiConfigUpdate(
                anthropic_enabled=True,
                anthropic_api_base="https://api.anthropic.com",
                anthropic_api_key="sk-ant-456",
                anthropic_model="claude-3-5-sonnet-20241022",
            ),
        )
        config = await get_config(db_session, "ent-2")
        resolved = resolve_enterprise_llm(config)
        assert resolved["provider"] == "anthropic"
        assert resolved["api_key"] == "sk-ant-456"
        assert resolved["model"] == "anthropic/claude-3-5-sonnet-20241022"

    async def test_resolve_none_when_disabled(self, db_session):
        await upsert_config(
            db_session,
            "ent-3",
            LLMApiConfigUpdate(openai_enabled=False, anthropic_enabled=False),
        )
        config = await get_config(db_session, "ent-3")
        resolved = resolve_enterprise_llm(config)
        assert resolved["provider"] is None
        assert resolved["api_key"] is None

    async def test_keep_key_when_blank(self, db_session):
        """更新时 api_key 留空应保留原密钥。"""
        await upsert_config(
            db_session,
            "ent-4",
            LLMApiConfigUpdate(
                openai_enabled=True,
                openai_api_key="sk-keep-me",
                openai_model="gpt-4o",
            ),
        )
        await upsert_config(
            db_session,
            "ent-4",
            LLMApiConfigUpdate(openai_enabled=True, openai_api_key=None, openai_model="gpt-4o-mini"),
        )
        config = await get_config(db_session, "ent-4")
        resolved = resolve_enterprise_llm(config)
        assert resolved["api_key"] == "sk-keep-me"


# ==================== API 端点 ====================

class _Helper:
    @staticmethod
    async def register(client, email):
        resp = await client.post("/api/v1/auth/register", json={
            "email": email, "name": "测试用户", "password": "Test1234!",
        })
        assert resp.status_code == 201, resp.text

    @staticmethod
    async def create_enterprise(client, name="测试企业"):
        resp = await client.post("/api/v1/enterprises", json={"name": name})
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]["id"]


class TestLLMConfigAPI:
    @pytest.fixture(autouse=True)
    def _enable_registration(self, monkeypatch):
        """测试环境允许注册（根 .env 可能关闭公开注册，这里临时开启以便走注册→建企业流程）。"""
        monkeypatch.setattr("app.config.settings.REGISTRATION_ENABLED", True)

    async def test_put_then_get_masked(self, client):
        await _Helper.register(client, "llm@test.com")
        ent = await _Helper.create_enterprise(client, "c1")
        # 保存配置
        put = await client.put(f"/api/v1/llm-config/{ent}", json={
            "openai_enabled": True,
            "openai_api_base": "https://api.example.com/v1",
            "openai_api_key": "sk-top-secret-999",
            "openai_model": "gpt-4o",
        })
        assert put.status_code == 200, put.text
        data = put.json()["data"]
        assert data["openai_has_key"] is True
        assert "sk-top-secret-999" not in put.text  # 响应绝不包含明文密钥

        # 读取（掩码）
        get = await client.get(f"/api/v1/llm-config/{ent}")
        assert get.status_code == 200
        view = get.json()["data"]
        assert view["openai_enabled"] is True
        assert view["openai_model"] == "gpt-4o"
        assert "sk-top-secret-999" not in get.text
        assert view["openai_api_key_masked"].startswith("sk-****")

    async def test_member_cannot_update(self, client):
        from tests.test_enterprise_management import (
            _invite_and_join,
            _restore_cookies,
            _save_cookies,
        )
        await _Helper.register(client, "llmadmin@test.com")
        ent = await _Helper.create_enterprise(client, "c2")
        member_cookies = await _invite_and_join(client, ent, "llmmember@test.com")
        _restore_cookies(client, member_cookies)
        resp = await client.put(f"/api/v1/llm-config/{ent}", json={
            "openai_enabled": True, "openai_api_key": "sk-x", "openai_model": "gpt-4o",
        })
        assert resp.status_code == 403

    async def test_cross_enterprise_isolation(self, client):
        await _Helper.register(client, "llma@test.com")
        ent_a = await _Helper.create_enterprise(client, "cA")
        # 在 A 保存
        await client.put(f"/api/v1/llm-config/{ent_a}", json={
            "openai_enabled": True, "openai_api_key": "sk-A", "openai_model": "gpt-4o",
        })
        # 切换用户到 B 企业
        await _Helper.register(client, "llmb@test.com")
        ent_b = await _Helper.create_enterprise(client, "cB")
        # B 读取 A 的配置应被拒绝（企业隔离）
        resp = await client.get(f"/api/v1/llm-config/{ent_a}")
        assert resp.status_code == 403
        # B 读取自己的为 null
        resp2 = await client.get(f"/api/v1/llm-config/{ent_b}")
        assert resp2.status_code == 200
        assert resp2.json()["data"] is None
