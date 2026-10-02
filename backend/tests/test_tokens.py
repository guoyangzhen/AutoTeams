"""P3-5: app/utils/tokens.py 覆盖率补充测试。"""
import sys
from types import ModuleType

import pytest
from unittest.mock import MagicMock

from app.utils.tokens import (
    count_text_tokens,
    count_messages_tokens,
    truncate_text_to_budget,
    truncate_messages_to_budget,
)


class TestCountTextTokens:
    """单段文本 token 估算测试。"""

    def test_empty_text_returns_zero(self):
        assert count_text_tokens("") == 0

    def test_simple_english(self):
        # "hello world" 在 cl100k_base 中为 2 tokens
        assert count_text_tokens("hello world") >= 1

    def test_chinese_text(self):
        assert count_text_tokens("你好世界") >= 1

    def test_model_mapping_exists(self):
        # gpt-4o 使用 o200k_base
        assert count_text_tokens("hello", model="gpt-4o") >= 1

    def test_unknown_model_uses_default(self):
        assert count_text_tokens("hello", model="unknown-model") >= 1


class TestCountMessagesTokens:
    """messages 列表 token 估算测试。"""

    def test_empty_messages(self):
        assert count_messages_tokens([]) == 0

    def test_single_message(self):
        messages = [{"role": "user", "content": "hello"}]
        assert count_messages_tokens(messages) > 0

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
        total = count_messages_tokens(messages)
        assert total > 0

    def test_multimodal_message_text_only(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this"},
                {"type": "image_url", "image_url": {"url": "http://example.com/x.png"}},
            ],
        }]
        total = count_messages_tokens(messages, model="gpt-4o")
        assert total > 0

    def test_invalid_content_items_ignored(self):
        messages = [{
            "role": "user",
            "content": [
                "not a dict",
                {"type": "text", "text": "ok"},
            ],
        }]
        assert count_messages_tokens(messages) > 0


class TestTruncateTextToBudget:
    """文本截断测试。"""

    def test_zero_budget_returns_empty(self):
        assert truncate_text_to_budget("hello", 0) == ""

    def test_negative_budget_returns_empty(self):
        assert truncate_text_to_budget("hello", -1) == ""

    def test_text_within_budget_unchanged(self):
        text = "hi"
        assert truncate_text_to_budget(text, 100) == text

    def test_long_text_truncated(self):
        text = "a" * 10000
        result = truncate_text_to_budget(text, 10)
        assert len(result) < len(text)


class TestTruncateMessagesToBudget:
    """消息列表截断测试。"""

    def test_empty_messages(self):
        assert truncate_messages_to_budget([], 100) == []

    def test_messages_within_budget(self):
        messages = [{"role": "user", "content": "hi"}]
        assert truncate_messages_to_budget(messages, 100) == messages

    def test_truncates_single_message(self):
        messages = [{"role": "user", "content": "a" * 10000}]
        result = truncate_messages_to_budget(messages, 10)
        assert len(result) == 1
        assert len(result[0]["content"]) < len(messages[0]["content"])

    def test_keeps_last_message(self):
        messages = [
            {"role": "system", "content": "sys" * 1000},
            {"role": "user", "content": "question"},
        ]
        result = truncate_messages_to_budget(messages, 5)
        # 优先保留最后一条
        assert result[-1]["content"] == "question"


class TestTokensFallback:
    """tiktoken 不可用时降级路径测试。"""

    @pytest.fixture
    def no_tiktoken(self, monkeypatch):
        monkeypatch.setattr("app.utils.tokens._tiktoken_available", False)
        monkeypatch.setattr("app.utils.tokens._encoding_cache", {})

    def test_count_text_tokens_fallback(self, no_tiktoken):
        assert count_text_tokens("abcd", model="gpt-4") == 2

    def test_count_messages_tokens_fallback(self, no_tiktoken):
        messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        total = count_messages_tokens(messages, model="gpt-4")
        assert total > 0

    def test_truncate_text_to_budget_fallback(self, no_tiktoken):
        text = "a" * 100
        result = truncate_text_to_budget(text, 10)
        assert len(result) == 20

    def test_get_encoding_returns_none_when_unavailable(self, no_tiktoken):
        from app.utils.tokens import _get_encoding
        assert _get_encoding("gpt-4") is None


