"""P3-5: app/services/llm_service.py 覆盖率补充测试。"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.services.llm_service import LLMService, ModelTier, LLMUsageStats


class TestUtilityMethods:
    """内部工具方法测试。"""

    def test_get_model_for_tier(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.OPENAI_MODEL_STRONG", "gpt-4")
        monkeypatch.setattr("app.services.llm_service.settings.OPENAI_MODEL_CHEAP", "gpt-3.5-turbo")
        service = LLMService(api_key="sk-test", model="gpt-4")

        assert service.get_model_for_tier(ModelTier.STRONG) == "gpt-4"
        assert service.get_model_for_tier(ModelTier.CHEAP) == "gpt-3.5-turbo"
        assert service.get_model_for_tier(ModelTier.DEFAULT) == "gpt-4"

    def test_normalize_model_adds_prefix(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.llm_service.settings.DEFAULT_LLM_PROVIDER", "openai"
        )
        service = LLMService(api_key="sk-test")
        assert service._normalize_model("gpt-4") == "openai/gpt-4"
        assert service._normalize_model("anthropic/claude-3") == "anthropic/claude-3"

    def test_normalize_model_domestic_providers(self, monkeypatch):
        """P0-S2: 国产模型 provider 下，纯模型名自动拼接 provider 前缀。"""
        for provider, model, expected in [
            ("doubao", "doubao-pro-32k", "doubao/doubao-pro-32k"),
            ("qwen", "qwen-plus", "qwen/qwen-plus"),
            ("zhipu", "glm-4-air", "zhipu/glm-4-air"),
            ("deepseek", "deepseek-chat", "deepseek/deepseek-chat"),
            ("moonshot", "moonshot-v1-32k", "moonshot/moonshot-v1-32k"),
        ]:
            monkeypatch.setattr(
                "app.services.llm_service.settings.DEFAULT_LLM_PROVIDER", provider
            )
            service = LLMService(api_key="sk-test")
            assert service._normalize_model(model) == expected

    def test_get_preset_model_domestic(self, monkeypatch):
        """P0-S2: 国产 provider 的三层预设模型。"""
        monkeypatch.setattr("app.services.llm_service.settings.DEFAULT_LLM_PROVIDER", "doubao")
        assert LLMService.get_preset_model(ModelTier.STRONG) == "doubao/doubao-pro-128k"
        assert LLMService.get_preset_model(ModelTier.CHEAP) == "doubao/doubao-lite-32k"
        assert LLMService.get_preset_model(ModelTier.DEFAULT) == "doubao/doubao-pro-32k"

    def test_get_model_for_tier_falls_back_to_preset(self, monkeypatch):
        """P0-S2: 未显式配置 STRONG/CHEAP 时回退到 provider 预设。"""
        monkeypatch.setattr("app.services.llm_service.settings.DEFAULT_LLM_PROVIDER", "qwen")
        monkeypatch.setattr("app.services.llm_service.settings.OPENAI_MODEL_STRONG", "")
        monkeypatch.setattr("app.services.llm_service.settings.OPENAI_MODEL_CHEAP", "")
        monkeypatch.setattr("app.services.llm_service.settings.OPENAI_MODEL", "qwen/qwen-plus")
        service = LLMService(api_key="sk-test")

        assert service.get_model_for_tier(ModelTier.STRONG) == "qwen/qwen-max"
        assert service.get_model_for_tier(ModelTier.CHEAP) == "qwen/qwen-turbo"
        assert service.get_model_for_tier(ModelTier.DEFAULT) == "qwen/qwen-plus"


class TestFallbackResponses:
    """演示模式响应测试。"""

    @pytest.fixture
    def fallback_service(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_service.settings.LLM_ALLOW_FALLBACK", True)
        monkeypatch.setattr("app.services.llm_service.settings.OPENAI_API_KEY", "")
        monkeypatch.setattr("app.services.llm_service.settings.DEEPSEEK_API_KEY", "")
        return LLMService(api_key="", model="gpt-4")

    @pytest.mark.asyncio
    async def test_fallback_greeting(self, fallback_service):
        reply = await fallback_service.chat([{"role": "user", "content": "你好"}])
        assert "AutoTeams" in reply

    @pytest.mark.asyncio
    async def test_fallback_knowledge(self, fallback_service):
        reply = await fallback_service.chat([{"role": "user", "content": "知识库"}])
        assert "知识库" in reply

    @pytest.mark.asyncio
    async def test_fallback_generic(self, fallback_service):
        reply = await fallback_service.chat([{"role": "user", "content": "随机问题"}])
        assert "收到您的问题" in reply

    @pytest.mark.asyncio
    async def test_chat_stream_fallback(self, fallback_service):
        stream = fallback_service.chat_stream([{"role": "user", "content": "你好"}])
        chunks = [chunk async for chunk in stream]
        assert chunks
        assert "AutoTeams" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_chat_with_image_fallback(self, fallback_service):
        reply = await fallback_service.chat_with_image([{"role": "user", "content": [{"type": "image_url"}]}])
        assert "图片分析" in reply


class TestLitellmPath:
    """LiteLLM 调用路径测试。"""

    def _make_service(self):
        service = LLMService(api_key="sk-test", model="gpt-4")
        mock_litellm = MagicMock()
        service._litellm = mock_litellm
        service._litellm_available = True
        service._use_fallback = False
        return service, mock_litellm

    @pytest.mark.asyncio
    async def test_chat_success(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.llm_service.settings.DEFAULT_LLM_PROVIDER", "openai"
        )
        service, mock_litellm = self._make_service()
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15
        mock_response = MagicMock()
        mock_response.usage = mock_usage
        mock_choice = MagicMock()
        mock_choice.message.content = "hello"
        mock_response.choices = [mock_choice]
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        mock_litellm.completion_cost.return_value = 0.001

        stats = LLMUsageStats()
        reply = await service.chat([{"role": "user", "content": "hi"}], stats=stats)

        assert reply == "hello"
        assert stats.model_used == "openai/gpt-4"
        assert stats.total_tokens == 15
        assert stats.cost_usd == 0.001
        assert not stats.fallback_used

    @pytest.mark.asyncio
    async def test_chat_cost_calculation_failure_does_not_block(self):
        """成本计算失败不应阻断主流程。"""
        service, mock_litellm = self._make_service()
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15
        mock_response = MagicMock()
        mock_response.usage = mock_usage
        mock_choice = MagicMock()
        mock_choice.message.content = "hello"
        mock_response.choices = [mock_choice]
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        mock_litellm.completion_cost.side_effect = ValueError("成本计算失败")

        stats = LLMUsageStats()
        reply = await service.chat([{"role": "user", "content": "hi"}], stats=stats)

        assert reply == "hello"
        assert stats.total_tokens == 15
        assert stats.cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_chat_fallback_when_all_models_fail(self, monkeypatch):
        service, mock_litellm = self._make_service()
        mock_litellm.acompletion = AsyncMock(side_effect=Exception("boom"))
        monkeypatch.setattr("app.services.llm_service.settings.LLM_ALLOW_FALLBACK", True)

        reply = await service.chat([{"role": "user", "content": "hi"}])
        assert "收到您的问题" in reply

    @pytest.mark.asyncio
    async def test_chat_stream_success(self):
        service, mock_litellm = self._make_service()

        async def _async_stream():
            chunks = ["hel", "lo"]
            for c in chunks:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = c
                yield chunk

        mock_litellm.acompletion = AsyncMock(return_value=_async_stream())
        stream = service.chat_stream([{"role": "user", "content": "hi"}])
        result = [c async for c in stream]
        assert "".join(result) == "hello"

    @pytest.mark.asyncio
    async def test_chat_with_image_success(self):
        service, mock_litellm = self._make_service()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "图中是一只猫"
        mock_response.choices = [mock_choice]
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)

        reply = await service.chat_with_image([{"role": "user", "content": [{"type": "image_url"}]}])
        assert reply == "图中是一只猫"

    @pytest.mark.asyncio
    async def test_chat_with_image_failure(self, monkeypatch):
        service, mock_litellm = self._make_service()
        mock_litellm.acompletion = AsyncMock(side_effect=Exception("boom"))
        monkeypatch.setattr("app.services.llm_service.settings.LLM_ALLOW_FALLBACK", True)

        reply = await service.chat_with_image([{"role": "user", "content": [{"type": "image_url"}]}])
        assert "暂时不可用" in reply
