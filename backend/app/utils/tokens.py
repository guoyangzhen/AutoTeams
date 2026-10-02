"""Token 估算与上下文截断工具（基于 tiktoken）。

P2-3:
- 使用 tiktoken 精确估算中英文混合文本的 token 数
- 当 tiktoken 未安装时，优雅降级为字符近似
- 提供 messages 列表的总 token 估算与截断函数
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 模型名到 tiktoken encoding 的映射（保守使用通用编码）
_TIKTOKEN_ENCODING_MAP: dict[str, str] = {
    "gpt-4": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
}

# 单条 message 的格式开销（role + content 分隔符等），保守取 7
_TIKTOKEN_MESSAGE_OVERHEAD = 7

# 每个 assistant 回复前的前缀 token 数
_TIKTOKEN_REPLY_PREFIX_TOKENS = 3

_tiktoken_available: Optional[bool] = None
_encoding_cache: dict[str, Any] = {}


def _get_encoding(model: str) -> Any | None:
    """懒加载并缓存 tiktoken encoding。"""
    global _tiktoken_available
    if _tiktoken_available is False:
        return None
    if _tiktoken_available is None:
        try:
            import tiktoken  # noqa: F401
            _tiktoken_available = True
        except ImportError:
            _tiktoken_available = False
            logger.warning(
                "tiktoken 未安装，token 估算将回退到字符近似。"
                "建议生产环境安装: pip install tiktoken"
            )
            return None

    import tiktoken
    encoding_name = _TIKTOKEN_ENCODING_MAP.get(model, "cl100k_base")
    if encoding_name not in _encoding_cache:
        _encoding_cache[encoding_name] = tiktoken.get_encoding(encoding_name)
    return _encoding_cache[encoding_name]


def count_text_tokens(text: str, model: str = "gpt-4") -> int:
    """精确估算单段文本的 token 数。

    Args:
        text: 待估算文本
        model: 模型名，用于选择对应的 tiktoken encoding

    Returns:
        token 数量（>=0）
    """
    if not text:
        return 0
    encoding = _get_encoding(model)
    if encoding is None:
        # 降级：字符近似（中文约 1.5 字符/token，英文约 4 字符/token，取折中 2.5）
        return max(1, len(text) // 2)
    return len(encoding.encode(text))


def count_messages_tokens(messages: list[dict], model: str = "gpt-4") -> int:
    """估算 OpenAI chat messages 格式的总 token 数。

    包含每条消息的 role/content 及格式开销。
    """
    if not messages:
        return 0
    encoding = _get_encoding(model)
    if encoding is None:
        total = 0
        for msg in messages:
            total += 4  # 角色标记开销
            total += count_text_tokens(msg.get("content", ""), model=model)
        return total

    total = 0
    for msg in messages:
        # 每条消息的格式开销
        total += _TIKTOKEN_MESSAGE_OVERHEAD
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(encoding.encode(content))
        elif isinstance(content, list):
            # 多模态消息：只估算文本部分，图片按固定开销估算
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    total += len(encoding.encode(item.get("text", "")))
                elif item.get("type") == "image_url":
                    # 图片在 GPT-4V 中按 tile 计费，无 tiktoken 对应；保守估算
                    total += 255
    # 每个回复前的前缀开销
    total += _TIKTOKEN_REPLY_PREFIX_TOKENS
    return total


def truncate_text_to_budget(text: str, max_tokens: int, model: str = "gpt-4") -> str:
    """将文本截断到指定 token 预算内（从开头保留）。"""
    if max_tokens <= 0:
        return ""
    encoding = _get_encoding(model)
    if encoding is None:
        chars = max_tokens * 2
        return text[:chars]
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return encoding.decode(tokens[:max_tokens])


def truncate_messages_to_budget(
    messages: list[dict],
    max_tokens: int,
    model: str = "gpt-4",
) -> list[dict]:
    """按 token 预算截断消息列表，优先保留最近消息。

    策略：
    1. 若总 token 未超预算，直接返回
    2. 保留最后一条消息（通常是当前 user query）
    3. 从后往前依次加入历史消息，直到预算耗尽
    4. 若只有一条仍超预算，则截断其内容
    """
    if not messages:
        return []

    total = count_messages_tokens(messages, model=model)
    if total <= max_tokens:
        return messages

    if len(messages) == 1:
        return [{
            "role": messages[0]["role"],
            "content": truncate_text_to_budget(messages[0]["content"], max_tokens, model=model),
        }]

    # 保留最后一条（当前查询）
    kept = [messages[-1]]
    budget = max_tokens - count_messages_tokens(kept, model=model)

    for msg in reversed(messages[:-1]):
        cost = count_messages_tokens([msg], model=model)
        if cost <= budget:
            kept.insert(0, msg)
            budget -= cost
        else:
            break

    return kept
