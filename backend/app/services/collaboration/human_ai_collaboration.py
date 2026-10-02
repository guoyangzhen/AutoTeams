"""WT4 人机协作模式（PRD §5.9 + 重构方案 §7.6 阶段2）。

MVP 2 种协作模式：
1. **人类审批介入**（Human Approval Gate）：流程到审批节点暂停 → 人类审批 → 继续
2. **AI 提议人类确认**（Advise & Confirm）：AI 生成建议 → 人类确认 → AI 执行

第 3 种"人类接管"（Human Takeover）为 P1 功能，本文件预留接口但标记 NotImplemented。

审批门状态机：pending → approved / rejected
- approved：流程继续执行（process_resumed=True）
- rejected：流程终止（process_resumed=False）

工程约束（spec §2.2）：service 层写操作显式 await db.commit()
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collaboration import ApprovalGate
from app.utils.audit import log_audit

logger = logging.getLogger(__name__)


# 协作模式（PRD §5.9）
MODE_ADVISE_CONFIRM = "advise_confirm"
MODE_HUMAN_APPROVAL_GATE = "human_approval_gate"
MODE_HUMAN_TAKEOVER = "human_takeover"  # P1

# 审批状态
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


class HumanAICollaboration:
    """人机协作服务。

    Usage::

        svc = HumanAICollaboration()
        # 模式1：人类审批介入
        gate = await svc.human_approval_gate(db, enterprise_id, process_id, node_id, agent_id)
        # 模式2：AI 提议人类确认
        gate = await svc.advise_and_confirm(db, enterprise_id, agent_id, proposal)
        # 审批
        await svc.approve_gate(db, gate_id, approver_id, comment="同意")
    """

    # ========================================================
    # 创建审批门（统一入口）
    # ========================================================

    async def create_approval_gate(
        self,
        db: AsyncSession,
        enterprise_id: str,
        process_id: str,
        node_id: str,
        agent_id: Optional[str] = None,
        mode: str = MODE_HUMAN_APPROVAL_GATE,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """创建审批门（内部触发，由流程引擎或事件总线调用）。

        Args:
            mode: 协作模式（advise_confirm / human_approval_gate）

        Returns:
            {"gate_id": str, "status": "pending", "mode": str}
        """
        gate = ApprovalGate(
            id=str(uuid.uuid4()),
            enterprise_id=enterprise_id,
            process_id=process_id,
            node_id=node_id,
            agent_id=agent_id,
            status=STATUS_PENDING,
        )
        db.add(gate)
        await db.commit()
        await db.refresh(gate)

        logger.info(
            "审批门已创建: enterprise=%s process=%s node=%s mode=%s gate=%s",
            enterprise_id, process_id, node_id, mode, gate.id,
        )
        return {
            "gate_id": gate.id,
            "status": STATUS_PENDING,
            "mode": mode,
        }

    # ========================================================
    # MVP 模式 1：人类审批介入
    # ========================================================

    async def human_approval_gate(
        self,
        db: AsyncSession,
        enterprise_id: str,
        process_id: str,
        node_id: str,
        agent_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """人类审批介入：流程到审批节点暂停，等待人类审批后继续。

        触发条件（PRD §5.9）：流程定义中有审批节点
        （流程引擎 steps[].approval_required = true）。
        """
        return await self.create_approval_gate(
            db,
            enterprise_id=enterprise_id,
            process_id=process_id,
            node_id=node_id,
            agent_id=agent_id,
            mode=MODE_HUMAN_APPROVAL_GATE,
            payload=payload,
        )

    # ========================================================
    # MVP 模式 2：AI 提议人类确认
    # ========================================================

    async def advise_and_confirm(
        self,
        db: AsyncSession,
        enterprise_id: str,
        agent_id: str,
        proposal: str,
        proposal_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """AI 提议人类确认：AI 生成建议，人类确认后 AI 执行。

        触发条件（PRD §5.9）：任务涉及对外发送/修改重要数据，但不需审批
        （运行模型 runtime_rules.advise_confirm）。

        proposal 为 AI 生成的建议文本；proposal_data 为结构化建议数据，
        存入审批门关联的 process 上下文（process_id 用 agent_id + advise 前缀）。
        """
        process_id = f"advise-{agent_id}"
        node_id = "human_confirm"
        return await self.create_approval_gate(
            db,
            enterprise_id=enterprise_id,
            process_id=process_id,
            node_id=node_id,
            agent_id=agent_id,
            mode=MODE_ADVISE_CONFIRM,
            payload={"proposal": proposal, "proposal_data": proposal_data or {}},
        )

    # ========================================================
    # P1：人类接管（预留接口）
    # ========================================================

    async def human_takeover(
        self,
        db: AsyncSession,
        enterprise_id: str,
        agent_id: str,
        task_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """人类接管：AI 无法处理或连续失败时转交人类（P1）。

        MVP 阶段不实现，抛出 NotImplementedError 供上层捕获后降级。
        """
        raise NotImplementedError(
            "人类接管（Human Takeover）为 P1 功能，MVP 阶段未实现"
        )

    # ========================================================
    # 审批通过 / 拒绝
    # ========================================================

    async def approve_gate(
        self,
        db: AsyncSession,
        gate_id: str,
        approver_id: str,
        comment: Optional[str] = None,
    ) -> dict[str, Any]:
        """审批通过 → 流程继续。

        Returns:
            {"approved": True, "process_resumed": True}
        """
        gate = await self._get_gate(db, gate_id)
        if gate is None:
            raise ValueError(f"审批门不存在: {gate_id}")
        if gate.status != STATUS_PENDING:
            raise ValueError(f"审批门已处理，当前状态: {gate.status}")

        gate.status = STATUS_APPROVED
        gate.approver_id = approver_id
        gate.decided_at = datetime.now(timezone.utc)

        await log_audit(
            db, None, "approve", "approval_gate", gate_id,
            details={"process_id": gate.process_id, "node_id": gate.node_id,
                     "comment": comment},
        )
        await db.commit()

        logger.info("审批通过: gate=%s approver=%s", gate_id, approver_id)
        return {"approved": True, "process_resumed": True}

    async def reject_gate(
        self,
        db: AsyncSession,
        gate_id: str,
        approver_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """审批拒绝 → 流程终止。

        Returns:
            {"rejected": True}
        """
        gate = await self._get_gate(db, gate_id)
        if gate is None:
            raise ValueError(f"审批门不存在: {gate_id}")
        if gate.status != STATUS_PENDING:
            raise ValueError(f"审批门已处理，当前状态: {gate.status}")

        gate.status = STATUS_REJECTED
        gate.approver_id = approver_id
        gate.decided_at = datetime.now(timezone.utc)

        await log_audit(
            db, None, "reject", "approval_gate", gate_id,
            details={"process_id": gate.process_id, "node_id": gate.node_id,
                     "reason": reason},
        )
        await db.commit()

        logger.info("审批拒绝: gate=%s approver=%s reason=%s", gate_id, approver_id, reason)
        return {"rejected": True}

    # ========================================================
    # 查询
    # ========================================================

    async def get_gate(
        self, db: AsyncSession, gate_id: str
    ) -> Optional[ApprovalGate]:
        return await self._get_gate(db, gate_id)

    async def list_gates(
        self,
        db: AsyncSession,
        enterprise_id: str,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ApprovalGate], int]:
        """分页查询审批门。"""
        conditions = [ApprovalGate.enterprise_id == enterprise_id]
        if status:
            conditions.append(ApprovalGate.status == status)

        count_result = await db.execute(
            select(func.count(ApprovalGate.id)).where(*conditions)
        )
        total = int(count_result.scalar() or 0)

        result = await db.execute(
            select(ApprovalGate)
            .where(*conditions)
            .order_by(ApprovalGate.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(result.scalars().all())
        return items, total

    # ========================================================
    # 内部辅助
    # ========================================================

    async def _get_gate(
        self, db: AsyncSession, gate_id: str
    ) -> Optional[ApprovalGate]:
        result = await db.execute(
            select(ApprovalGate).where(ApprovalGate.id == gate_id)
        )
        return result.scalar_one_or_none()


# 模块级单例（无状态，可安全共享）
human_ai_collaboration = HumanAICollaboration()
