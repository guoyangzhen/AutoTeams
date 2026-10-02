"""为演示企业补充「待审批」审批门数据。

背景：任务流看板的「待审批」纵列直接渲染 ApprovalGate.status == 'pending' 的记录。
当前演示企业（demo@autoteams.example，enterprise_id=327e4a3f-...）的审批门全部为
approved，导致看板该纵列永远显示「暂无待审批」，批准/拒绝按钮无对象可操作。

本脚本为演示企业插入若干条真实、可审批的 pending 审批门（人机协作 MVP
模式 1：人类审批介入），使看板出现可批准/拒绝的待办。

幂等性：执行前先清理该企业已有的 pending 审批门再重建。

用法：
    cd backend
    python -m scripts.seed_pending_approvals
"""
import asyncio
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select

from app.database import async_session_factory
from app.models.agent import Agent
from app.models.collaboration import ApprovalGate
from app.utils.time import utcnow

# 演示企业（与 demo@autoteams.example 登录用户一致）
DEMO_ENTERPRISE_ID = "327e4a3f-38c1-48c9-94e8-adce03e47f4a"

# 待审批清单：(process_id, node_id, 需求方岗位名, 金额, 距现在小时数)
PENDING_GATES = [
    ("quotation-approval-OPP-021", "finance_review", "销售代表（AI）", 68000, 3),
    ("quotation-approval-OPP-022", "finance_review", "销售代表（AI）", 24000, 6),
    ("purchase-approval-PO-118", "purchase_review", "采购专员（AI）", 15600, 2),
    ("purchase-approval-PO-119", "purchase_review", "采购专员（AI）", 9200, 9),
    ("quotation-approval-OPP-023", "finance_review", "销售代表（AI）", 128000, 1),
]


async def seed() -> int:
    async with async_session_factory() as db:
        # 幂等：清理该企业已有 pending 门，避免重复执行累积
        await db.execute(
            delete(ApprovalGate).where(
                ApprovalGate.enterprise_id == DEMO_ENTERPRISE_ID,
                ApprovalGate.status == "pending",
            )
        )

        # 按岗位名取需求方 Agent
        result = await db.execute(
            select(Agent).where(Agent.enterprise_id == DEMO_ENTERPRISE_ID)
        )
        by_name = {a.name: a for a in result.scalars().all()}

        now = utcnow()
        count = 0
        for process_id, node_id, requester_name, amount, hours_ago in PENDING_GATES:
            requester = by_name.get(requester_name)
            db.add(
                ApprovalGate(
                    enterprise_id=DEMO_ENTERPRISE_ID,
                    process_id=process_id,
                    node_id=node_id,
                    agent_id=requester.id if requester else None,
                    status="pending",
                    created_at=now - timedelta(hours=hours_ago),
                )
            )
            count += 1

        await db.commit()
        return count


async def main() -> None:
    count = await seed()
    print(f"[seed_pending_approvals] 已为演示企业写入 {count} 条待审批审批门")


if __name__ == "__main__":
    asyncio.run(main())