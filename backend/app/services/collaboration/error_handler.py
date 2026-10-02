"""WT4 错误处理与降级（PRD §5.10 + 重构方案 §7.6 阶段2）。

MVP 2 类错误处理：
1. **工具调用失败**（handle_tool_failure）：CRM API 超时/权限不足等
   → 三级恢复：重试 3 次（指数退避）→ 降级缓存 → 通知人工
2. **流程异常**（handle_process_exception）：审批超时/参与者离线等
   → 暂停流程 → 超时升级（48h 转上级）→ 转人工

三级恢复机制（PRD §5.10）：
- 轻微（minor）→ 自动重试 → 成功继续 / 失败降级
- 中等（moderate）→ 降级执行 → 成功继续 / 失败暂停
- 严重（severe）→ 暂停流程 → 通知人类 → 人类处理 → 恢复

P1 错误（knowledge_gap / capability_insufficiency / data_conflict）预留接口。

工程约束（spec §2.2）：service 层写操作显式 await db.commit()
"""
import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collaboration import ApprovalGate
from app.schemas.collaboration import ErrorRecoveryResult
from app.services.collaboration.event_bus import (
    EventBus,
    event_bus,
    EVENT_APPROVAL,
)
from app.utils.audit import log_audit

logger = logging.getLogger(__name__)


# 错误类型
ERR_TOOL_FAILURE = "tool_failure"
ERR_PROCESS_EXCEPTION = "process_exception"
# P1 错误（预留）
ERR_KNOWLEDGE_GAP = "knowledge_gap"
ERR_CAPABILITY_INSUFFICIENCY = "capability_insufficiency"
ERR_DATA_CONFLICT = "data_conflict"

# 恢复动作
ACTION_RETRY = "retry"
ACTION_FALLBACK = "fallback"
ACTION_PAUSE_AND_ESCALATE = "pause_and_escalate"
ACTION_ROLLBACK = "rollback"

# 严重程度
SEVERITY_MINOR = "minor"
SEVERITY_MODERATE = "moderate"
SEVERITY_SEVERE = "severe"

# 默认重试配置（指数退避：单位秒，测试场景下可覆盖为极小值）
DEFAULT_BACKOFF_DELAYS = (0.1, 0.2, 0.4)
DEFAULT_MAX_RETRIES = 3
# 流程超时升级阈值（小时，PRD §5.10：48h 转上级）
DEFAULT_ESCALATION_HOURS = 48

# 可重试异常类型（瞬时网络错误）
# 注意：不包含 OSError，因为 PermissionError/FileNotFoundError 等 OSError 子类
# 属于不可重试错误，使用 OSError 会误将它们纳入重试。
_RETRYABLE_ERRORS = (TimeoutError, ConnectionError)


