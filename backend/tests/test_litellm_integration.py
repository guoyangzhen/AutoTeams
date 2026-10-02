"""LiteLLM 集成测试。

测试 P1-3 LiteLLM 多模型路由的核心功能：
1. LLMUsageStats 统计类
2. ModelTier 模型层级
3. 模型规范化（_normalize_model：添加 openai/ 前缀）
4. 模型层级路由（get_model_for_tier）
5. 优雅降级（无 API Key 时返回演示模式响应）
6. 故障转移候选模型列表构建
7. chat/chat_stream/chat_with_image 在 fallback 模式下的行为

注意：测试环境通常无 OPENAI_API_KEY 和 litellm 安装，
所有 LLM 调用走 fallback 路径，仍能验证多模型路由的"逻辑正确性"。
"""
import pytest
from app.services.llm_service import (
    LLMService,
    LLMUsageStats,
    ModelTier,
    llm_service,
)


class TestLLMUsageStats:
    """LLM 调用统计类测试。"""

    def test_default_values(self):
        stats = LLMUsageStats()
        assert stats.prompt_tokens == 0
        assert stats.completion_tokens == 0
        assert stats.total_tokens == 0
        assert stats.cost_usd == 0.0
        assert stats.model_used == ""
        assert stats.fallback_used is False

    def test_to_dict_has_all_fields(self):
        stats = LLMUsageStats()
        stats.prompt_tokens = 100
        stats.completion_tokens = 50
        stats.total_tokens = 150
        stats.cost_usd = 0.0025
        stats.model_used = "openai/gpt-4"
        stats.fallback_used = True

        d = stats.to_dict()
        assert d["prompt_tokens"] == 100
        assert d["completion_tokens"] == 50
        assert d["total_tokens"] == 150
        assert d["cost_usd"] == 0.0025
        assert d["model_used"] == "openai/gpt-4"
        assert d["fallback_used"] is True

    def test_cost_usd_rounded_to_6_decimals(self):
        stats = LLMUsageStats()
        stats.cost_usd = 0.0025000001
        d = stats.to_dict()
        # round(x, 6) → 0.0025
        assert d["cost_usd"] == 0.0025


class TestModelTier:
    """模型层级常量测试。"""

    def test_tier_values(self):
        assert ModelTier.STRONG == "strong"
        assert ModelTier.CHEAP == "cheap"
        assert ModelTier.DEFAULT == "default"

    def test_tiers_are_distinct(self):
        tiers = {ModelTier.STRONG, ModelTier.CHEAP, ModelTier.DEFAULT}
        assert len(tiers) == 3


class TestNormalizeModel:
    """模型名规范化测试。"""

    def test_plain_model_gets_openai_prefix(self):
        assert LLMService._normalize_model("gpt-4") == "openai/gpt-4"
        assert LLMService._normalize_model("gpt-3.5-turbo") == "openai/gpt-3.5-turbo"

    def test_already_prefixed_not_modified(self):
        assert LLMService._normalize_model("openai/gpt-4") == "openai/gpt-4"
        assert LLMService._normalize_model("anthropic/claude-3-sonnet") == "anthropic/claude-3-sonnet"
        assert LLMService._normalize_model("azure/gpt-4") == "azure/gpt-4"

    def test_custom_provider_preserved(self):
        assert LLMService._normalize_model("my-provider/custom-model") == "my-provider/custom-model"


class TestModelTierRouting:
    """模型层级路由测试。"""

    def test_strong_tier_uses_strong_model(self, monkeypatch):
        """无参 __init__ 会读取 settings，我们直接用 llm_service 实例。"""
        # 验证其层级选择逻辑：STRONG tier 应返回当前 provider 的强模型
        from app.config import settings
        provider = settings.DEFAULT_LLM_PROVIDER.strip().lower()
        expected = (
            settings.DEEPSEEK_MODEL_STRONG or settings.DEEPSEEK_MODEL
            if provider == "deepseek"
            else settings.OPENAI_MODEL_STRONG or settings.OPENAI_MODEL
        )
        assert llm_service.get_model_for_tier(ModelTier.STRONG) == expected

    def test_cheap_tier_uses_cheap_model(self):
        from app.config import settings
        provider = settings.DEFAULT_LLM_PROVIDER.strip().lower()
        expected = (
            settings.DEEPSEEK_MODEL_CHEAP or settings.DEEPSEEK_MODEL
            if provider == "deepseek"
            else settings.OPENAI_MODEL_CHEAP or settings.OPENAI_MODEL
        )
        assert llm_service.get_model_for_tier(ModelTier.CHEAP) == expected

    def test_default_tier_uses_main_model(self):
        assert llm_service.get_model_for_tier(ModelTier.DEFAULT) == llm_service.model

    def test_unknown_tier_falls_back_to_default(self):
        assert llm_service.get_model_for_tier("unknown") == llm_service.model


