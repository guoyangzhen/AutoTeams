"""WT4 回滚机制（PRD §5.10 + 重构方案 §7.6 阶段2）。

机制：
- 操作前快照：每个 Agent 关键操作前创建状态快照（config/memory_config/kpi_ids/...）
- 操作回滚：操作失败可回滚到上一个稳定状态
- 流程级回滚：整条业务链路可回滚到任意节点（按 process_id 聚合快照）
- 回滚操作记录在审计日志中（spec §2.6 审计签名链）

快照约定：
- operation 字段在 process_id 提供时存储为 ``"{process_id}:{operation}"``，
  便于流程级回滚按前缀检索。
- snapshot JSON 含 ``{"state": {...}, "_process_id": process_id | None}``，
  state 为操作前的 Agent 状态镜像。

工程约束（spec §2.2）：service 层写操作显式 await db.commit()
"""
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.collaboration import OperationSnapshot
from app.utils.audit import log_audit

logger = logging.getLogger(__name__)


# Agent 操作前需要快照的字段（可回滚字段）
_SNAPSHOT_FIELDS = (
    "config",
    "memory_config",
    "kpi_ids",
    "lifecycle_stage",
    "position_id",
    "system_prompt",
)


class RollbackManager:
    """回滚管理器。

    Usage::

        mgr = RollbackManager()
        # 操作前快照
        snap_id = await mgr.create_snapshot(db, agent_id, "update_config",
                                            process_id="quotation-OPP-001")
        # 执行操作（可能失败）...
        # 操作回滚
        result = await mgr.rollback_operation(db, snap_id)
        # 流程级回滚
        result = await mgr.rollback_process(db, enterprise_id, "quotation-OPP-001")
    """

    # ========================================================
    # 创建快照
    # ========================================================

    async def create_snapshot(
        self,
        db: AsyncSession,
        agent_id: str,
        operation: str,
        state: Optional[dict[str, Any]] = None,
        process_id: Optional[str] = None,
    ) -> str:
        """操作前创建状态快照。

        Args:
            agent_id: 操作的 Agent ID
            operation: 操作名（如 update_config / send_quotation）
            state: 显式提供的状态镜像；为 None 时自动捕获当前 Agent 状态
            process_id: 所属流程 ID（提供时用于流程级回滚）

        Returns:
            snapshot_id
        """
        if state is None:
            state = await self._capture_agent_state(db, agent_id)

        # process_id 提供时，operation 存储为 "{process_id}:{operation}" 便于前缀检索
        stored_operation = f"{process_id}:{operation}" if process_id else operation

        snapshot = OperationSnapshot(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            operation=stored_operation,
            snapshot={"state": state, "_process_id": process_id},
        )
        db.add(snapshot)
        await db.commit()
        await db.refresh(snapshot)

        logger.info(
            "操作快照已创建: agent=%s operation=%s snapshot=%s",
            agent_id, stored_operation, snapshot.id,
        )
        return snapshot.id

    # ========================================================
    # 操作回滚
    # ========================================================

    async def rollback_operation(
        self,
        db: AsyncSession,
        snapshot_id: str,
    ) -> dict[str, Any]:
        """回滚到指定快照：恢复 Agent 状态。

        Returns:
            {"rolled_back": True, "agent_id": str, "operation": str, "snapshot_id": str}
        """
        snapshot = await self._get_snapshot(db, snapshot_id)
        if snapshot is None:
            raise ValueError(f"快照不存在: {snapshot_id}")

        state = (snapshot.snapshot or {}).get("state", {})
        await self._restore_agent_state(db, snapshot.agent_id, state)

        await log_audit(
            db, None, "rollback", "operation_snapshot", snapshot_id,
            details={"agent_id": snapshot.agent_id, "operation": snapshot.operation},
        )
        await db.commit()

        logger.info(
            "操作已回滚: agent=%s operation=%s snapshot=%s",
            snapshot.agent_id, snapshot.operation, snapshot_id,
        )
        return {
            "rolled_back": True,
            "agent_id": snapshot.agent_id,
            "operation": snapshot.operation,
            "snapshot_id": snapshot_id,
        }

    # ========================================================
    # 流程级回滚
    # ========================================================

    async def rollback_process(
        self,
        db: AsyncSession,
        enterprise_id: str,
        process_id: str,
    ) -> dict[str, Any]:
        """流程级回滚：回滚该流程下所有 Agent 的操作快照（按时间倒序）。

        检索 operation_snapshots 中 operation 以 ``"{process_id}:"`` 开头的快照，
        按创建时间倒序逐个回滚（最近的操作先回滚）。

        Returns:
            {"rolled_back": bool, "process_id": str, "rolled_back_count": int,
             "agent_ids": [str], "snapshot_ids": [str]}
        """
        prefix = f"{process_id}:"
        # 联表 agents 以限定企业范围（隔离）
        result = await db.execute(
            select(OperationSnapshot)
            .join(Agent, OperationSnapshot.agent_id == Agent.id)
            .where(
                Agent.enterprise_id == enterprise_id,
                OperationSnapshot.operation.like(f"{prefix}%"),
            )
            .order_by(OperationSnapshot.created_at.desc())
        )
        snapshots = list(result.scalars().all())

        if not snapshots:
            logger.info("流程 %s 无可回滚快照", process_id)
            return {
                "rolled_back": False,
                "process_id": process_id,
                "rolled_back_count": 0,
                "agent_ids": [],
                "snapshot_ids": [],
            }

        rolled_back_ids: list[str] = []
        agent_ids: list[str] = []
        for snap in snapshots:
            state = (snap.snapshot or {}).get("state", {})
            await self._restore_agent_state(db, snap.agent_id, state)
            rolled_back_ids.append(snap.id)
            if snap.agent_id not in agent_ids:
                agent_ids.append(snap.agent_id)

        await log_audit(
            db, None, "rollback_process", "process", process_id,
            details={"enterprise_id": enterprise_id,
                     "rolled_back_count": len(rolled_back_ids),
                     "agent_ids": agent_ids, "snapshot_ids": rolled_back_ids},
        )
        await db.commit()

        logger.info(
            "流程级回滚完成: process=%s count=%d agents=%s",
            process_id, len(rolled_back_ids), agent_ids,
        )
        return {
            "rolled_back": True,
            "process_id": process_id,
            "rolled_back_count": len(rolled_back_ids),
            "agent_ids": agent_ids,
            "snapshot_ids": rolled_back_ids,
        }

    # ========================================================
    # 查询
    # ========================================================

    async def get_snapshot(
        self, db: AsyncSession, snapshot_id: str
    ) -> Optional[OperationSnapshot]:
        return await self._get_snapshot(db, snapshot_id)

    async def list_snapshots(
        self,
        db: AsyncSession,
        agent_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[OperationSnapshot], int]:
        """分页查询 Agent 的操作快照。"""
        from sqlalchemy import func

        count_result = await db.execute(
            select(func.count(OperationSnapshot.id)).where(
                OperationSnapshot.agent_id == agent_id
            )
        )
        total = int(count_result.scalar() or 0)

        result = await db.execute(
            select(OperationSnapshot)
            .where(OperationSnapshot.agent_id == agent_id)
            .order_by(OperationSnapshot.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(result.scalars().all())
        return items, total

    # ========================================================
    # 内部：Agent 状态捕获 / 恢复
    # ========================================================

    async def _capture_agent_state(
        self, db: AsyncSession, agent_id: str
    ) -> dict[str, Any]:
        """捕获 Agent 当前可回滚字段的状态镜像。"""
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise ValueError(f"Agent 不存在: {agent_id}")
        return {
            field: getattr(agent, field) for field in _SNAPSHOT_FIELDS
        }

    async def _restore_agent_state(
        self, db: AsyncSession, agent_id: str, state: dict[str, Any]
    ) -> None:
        """从状态镜像恢复 Agent 可回滚字段。"""
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if agent is None:
            logger.warning("回滚目标 Agent 不存在，跳过: %s", agent_id)
            return
        for field in _SNAPSHOT_FIELDS:
            if field in state:
                setattr(agent, field, state[field])

    async def _get_snapshot(
        self, db: AsyncSession, snapshot_id: str
    ) -> Optional[OperationSnapshot]:
        result = await db.execute(
            select(OperationSnapshot).where(OperationSnapshot.id == snapshot_id)
        )
        return result.scalar_one_or_none()


# 模块级单例（无状态，可安全共享）
rollback_manager = RollbackManager()