class ErrorHandler:
    """错误处理与降级服务。

    Usage::

        handler = ErrorHandler()
        result = await handler.handle_tool_failure(
            db, enterprise_id, tool_id="crm_api", error=TimeoutError("超时"),
            retry_func=lambda: call_crm(),
        )
    """

    def __init__(
        self,
        event_bus_svc: Optional[EventBus] = None,
        backoff_delays: tuple[float, ...] = DEFAULT_BACKOFF_DELAYS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        escalation_hours: int = DEFAULT_ESCALATION_HOURS,
    ) -> None:
        self._event_bus = event_bus_svc or event_bus
        self._backoff_delays = backoff_delays
        self._max_retries = max_retries
        self._escalation_hours = escalation_hours

    # ========================================================
    # MVP 错误 1：工具调用失败
    # ========================================================

    async def handle_tool_failure(
        self,
        db: AsyncSession,
        enterprise_id: str,
        tool_id: str,
        error: BaseException,
        agent_id: Optional[str] = None,
        retry_func: Optional[Callable[[], Awaitable[Any]]] = None,
        fallback_func: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> ErrorRecoveryResult:
        """处理工具调用失败：三级恢复（重试 → 降级 → 暂停+人工）。

        Args:
            retry_func: 可重试的工具调用（无参 async callable）
            fallback_func: 降级方案（如使用缓存数据）

        Returns:
            ErrorRecoveryResult：含 severity / recovery_action / resolved / attempts
        """
        attempts = 0

        # ---- 一级：轻微 → 自动重试（指数退避）----
        if retry_func is not None:
            retry_result = await self._retry_with_backoff(retry_func)
            attempts = retry_result["attempts"]
            if retry_result["success"]:
                await self._log_error(
                    db, enterprise_id, agent_id, ERR_TOOL_FAILURE, tool_id,
                    error, SEVERITY_MINOR, ACTION_RETRY, resolved=True,
                    attempts=attempts,
                )
                return ErrorRecoveryResult(
                    error_type=ERR_TOOL_FAILURE,
                    severity=SEVERITY_MINOR,
                    recovery_action=ACTION_RETRY,
                    resolved=True,
                    attempts=attempts,
                    message=f"工具 {tool_id} 重试 {attempts} 次后成功",
                    details={"tool_id": tool_id},
                )

        # ---- 二级：中等 → 降级执行 ----
        if fallback_func is not None:
            try:
                fallback_result = await fallback_func()
                await self._log_error(
                    db, enterprise_id, agent_id, ERR_TOOL_FAILURE, tool_id,
                    error, SEVERITY_MODERATE, ACTION_FALLBACK, resolved=True,
                    attempts=attempts,
                )
                return ErrorRecoveryResult(
                    error_type=ERR_TOOL_FAILURE,
                    severity=SEVERITY_MODERATE,
                    recovery_action=ACTION_FALLBACK,
                    resolved=True,
                    attempts=attempts,
                    message=f"工具 {tool_id} 重试失败，已降级为缓存数据",
                    details={"tool_id": tool_id, "fallback": True,
                             "fallback_result": str(fallback_result)[:200]},
                )
            except Exception as fallback_err:
                logger.warning(
                    "降级方案执行失败: tool=%s error=%s", tool_id, fallback_err,
                    exc_info=True,
                )

        # ---- 三级：严重 → 暂停 + 通知人工 ----
        escalation = await self._escalate_to_human(
            db, enterprise_id, agent_id, tool_id, error,
            process_id=f"tool-{tool_id}",
        )
        await self._log_error(
            db, enterprise_id, agent_id, ERR_TOOL_FAILURE, tool_id,
            error, SEVERITY_SEVERE, ACTION_PAUSE_AND_ESCALATE, resolved=False,
            attempts=attempts,
        )
        return ErrorRecoveryResult(
            error_type=ERR_TOOL_FAILURE,
            severity=SEVERITY_SEVERE,
            recovery_action=ACTION_PAUSE_AND_ESCALATE,
            resolved=False,
            attempts=attempts,
            message=f"工具 {tool_id} 重试与降级均失败，已暂停并通知人工（升级单 {escalation}）",
            details={"tool_id": tool_id, "escalation_gate_id": escalation},
        )

    # ========================================================
    # MVP 错误 2：流程异常
    # ========================================================

    async def handle_process_exception(
        self,
        db: AsyncSession,
        enterprise_id: str,
        process_id: str,
        exception: BaseException,
        agent_id: Optional[str] = None,
        elapsed_hours: float = 0.0,
    ) -> ErrorRecoveryResult:
        """处理流程异常：暂停流程 → 超时升级（48h 转上级）→ 转人工。

        Args:
            elapsed_hours: 流程已等待时长（小时），≥ escalation_hours 触发升级

        Returns:
            ErrorRecoveryResult
        """
        # 严重度判定：已超时 → severe（直接升级）；未超时 → moderate（暂停等待）
        if elapsed_hours >= self._escalation_hours:
            # 严重：超时升级 → 转人工
            escalation = await self._escalate_to_human(
                db, enterprise_id, agent_id, process_id, exception,
                process_id=process_id,
            )
            await self._log_error(
                db, enterprise_id, agent_id, ERR_PROCESS_EXCEPTION, process_id,
                exception, SEVERITY_SEVERE, ACTION_PAUSE_AND_ESCALATE,
                resolved=False, attempts=0,
            )
            return ErrorRecoveryResult(
                error_type=ERR_PROCESS_EXCEPTION,
                severity=SEVERITY_SEVERE,
                recovery_action=ACTION_PAUSE_AND_ESCALATE,
                resolved=False,
                attempts=0,
                message=f"流程 {process_id} 已超时 {elapsed_hours}h（阈值 {self._escalation_hours}h），"
                        f"已升级至上级并转人工（升级单 {escalation}）",
                details={"process_id": process_id, "elapsed_hours": elapsed_hours,
                         "escalation_gate_id": escalation},
            )

        # 中等：暂停流程等待（未超时，给予恢复窗口）
        await self._log_error(
            db, enterprise_id, agent_id, ERR_PROCESS_EXCEPTION, process_id,
            exception, SEVERITY_MODERATE, ACTION_PAUSE_AND_ESCALATE,
            resolved=False, attempts=0,
        )
        return ErrorRecoveryResult(
            error_type=ERR_PROCESS_EXCEPTION,
            severity=SEVERITY_MODERATE,
            recovery_action=ACTION_PAUSE_AND_ESCALATE,
            resolved=False,
            attempts=0,
            message=f"流程 {process_id} 已暂停（等待 {elapsed_hours}h / 阈值 {self._escalation_hours}h），"
                    f"超时后将升级至上级",
            details={"process_id": process_id, "elapsed_hours": elapsed_hours},
        )

    # ========================================================
    # P1 错误（预留接口，MVP 不实现完整逻辑）
    # ========================================================

    async def handle_knowledge_gap(
        self, db: AsyncSession, enterprise_id: str, query: str
    ) -> ErrorRecoveryResult:
        """知识缺失（P1）：转交有知识的 Agent → 仍无则标注"待补充"并转人工。"""
        return ErrorRecoveryResult(
            error_type=ERR_KNOWLEDGE_GAP,
            severity=SEVERITY_MODERATE,
            recovery_action=ACTION_FALLBACK,
            resolved=False,
            attempts=0,
            message="知识缺失处理为 P1 功能，MVP 阶段仅记录",
            details={"query": query[:200]},
        )

    async def handle_capability_insufficiency(
        self, db: AsyncSession, enterprise_id: str, task: str, agent_id: Optional[str] = None
    ) -> ErrorRecoveryResult:
        """能力不足（P1）：转交其他 Agent → 仍无法处理则人类接管。"""
        return ErrorRecoveryResult(
            error_type=ERR_CAPABILITY_INSUFFICIENCY,
            severity=SEVERITY_MODERATE,
            recovery_action=ACTION_FALLBACK,
            resolved=False,
            attempts=0,
            message="能力不足处理为 P1 功能，MVP 阶段仅记录",
            details={"task": task[:200], "agent_id": agent_id},
        )

    async def handle_data_conflict(
        self, db: AsyncSession, enterprise_id: str, entity_id: str
    ) -> ErrorRecoveryResult:
        """数据冲突（P1）：标记冲突 → 暂停操作 → 等待人工裁定。"""
        return ErrorRecoveryResult(
            error_type=ERR_DATA_CONFLICT,
            severity=SEVERITY_SEVERE,
            recovery_action=ACTION_PAUSE_AND_ESCALATE,
            resolved=False,
            attempts=0,
            message="数据冲突处理为 P1 功能，MVP 阶段仅记录",
            details={"entity_id": entity_id},
        )

    # ========================================================
    # 内部：三级恢复实现
    # ========================================================

    async def _retry_with_backoff(
        self, func: Callable[[], Awaitable[Any]]
    ) -> dict[str, Any]:
        """指数退避重试。

        Returns:
            {"success": bool, "attempts": int, "last_error": str | None}
        """
        last_error: Optional[BaseException] = None
        attempts = 0
        delays = list(self._backoff_delays) + [0.0] * max(0, self._max_retries - len(self._backoff_delays))
        for i in range(self._max_retries):
            attempts = i + 1
            try:
                await func()
                return {"success": True, "attempts": attempts, "last_error": None}
            except _RETRYABLE_ERRORS as e:
                last_error = e
                logger.warning(
                    "工具调用重试 %d/%d 失败（可重试）: %s", attempts, self._max_retries, e,
                )
                if i < self._max_retries - 1:
                    await asyncio.sleep(delays[i])
            except Exception as e:
                # 非瞬时错误（如权限不足），不重试
                last_error = e
                logger.warning(
                    "工具调用失败（不可重试）: %s", e, exc_info=True,
                )
                break
        return {"success": False, "attempts": attempts, "last_error": str(last_error) if last_error else None}

    async def _escalate_to_human(
        self,
        db: AsyncSession,
        enterprise_id: str,
        agent_id: Optional[str],
        resource: str,
        error: BaseException,
        process_id: str,
    ) -> str:
        """升级至人工：创建审批门（转人工处理）+ 发布审批事件。

        Returns: escalation_gate_id
        """
        gate = ApprovalGate(
            id=str(uuid.uuid4()),
            enterprise_id=enterprise_id,
            process_id=process_id,
            node_id=f"escalation-{resource}",
            agent_id=agent_id,
            status="pending",
        )
        db.add(gate)
        await db.flush()

        # 发布审批事件（best-effort，失败不阻断升级记录）
        try:
            await self._event_bus.publish(
                db, enterprise_id, EVENT_APPROVAL,
                payload={"resource": resource, "error": str(error)[:500],
                         "escalation": True, "gate_id": gate.id},
                source_agent_id=agent_id,
            )
        except Exception as e:
            logger.warning("升级事件发布失败（不阻断）: %s", e, exc_info=True)

        await db.commit()
        return gate.id

    async def _log_error(
        self,
        db: AsyncSession,
        enterprise_id: str,
        agent_id: Optional[str],
        error_type: str,
        resource_id: str,
        error: BaseException,
        severity: str,
        recovery_action: str,
        resolved: bool,
        attempts: int,
    ) -> None:
        """错误处理审计记录（spec §2.6：审计签名链）。"""
        await log_audit(
            db, None, "error_handle", error_type, resource_id,
            details={
                "enterprise_id": enterprise_id,
                "agent_id": agent_id,
                "severity": severity,
                "recovery_action": recovery_action,
                "resolved": resolved,
                "attempts": attempts,
                "error_type": type(error).__name__,
                "error_message": str(error)[:500],
            },
        )


# 模块级单例（无状态，可安全共享）
error_handler = ErrorHandler()
