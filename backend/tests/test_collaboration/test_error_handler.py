"""错误处理与降级测试（PRD §5.10，MVP 2 类 + 三级恢复）。

测试覆盖：
- 工具调用失败（handle_tool_failure）：三级恢复
  - 一级：重试成功（minor / retry）
  - 二级：降级执行（moderate / fallback）
  - 三级：暂停+升级人工（severe / pause_and_escalate）
- 流程异常（handle_process_exception）：
  - 未超时（moderate，暂停等待）
  - 已超时（severe，升级人工）
- 不可重试错误（如权限不足）不重试
"""
import pytest
from unittest.mock import AsyncMock

from app.services.collaboration.error_handler import (
    ErrorHandler,
    error_handler,
    ERR_TOOL_FAILURE,
    ERR_PROCESS_EXCEPTION,
    ACTION_RETRY,
    ACTION_FALLBACK,
    ACTION_PAUSE_AND_ESCALATE,
    SEVERITY_MINOR,
    SEVERITY_MODERATE,
    SEVERITY_SEVERE,
)
from app.models.collaboration import ApprovalGate

# 共享 fixtures（conftest_extensions 未被 pytest 自动发现，需显式导入）


# ============================================================
# 测试辅助
# ============================================================


async def _seed_enterprise(db):
    from sqlalchemy import text
    import uuid
    from datetime import datetime, timezone

    eid = f"ent-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    await db.execute(
        text(
            "INSERT INTO enterprises (id, name, is_active, invite_max_uses, invite_used_count, created_at, updated_at) "
            "VALUES (:id, :name, 1, 10, 0, :now, :now)"
        ),
        {"id": eid, "name": "测试企业", "now": now},
    )
    await db.commit()
    return eid


# ============================================================
# 工具调用失败：三级恢复测试
# ============================================================


class TestHandleToolFailureLevel1Retry:
    """一级恢复：自动重试（指数退避）。"""

    async def test_retry_succeeds_on_first_attempt(self, db_session, v3_tables):
        """第一次重试成功 → minor / retry / resolved=True。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(backoff_delays=(0.01, 0.02, 0.04), max_retries=3)

        # mock 第一次就成功
        retry_func = AsyncMock(return_value="ok")

        result = await handler.handle_tool_failure(
            db_session, eid, tool_id="crm_api",
            error=TimeoutError("超时"),
            retry_func=retry_func,
        )

        assert result.error_type == ERR_TOOL_FAILURE
        assert result.severity == SEVERITY_MINOR
        assert result.recovery_action == ACTION_RETRY
        assert result.resolved is True
        assert result.attempts == 1
        assert retry_func.call_count == 1

    async def test_retry_succeeds_after_multiple_attempts(
        self, db_session, v3_tables
    ):
        """多次重试后成功 → attempts 累计。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(backoff_delays=(0.01, 0.02, 0.04), max_retries=3)

        # 前两次失败，第三次成功
        retry_func = AsyncMock(
            side_effect=[TimeoutError("失败1"), TimeoutError("失败2"), "ok"]
        )

        result = await handler.handle_tool_failure(
            db_session, eid, tool_id="crm_api",
            error=TimeoutError("超时"),
            retry_func=retry_func,
        )

        assert result.severity == SEVERITY_MINOR
        assert result.resolved is True
        assert result.attempts == 3
        assert retry_func.call_count == 3


class TestHandleToolFailureLevel2Fallback:
    """二级恢复：降级执行。"""

    async def test_fallback_succeeds_after_retry_exhausted(
        self, db_session, v3_tables
    ):
        """重试失败 + 降级成功 → moderate / fallback / resolved=True。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(backoff_delays=(0.01,), max_retries=1)

        retry_func = AsyncMock(side_effect=TimeoutError("持续失败"))
        fallback_func = AsyncMock(return_value="降级结果")

        result = await handler.handle_tool_failure(
            db_session, eid, tool_id="crm_api",
            error=TimeoutError("超时"),
            retry_func=retry_func,
            fallback_func=fallback_func,
        )

        assert result.severity == SEVERITY_MODERATE
        assert result.recovery_action == ACTION_FALLBACK
        assert result.resolved is True
        assert fallback_func.call_count == 1


class TestHandleToolFailureLevel3Escalate:
    """三级恢复：暂停 + 通知人工。"""

    async def test_escalates_when_retry_and_fallback_both_fail(
        self, db_session, v3_tables
    ):
        """重试 + 降级均失败 → severe / pause_and_escalate / resolved=False。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(backoff_delays=(0.01,), max_retries=1)

        retry_func = AsyncMock(side_effect=TimeoutError("持续失败"))
        fallback_func = AsyncMock(side_effect=RuntimeError("降级也失败"))

        result = await handler.handle_tool_failure(
            db_session, eid, tool_id="crm_api",
            error=TimeoutError("超时"),
            retry_func=retry_func,
            fallback_func=fallback_func,
        )

        assert result.severity == SEVERITY_SEVERE
        assert result.recovery_action == ACTION_PAUSE_AND_ESCALATE
        assert result.resolved is False
        # 升级单（ApprovalGate）已创建
        assert "escalation_gate_id" in result.details

    async def test_escalates_when_no_retry_and_no_fallback(
        self, db_session, v3_tables
    ):
        """无重试/降级方案 → 直接升级人工。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler()

        result = await handler.handle_tool_failure(
            db_session, eid, tool_id="crm_api",
            error=PermissionError("无权限"),
        )

        assert result.severity == SEVERITY_SEVERE
        assert result.resolved is False

    async def test_escalation_creates_approval_gate(self, db_session, v3_tables):
        """升级至人工：创建 ApprovalGate。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(backoff_delays=(0.01,), max_retries=1)

        result = await handler.handle_tool_failure(
            db_session, eid, tool_id="crm_api",
            error=TimeoutError("超时"),
            retry_func=AsyncMock(side_effect=TimeoutError("失败")),
        )

        # 验证 ApprovalGate 已创建
        from sqlalchemy import select, func
        count_result = await db_session.execute(
            select(func.count(ApprovalGate.id)).where(
                ApprovalGate.enterprise_id == eid
            )
        )
        gate_count = int(count_result.scalar() or 0)
        assert gate_count >= 1