class TestTokensWithTiktoken:
    """模拟 tiktoken 可用时的路径测试。"""

    @pytest.fixture
    def fake_tiktoken(self, monkeypatch):
        """提供一个返回固定 token 数的假 tiktoken encoding。"""
        class FakeEncoding:
            def __init__(self, name):
                self.name = name

            def encode(self, text):
                # 每个单词约 1 token，便于断言
                if not text:
                    return []
                return text.split()

            def decode(self, tokens):
                return " ".join(tokens)

        fake_module = ModuleType("tiktoken")
        fake_module.get_encoding = FakeEncoding

        # 重置全局状态
        monkeypatch.setattr("app.utils.tokens._tiktoken_available", None)
        monkeypatch.setattr("app.utils.tokens._encoding_cache", {})

        original = sys.modules.get("tiktoken")
        sys.modules["tiktoken"] = fake_module
        try:
            yield fake_module
        finally:
            if original is not None:
                sys.modules["tiktoken"] = original
            else:
                sys.modules.pop("tiktoken", None)

    def test_count_text_tokens_with_tiktoken(self, fake_tiktoken):
        # "hello world" 会被 split 成 2 tokens
        assert count_text_tokens("hello world", model="gpt-4") == 2

    def test_count_messages_tokens_with_tiktoken(self, fake_tiktoken):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hello world"},
        ]
        # system: 7 + 3, user: 7 + 2, prefix: 3 => 22
        total = count_messages_tokens(messages, model="gpt-4")
        assert total == 22

    def test_truncate_text_to_budget_with_tiktoken(self, fake_tiktoken):
        text = "a b c d e"
        result = truncate_text_to_budget(text, 3)
        assert result == "a b c"

    def test_truncate_messages_to_budget_truncates_history(self, fake_tiktoken):
        messages = [
            {"role": "system", "content": "one two three four"},
            {"role": "user", "content": "q"},
        ]
        result = truncate_messages_to_budget(messages, 15)
        # 必须保留最后一条 user 消息
        assert result[-1]["content"] == "q"

    def test_encoding_cache_reuses_same_encoding(self, fake_tiktoken):
        from app.utils.tokens import _get_encoding, _encoding_cache
        enc1 = _get_encoding("gpt-4")
        enc2 = _get_encoding("gpt-4")
        assert enc1 is enc2
        assert "cl100k_base" in _encoding_cache

    def test_count_messages_tokens_multimodal_with_tiktoken(self, fake_tiktoken):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "hello world"},
                {"type": "image_url", "image_url": {"url": "http://x.png"}},
            ],
        }]
        # 7 overhead + 2 text + 255 image + 3 prefix = 267
        assert count_messages_tokens(messages, model="gpt-4") == 267

    def test_count_messages_tokens_skips_non_dict_items_with_tiktoken(self, fake_tiktoken):
        messages = [{
            "role": "user",
            "content": [
                "not a dict",
                {"type": "text", "text": "hello"},
            ],
        }]
        # 7 overhead + 1 text + 3 prefix = 11
        assert count_messages_tokens(messages, model="gpt-4") == 11

    def test_truncate_text_within_budget_with_tiktoken(self, fake_tiktoken):
        text = "a b"
        # 2 tokens <= 5 budget，应原样返回
        assert truncate_text_to_budget(text, 5) == text

    def test_truncate_messages_keeps_fitting_history_with_tiktoken(self, fake_tiktoken):
        messages = [
            {"role": "system", "content": "one"},
            {"role": "user", "content": "q"},
        ]
        result = truncate_messages_to_budget(messages, 20)
        # 两条都应保留
        assert len(result) == 2
        assert result[0]["content"] == "one"