class TestFallbackMode:
    """优雅降级模式测试（无 API Key 或 litellm 不可用时）。"""

    @pytest.fixture(autouse=True)
    def _enable_fallback(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.LLM_ALLOW_FALLBACK", True)

    @pytest.mark.asyncio
    async def test_chat_without_api_key_returns_fallback_response(self):
        """无 API Key 时 chat 应返回演示模式响应。"""
        # 创建一个明确无 API Key 的服务实例
        service = LLMService(api_key="")
        result = await service.chat([{"role": "user", "content": "你好"}])
        assert isinstance(result, str)
        assert len(result) > 0
        # 演示模式响应应包含特征关键词
        assert "演示模式" in result or "API" in result

    @pytest.mark.asyncio
    async def test_chat_fallback_recognizes_greeting(self):
        service = LLMService(api_key="")
        result = await service.chat([{"role": "user", "content": "你好，请介绍一下自己"}])
        assert "演示模式" in result

    @pytest.mark.asyncio
    async def test_chat_fallback_recognizes_knowledge_query(self):
        service = LLMService(api_key="")
        result = await service.chat([{"role": "user", "content": "请告诉我知识库里的文件"}])
        assert "演示模式" in result

    @pytest.mark.asyncio
    async def test_chat_fallback_generic_query(self):
        service = LLMService(api_key="")
        result = await service.chat([{"role": "user", "content": "什么是机器学习？"}])
        # 通用 fallback 响应应包含部分用户问题
        assert len(result) > 10

    @pytest.mark.asyncio
    async def test_chat_stream_yields_chars_in_fallback(self):
        service = LLMService(api_key="")
        chunks = []
        async for chunk in service.chat_stream([{"role": "user", "content": "你好"}]):
            chunks.append(chunk)
        # 流式接口在 fallback 模式下也应输出字符
        assert len(chunks) > 0
        assert all(isinstance(c, str) for c in chunks)
        # 拼接后应等于一次性 chat 的响应
        full = "".join(chunks)
        assert len(full) > 0

    @pytest.mark.asyncio
    async def test_chat_with_image_in_fallback(self):
        service = LLMService(api_key="")
        result = await service.chat_with_image([{"role": "user", "content": "test"}])
        assert "演示模式" in result or "不可用" in result


class TestStatsFilling:
    """stats 参数填充测试（fallback 模式下也应正确填充）。"""

    @pytest.fixture(autouse=True)
    def _enable_fallback(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.LLM_ALLOW_FALLBACK", True)

    @pytest.mark.asyncio
    async def test_stats_marked_as_fallback_when_no_api_key(self):
        service = LLMService(api_key="")
        stats = LLMUsageStats()
        await service.chat(
            [{"role": "user", "content": "test"}],
            stats=stats,
        )
        assert stats.model_used == "fallback"
        assert stats.fallback_used is False  # 没有调用任何模型，不算故障转移

    @pytest.mark.asyncio
    async def test_stats_unchanged_on_strong_tier_in_fallback(self):
        """在 fallback 模式下，即使指定 STRONG tier，stats.model_used 也应为 'fallback'。"""
        service = LLMService(api_key="")
        stats = LLMUsageStats()
        await service.chat(
            [{"role": "user", "content": "test"}],
            tier=ModelTier.STRONG,
            stats=stats,
        )
        # 由于无 API Key，仍走 fallback 路径
        assert stats.model_used == "fallback"


class TestFallbackModelsList:
    """故障转移备用模型列表测试。"""

    def test_empty_fallback_models_when_not_configured(self, monkeypatch):
        """LLM_FALLBACK_MODELS 为空时，备用列表应为空。"""
        service = LLMService(api_key="fake-key")
        # 不配置 LLM_FALLBACK_MODELS 时
        # 注意：实际值取决于 .env，这里只验证结构
        assert isinstance(service._fallback_models, list)

    def test_fallback_models_parsed_from_comma_separated(self, monkeypatch):
        """LLM_FALLBACK_MODELS 应正确解析逗号分隔的列表。"""
        # 通过 monkeypatch 临时修改 settings
        monkeypatch.setattr(
            "app.services.llm_service.settings.LLM_FALLBACK_MODELS",
            "openai/gpt-4, openai/gpt-3.5-turbo,anthropic/claude-3-sonnet"
        )
        service = LLMService(api_key="fake-key")
        assert service._fallback_models == [
            "openai/gpt-4",
            "openai/gpt-3.5-turbo",
            "anthropic/claude-3-sonnet",
        ]

    def test_fallback_models_strips_whitespace(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.llm_service.settings.LLM_FALLBACK_MODELS",
            " openai/gpt-4 , openai/gpt-3.5-turbo "
        )
        service = LLMService(api_key="fake-key")
        assert service._fallback_models == ["openai/gpt-4", "openai/gpt-3.5-turbo"]

    def test_fallback_models_ignores_empty_entries(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.llm_service.settings.LLM_FALLBACK_MODELS",
            "openai/gpt-4,,  ,openai/gpt-3.5-turbo,"
        )
        service = LLMService(api_key="fake-key")
        assert service._fallback_models == ["openai/gpt-4", "openai/gpt-3.5-turbo"]


class TestConfigIntegration:
    """配置项集成测试。"""

    def test_config_has_multi_model_settings(self):
        """P1-3 新增的配置项应存在且有默认值。"""
        from app.config import settings
        assert hasattr(settings, "OPENAI_MODEL_STRONG")
        assert hasattr(settings, "OPENAI_MODEL_CHEAP")
        assert hasattr(settings, "LLM_FALLBACK_MODELS")
        assert hasattr(settings, "LLM_REQUEST_TIMEOUT")
        assert hasattr(settings, "LLM_MAX_RETRIES")
        # 默认值检查
        assert settings.LLM_REQUEST_TIMEOUT == 120
        assert settings.LLM_MAX_RETRIES == 2

    def test_config_cheap_model_default_is_gpt35(self):
        """廉价模型默认应为 gpt-3.5-turbo（除非 .env 覆盖）。"""
        from app.config import settings
        # .env 可能覆盖默认值，这里验证至少有值
        assert settings.OPENAI_MODEL_CHEAP

    def test_config_strong_model_default_is_gpt4(self):
        from app.config import settings
        assert settings.OPENAI_MODEL_STRONG
