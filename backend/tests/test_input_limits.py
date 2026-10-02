"""P0-DoS: 用户输入长度上限验证测试。

验证各请求 schema 对用户输入文本字段的 max_length 约束生效，
防止超大 payload 耗尽内存与 LLM token 预算（架构审计 Phase 9 新发现）。
"""
import pytest
from pydantic import ValidationError

from app.schemas.agent import ChatMessage, MAX_USER_MESSAGE_LENGTH
from app.schemas.conversation import (
    MessageCreate,
    ConversationUpdate,
    SatisfactionUpdate,
    ImplicitFeedbackRequest,
)
from app.schemas.setup import SetupMessage
from app.api.loop import OptimizeRequest
from app.api.files import MAX_BATCH_UPLOAD_FILES
from app.utils.error_codes import ErrorCode


class TestInputLengthLimits:
    """验证用户输入字段有 max_length 兜底。"""

    def test_chat_message_accepts_valid_length(self):
        """单条 chat 消息在上限内应通过。"""
        ChatMessage(content="x")
        ChatMessage(content="x" * MAX_USER_MESSAGE_LENGTH)

    def test_chat_message_rejects_over_limit(self):
        """超过 MAX_USER_MESSAGE_LENGTH 应被拒绝。"""
        with pytest.raises(ValidationError):
            ChatMessage(content="x" * (MAX_USER_MESSAGE_LENGTH + 1))

    def test_message_create_rejects_over_limit(self):
        """MessageCreate.content 超限应被拒绝。"""
        with pytest.raises(ValidationError):
            MessageCreate(content="x" * (MAX_USER_MESSAGE_LENGTH + 1))

    def test_conversation_update_title_rejects_over_limit(self):
        """对话标题超 200 字符应被拒绝。"""
        with pytest.raises(ValidationError):
            ConversationUpdate(title="x" * 201)

    def test_conversation_update_title_accepts_200(self):
        """对话标题恰好 200 字符应通过。"""
        ConversationUpdate(title="x" * 200)

    def test_setup_message_rejects_over_limit(self):
        """Setup 向导消息超限应被拒绝。"""
        with pytest.raises(ValidationError):
            SetupMessage(content="x" * (MAX_USER_MESSAGE_LENGTH + 1))

    def test_optimize_request_rejects_over_limit(self):
        """Loop 优化请求 query/feedback 超 8000 字符应被拒绝。"""
        with pytest.raises(ValidationError):
            OptimizeRequest(query="x" * 8001, feedback="f")
        with pytest.raises(ValidationError):
            OptimizeRequest(query="q", feedback="x" * 8001)

    def test_optimize_request_accepts_8000(self):
        """Loop 优化请求恰好 8000 字符应通过。"""
        OptimizeRequest(query="x" * 8000, feedback="x" * 8000)

    def test_batch_upload_limit_constant(self):
        """MAX_BATCH_UPLOAD_FILES 应为合理的正值（覆盖文件夹批量入库）。"""
        assert isinstance(MAX_BATCH_UPLOAD_FILES, int)
        assert 10 <= MAX_BATCH_UPLOAD_FILES <= 200

    def test_batch_upload_error_code_defined(self):
        """BATCH_UPLOAD_TOO_MANY_FILES 错误码应已定义。"""
        assert ErrorCode.BATCH_UPLOAD_TOO_MANY_FILES == "BATCH_UPLOAD_TOO_MANY_FILES"

    def test_satisfaction_update_rejects_over_limit(self):
        """satisfaction 超 50 字符应被拒绝。"""
        with pytest.raises(ValidationError):
            SatisfactionUpdate(satisfaction="x" * 51)

    def test_satisfaction_update_accepts_valid(self):
        """satisfaction 合法值应通过。"""
        SatisfactionUpdate(satisfaction="satisfied")
        SatisfactionUpdate(satisfaction="implicit:unsatisfied:regenerate")

    def test_dwell_seconds_rejects_negative(self):
        """dwell_seconds 负值应被拒绝。"""
        with pytest.raises(ValidationError):
            ImplicitFeedbackRequest(signal="dwell", dwell_seconds=-1)

    def test_dwell_seconds_rejects_over_one_day(self):
        """dwell_seconds 超过 86400（1 天）应被拒绝。"""
        with pytest.raises(ValidationError):
            ImplicitFeedbackRequest(signal="dwell", dwell_seconds=86401)

    def test_dwell_seconds_accepts_valid_range(self):
        """dwell_seconds 合法范围应通过。"""
        ImplicitFeedbackRequest(signal="dwell", dwell_seconds=0)
        ImplicitFeedbackRequest(signal="dwell", dwell_seconds=86400)
        ImplicitFeedbackRequest(signal="copy")  # None 默认值