class TestHandleToolFailureNonRetryable:
    """不可重试错误测试。"""

    async def test_non_retryable_error_skips_retry(self, db_session, v3_tables):
        """不可重试错误（如权限不足）不重试，直接进入降级/升级。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(backoff_delays=(0.01,), max_retries=3)

        # PermissionError 不是 _RETRYABLE_ERRORS，应只调用一次
        retry_func = AsyncMock(side_effect=PermissionError("禁止访问"))
        fallback_func = AsyncMock(return_value="降级")

        result = await handler.handle_tool_failure(
            db_session, eid, tool_id="crm_api",
            error=PermissionError("禁止访问"),
            retry_func=retry_func,
            fallback_func=fallback_func,
        )

        # 应直接降级成功
        assert result.recovery_action == ACTION_FALLBACK
        assert retry_func.call_count == 1  # 只调用一次（不重试）


# ============================================================
# 流程异常测试
# ============================================================


class TestHandleProcessException:
    async def test_moderate_when_not_yet_timed_out(self, db_session, v3_tables):
        """未超时 → moderate（暂停等待）。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(escalation_hours=48)

        result = await handler.handle_process_exception(
            db_session, eid,
            process_id="quotation-OPP-001",
            exception=RuntimeError("审批人离线"),
            elapsed_hours=2.0,
        )

        assert result.severity == SEVERITY_MODERATE
        assert result.resolved is False
        assert "暂停" in result.message

    async def test_severe_when_timed_out(self, db_session, v3_tables):
        """已超时 → severe（升级人工）。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(escalation_hours=48)

        result = await handler.handle_process_exception(
            db_session, eid,
            process_id="quotation-OPP-001",
            exception=RuntimeError("审批超时 50h"),
            elapsed_hours=50.0,
        )

        assert result.severity == SEVERITY_SEVERE
        assert result.resolved is False
        assert "超时" in result.message
        assert "escalation_gate_id" in result.details

    async def test_escalation_threshold_boundary(self, db_session, v3_tables):
        """超时阈值边界：elapsed_hours == escalation_hours 触发 severe。"""
        eid = await _seed_enterprise(db_session)
        handler = ErrorHandler(escalation_hours=48)

        result = await handler.handle_process_exception(
            db_session, eid, "process-1", RuntimeError("超时"),
            elapsed_hours=48.0,  # 恰好等于阈值
        )
        assert result.severity == SEVERITY_SEVERE

        result_below = await handler.handle_process_exception(
            db_session, eid, "process-2", RuntimeError("未超时"),
            elapsed_hours=47.9,
        )
        assert result_below.severity == SEVERITY_MODERATE


# ============================================================
# P1 错误处理（预留接口）
# ============================================================


class TestP1ErrorHandlers:
    """P1 错误处理接口（MVP 仅记录，不实现完整逻辑）。"""

    async def test_handle_knowledge_gap_returns_mvp_placeholder(
        self, db_session, v3_tables
    ):
        """知识缺失：MVP 阶段仅记录。"""
        eid = await _seed_enterprise(db_session)
        result = await error_handler.handle_knowledge_gap(
            db_session, eid, query="什么是 SL-T100？"
        )
        assert result.resolved is False
        assert result.error_type == "knowledge_gap"

    async def test_handle_capability_insufficiency_returns_mvp_placeholder(
        self, db_session, v3_tables
    ):
        """能力不足：MVP 阶段仅记录。"""
        eid = await _seed_enterprise(db_session)
        result = await error_handler.handle_capability_insufficiency(
            db_session, eid, task="复杂法律咨询", agent_id="agent-1"
        )
        assert result.resolved is False
        assert result.error_type == "capability_insufficiency"

    async def test_handle_data_conflict_returns_mvp_placeholder(
        self, db_session, v3_tables
    ):
        """数据冲突：MVP 阶段仅记录。"""
        eid = await _seed_enterprise(db_session)
        result = await error_handler.handle_data_conflict(
            db_session, eid, entity_id="customer-001"
        )
        assert result.resolved is False
        assert result.error_type == "data_conflict"
