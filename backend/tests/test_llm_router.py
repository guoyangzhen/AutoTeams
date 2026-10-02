"""AutoTeams 5.0 多模型路由与 BYOK 单元测试。

覆盖决策四的三条硬性要求：
1. **DeepSeek 优先** —— 默认主力模型必须是 DeepSeek；
2. **Kimi / GLM-4 按场景备选** —— 日常对话备选 Kimi、复杂推理备选 GLM-4；
3. **BYOK 运行时即时切换** —— 设置/清除会话级 BYOK 无需重启即生效，
   且优先级高于 settings 全局凭证。

全部用例不发起真实网络请求：只验证**路由与凭证决策**，
实际 HTTP 行为由既有 test_litellm_integration.py 覆盖。
"""
import pytest

from app.config import settings
from app.services.ai import credentials, registry
from app.services.ai.circuit import CircuitBreaker, LLMCircuitOpenError
from app.services.ai.credentials import ByokProfile
from app.services.ai.registry import ModelTier, Provider, TaskType
from app.services.ai.router import ModelRouter
from app.utils.credential_crypto import encrypt_credential

_CRED_KEY = {
    Provider.DEEPSEEK: "DEEPSEEK_API_KEY",
    Provider.MOONSHOT: "MOONSHOT_API_KEY",
    Provider.ZHIPU: "ZHIPU_API_KEY",
    Provider.OPENAI: "OPENAI_API_KEY",
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例从"全部 provider 未配凭证"的干净状态出发，避免用例间串味。"""
    for key in _CRED_KEY.values():
        monkeypatch.setattr(settings, key, "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", "")
    monkeypatch.setattr(settings, "DEFAULT_LLM_PROVIDER", "openai")
    # 会话级 BYOK 是进程全局状态，必须逐用例复位
    credentials.set_active_byok(None)
    yield
    credentials.set_active_byok(None)


def _enable(monkeypatch, provider: Provider) -> None:
    """给某 provider 配置凭证。"""
    monkeypatch.setattr(settings, _CRED_KEY[provider], f"sk-{provider.value}")


# ---------------------------------------------------------------------------
# 1. 决策四路由矩阵
# ---------------------------------------------------------------------------

class TestRoutingMatrix:
    """决策四规定的任务类型 → 默认/备选模型。"""

    def test_conversation_falls_back_to_kimi(self):
        """日常对话/客服：DeepSeek 主力，Kimi 备选。"""
        assert registry.routing_chain(TaskType.CONVERSATION) == (
            Provider.DEEPSEEK,
            Provider.MOONSHOT,
        )

    def test_reasoning_falls_back_to_glm(self):
        """复杂推理/分析：DeepSeek 主力，GLM-4 备选。"""
        assert registry.routing_chain(TaskType.REASONING) == (
            Provider.DEEPSEEK,
            Provider.ZHIPU,
        )

    def test_code_prefers_external_agent(self):
        """代码相关：决策四要求外接 Codex/Claude，DeepSeek 兜底。"""
        chain = registry.routing_chain(TaskType.CODE)
        assert chain[0] is Provider.EXTERNAL
        assert Provider.DEEPSEEK in chain

    def test_summarization_falls_back_to_kimi(self):
        """文档总结/摘要：DeepSeek 主力，Kimi 备选。"""
        assert registry.routing_chain(TaskType.SUMMARIZATION) == (
            Provider.DEEPSEEK,
            Provider.MOONSHOT,
        )

    def test_deepseek_is_default_primary_when_configured(self, monkeypatch):
        """配置了 DeepSeek 时，默认主力模型必须是 DeepSeek。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        model = registry.default_primary_model()
        assert model is not None
        assert model.startswith("deepseek/")

    def test_deepseek_preferred_over_kimi_for_conversation(self, monkeypatch):
        """两者都配置时，日常对话仍以 DeepSeek 为首选（决策四的"DeepSeek 优先"）。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        _enable(monkeypatch, Provider.MOONSHOT)
        router = ModelRouter(CircuitBreaker())
        usable = [c.model for c in router.build_candidates(TaskType.CONVERSATION) if c.usable]
        assert usable[0].startswith("deepseek/")
        assert any(m.startswith("moonshot/") for m in usable)

    def test_reasoning_prefers_deepseek_then_glm(self, monkeypatch):
        """复杂推理：DeepSeek 优先，GLM-4 紧随其后。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        _enable(monkeypatch, Provider.ZHIPU)
        router = ModelRouter(CircuitBreaker())
        usable = [c.model for c in router.build_candidates(TaskType.REASONING) if c.usable]
        assert usable[0].startswith("deepseek/")
        assert usable[1].startswith("zhipu/")


class TestTierMapping:
    """4.0 的 strong/cheap 层级语义映射到 5.0 任务类型。"""

    @pytest.mark.parametrize(
        "tier,expected",
        [
            (ModelTier.STRONG, TaskType.REASONING),
            (ModelTier.CHEAP, TaskType.SUMMARIZATION),
            (ModelTier.DEFAULT, TaskType.CONVERSATION),
        ],
    )
    def test_tier_maps_to_task_type(self, tier, expected):
        assert registry.task_type_for_tier(tier) is expected

    def test_unknown_tier_falls_back_to_conversation(self):
        assert registry.task_type_for_tier("不存在的层级") is TaskType.CONVERSATION


# ---------------------------------------------------------------------------
# 2. 候选链装配
# ---------------------------------------------------------------------------

class TestCandidateChain:
    def test_unconfigured_provider_marked_unusable_not_dropped(self):
        """未配凭证的 provider 保留在链上但标记不可用，便于设置页展示缺口。"""
        router = ModelRouter(CircuitBreaker())
        chain = router.build_candidates(TaskType.CONVERSATION)
        assert chain, "矩阵链不应为空"
        assert all(not c.usable for c in chain)
        assert any(c.reason == "no_credentials" for c in chain)

    def test_explicit_model_takes_over_entirely(self, monkeypatch):
        """调用点显式指定模型时完全接管，不再追加矩阵候选。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        router = ModelRouter(CircuitBreaker())
        chain = router.build_candidates(
            TaskType.CONVERSATION, explicit_model="openai/gpt-4o-mini"
        )
        assert len(chain) == 1
        assert chain[0].model == "openai/gpt-4o-mini"
        assert chain[0].reason == "explicit"

    def test_service_default_used_only_when_matrix_unusable(self):
        """矩阵全不可用时，实例自带模型/凭证作为末位兜底。"""
        router = ModelRouter(CircuitBreaker())
        chain = router.build_candidates(
            TaskType.CONVERSATION,
            service_model="gpt-4",
            service_key="sk-instance",
        )
        usable = [c for c in chain if c.usable]
        assert len(usable) == 1
        assert usable[0].model == "openai/gpt-4"
        assert usable[0].reason == "service_default"

    def test_configured_fallback_models_appended(self, monkeypatch):
        """运维在 LLM_FALLBACK_MODELS 配的模型追加到链尾。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        monkeypatch.setattr(
            settings, "LLM_FALLBACK_MODELS", "openai/gpt-4o , , zhipu/glm-4"
        )
        router = ModelRouter(CircuitBreaker())
        models = [c.model for c in router.build_candidates(TaskType.CONVERSATION)]
        assert "openai/gpt-4o" in models
        assert "zhipu/glm-4" in models

    def test_route_snapshot_never_leaks_credentials(self):
        """凭证不得出现在 repr / 快照中（防止日志泄露 BYOK）。"""
        router = ModelRouter(CircuitBreaker())
        chain = router.build_candidates(
            TaskType.CONVERSATION, service_model="gpt-4", service_key="sk-super-secret"
        )
        assert "sk-super-secret" not in repr(chain)
        for entry in router.describe_route(TaskType.CONVERSATION):
            assert "api_key" not in entry


# ---------------------------------------------------------------------------
# 3. BYOK：企业私有 Key 的运行时即时切换
# ---------------------------------------------------------------------------

class TestByokRuntimeSwitch:
    def test_byok_switches_credentials_without_restart(self, monkeypatch):
        """核心诉求：设置页保存 BYOK 后，下一次调用立即走新凭证。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        router = ModelRouter(CircuitBreaker())

        before = [c for c in router.build_candidates(TaskType.CONVERSATION) if c.usable]
        assert before[0].api_key == "sk-deepseek"

        credentials.set_active_byok(
            ByokProfile(provider="moonshot", api_key="sk-enterprise-kimi")
        )

        byok_candidate = next(
            c for c in router.build_candidates(TaskType.CONVERSATION) if c.reason == "byok"
        )
        assert byok_candidate.api_key == "sk-enterprise-kimi"
        assert byok_candidate.model.startswith("moonshot/")

    def test_byok_takes_priority_over_settings(self, monkeypatch):
        """BYOK 必须压过全局 settings 凭证。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        _enable(monkeypatch, Provider.OPENAI)
        credentials.set_active_byok(
            ByokProfile(
                provider="deepseek", api_key="sk-byok", model="deepseek/deepseek-reasoner"
            )
        )
        router = ModelRouter(CircuitBreaker())
        byok_candidate = next(
            c for c in router.build_candidates(TaskType.CONVERSATION) if c.reason == "byok"
        )
        assert byok_candidate.api_key == "sk-byok"
        assert byok_candidate.model == "deepseek/deepseek-reasoner"

    def test_clearing_byok_restores_settings_credentials(self, monkeypatch):
        """清除 BYOK 后回落到全局凭证（设置页"恢复默认"）。"""
        _enable(monkeypatch, Provider.DEEPSEEK)
        router = ModelRouter(CircuitBreaker())

        credentials.set_active_byok(ByokProfile(provider="zhipu", api_key="sk-byok"))
        assert any(
            c.reason == "byok" for c in router.build_candidates(TaskType.CONVERSATION)
        )

        credentials.set_active_byok(None)
        chain = router.build_candidates(TaskType.CONVERSATION)
        assert not any(c.reason == "byok" for c in chain)
        assert [c for c in chain if c.usable][0].api_key == "sk-deepseek"

    def test_call_site_byok_overrides_session_byok(self):
        """调用点 BYOK 优先于会话级 BYOK。"""
        credentials.set_active_byok(ByokProfile(provider="zhipu", api_key="sk-session"))
        router = ModelRouter(CircuitBreaker())
        chain = router.build_candidates(
            TaskType.CONVERSATION,
            byok=ByokProfile(provider="moonshot", api_key="sk-call-site"),
        )
        assert next(c for c in chain if c.reason == "byok").api_key == "sk-call-site"

    def test_byok_with_unknown_openai_compatible_provider(self):
        """任意 OpenAI 兼容自建端点（ollama/vllm）应能通过 BYOK 接入。"""
        credentials.set_active_byok(
            ByokProfile(
                provider="vllm",
                api_key="sk-selfhost",
                model="my-org/custom-7b",
                api_base="http://10.0.0.5:8000/v1",
            )
        )
        router = ModelRouter(CircuitBreaker())
        candidate = next(
            c for c in router.build_candidates(TaskType.CONVERSATION) if c.reason == "byok"
        )
        assert candidate.model == "my-org/custom-7b"
        assert candidate.api_base == "http://10.0.0.5:8000/v1"
        assert candidate.api_key == "sk-selfhost"

    def test_byok_unknown_provider_without_model_is_skipped(self):
        """自建 provider 未给 model 时无法推断，跳过而非发出必失败的请求。"""
        credentials.set_active_byok(
            ByokProfile(provider="ollama", api_key="sk-x", model=None)
        )
        router = ModelRouter(CircuitBreaker())
        chain = router.build_candidates(TaskType.CONVERSATION)
        assert not any(c.reason == "byok" for c in chain)

    @pytest.mark.parametrize(
        "kwargs", [{"provider": "", "api_key": "k"}, {"provider": "deepseek", "api_key": ""}]
    )
    def test_byok_rejects_incomplete_profile(self, kwargs):
        """BYOK 缺少 provider 或 key 时直接拒绝，不做静默兜底。"""
        with pytest.raises(ValueError):
            ByokProfile(**kwargs)

    def test_encrypted_byok_key_is_decrypted_on_load(self):
        """BYOK 落库为密文，由 from_encrypted 严格解密成内存态（决策四：密钥加密存储）。"""
        encrypted = encrypt_credential("sk-plaintext-key")
        profile = ByokProfile.from_encrypted(
            provider="deepseek", api_key_cipher=encrypted
        )
        assert profile.key == "sk-plaintext-key"

    def test_broken_ciphertext_raises_instead_of_silently_passing(self):
        """解密失败必须显式失败，不得回退明文（P2-11 的既有结论）。"""
        with pytest.raises(Exception):
            ByokProfile.from_encrypted(
                provider="deepseek", api_key_cipher="明文但非合法密文!!"
            )


# ---------------------------------------------------------------------------
# 4. 凭证解析
# ---------------------------------------------------------------------------

class TestCredentialResolution:
    def test_resolves_per_provider_prefix(self, monkeypatch):
        """凭证按模型 provider 前缀解析，主备可来自不同供应商。"""
        monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-ds")
        monkeypatch.setattr(settings, "MOONSHOT_API_KEY", "sk-ms")
        assert credentials.resolve_credentials("moonshot/moonshot-v1-32k")[0] == "sk-ms"
        assert credentials.resolve_credentials("deepseek/deepseek-chat")[0] == "sk-ds"

    def test_unknown_provider_falls_back_to_openai_settings(self, monkeypatch):
        """未知/自建 provider 回落 OpenAI 兼容全局配置。"""
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
        assert credentials.resolve_credentials("vllm/some-model")[0] == "sk-openai"

    def test_missing_credentials_return_none(self):
        """未配置任何 Key 时返回 None，由上层决定演示模式或抛错（不编造凭证）。"""
        assert credentials.resolve_credentials("deepseek/deepseek-chat")[0] is None

    def test_provider_of_extracts_prefix(self):
        assert credentials.provider_of("deepseek/deepseek-chat") == "deepseek"
        assert credentials.provider_of("glm-4") == "openai"  # 无前缀 → 默认 provider


# ---------------------------------------------------------------------------
# 5. 熔断
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_opens_after_threshold_failures(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3)
        breaker = CircuitBreaker()
        breaker.record_failure("deepseek/deepseek-chat")
        breaker.record_failure("deepseek/deepseek-chat")
        assert not breaker.is_open("deepseek/deepseek-chat")
        breaker.record_failure("deepseek/deepseek-chat")
        assert breaker.is_open("deepseek/deepseek-chat")

    def test_success_resets_state(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 2)
        breaker = CircuitBreaker()
        breaker.record_failure("m")
        breaker.record_success("m")
        assert breaker.streak("m") == 0
        assert not breaker.is_open("m")

    def test_router_raises_when_circuit_open(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 1)
        router = ModelRouter(CircuitBreaker())
        model = "deepseek/deepseek-chat"
        router.record_failure(model)
        with pytest.raises(LLMCircuitOpenError):
            router.raise_if_circuit_open(model)
