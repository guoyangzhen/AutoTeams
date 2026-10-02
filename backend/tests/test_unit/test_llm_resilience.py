"""LLM 调用韧性策略回归测试。"""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.llm_service import LLMCircuitOpenError, LLMService


class RetryableError(RuntimeError):
    status_code = 429


class InvalidRequestError(RuntimeError):
    status_code = 400


class FakeLiteLLM:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def acompletion(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def resilient_service(monkeypatch):
    service = LLMService(api_key="test-key")
    service._use_fallback = False
    service._litellm_available = True
    monkeypatch.setattr(settings, "LLM_REQUEST_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "LLM_RETRY_INITIAL_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS", 60)
    return service


@pytest.mark.asyncio
async def test_retryable_error_retries_then_succeeds(resilient_service):
    expected = SimpleNamespace(choices=[])
    fake = FakeLiteLLM([RetryableError("rate limited"), expected])
    resilient_service._litellm = fake

    result = await resilient_service._completion_with_resilience(model="openai/test", messages=[])

    assert result is expected
    assert fake.calls == 2
    assert not resilient_service._circuit_is_open("openai/test")


@pytest.mark.asyncio
async def test_invalid_request_is_not_retried(resilient_service):
    fake = FakeLiteLLM([InvalidRequestError("invalid request")])
    resilient_service._litellm = fake

    with pytest.raises(InvalidRequestError):
        await resilient_service._completion_with_resilience(model="openai/test", messages=[])

    assert fake.calls == 1


@pytest.mark.asyncio
async def test_repeated_terminal_failures_open_circuit(resilient_service):
    resilient_service._litellm = FakeLiteLLM([InvalidRequestError("bad"), InvalidRequestError("bad")])

    for _ in range(2):
        with pytest.raises(InvalidRequestError):
            await resilient_service._completion_with_resilience(model="openai/test", messages=[])

    with pytest.raises(LLMCircuitOpenError):
        await resilient_service._completion_with_resilience(model="openai/test", messages=[])
