"""P2 字段对齐: relation 枚举值小写化 + event_type 事件类型统一

Revision ID: d6e7f8a9b6c7
Revises: c5d6e7f8a9b5
Create Date: 2026-07-28 02:00:00

数据迁移（无 schema 变更）：
1. knowledge_graphs.edges JSON 数组中的 relation 大写值 → 小写
2. collaboration_events.event_type 旧值 → 与前端 CollaborationEventType 对齐
"""
from alembic import op
import sqlalchemy as sa
import json
import logging

revision: str = 'd6e7f8a9b6c7'
down_revision: str = 'c5d6e7f8a9b5'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

# relation 大写 → 小写映射
RELATION_MAP = {
    "BELONGS_TO": "belongs_to",
    "REPORTS_TO": "reports_to",
    "EXECUTES": "executes",
    "OWES": "owes",
    "HAS_PERMISSION": "has_permission",
    "USES": "uses",
    "PRODUCES": "produces",
    "SERVES": "serves",
}

# event_type 旧值 → 新值映射
EVENT_TYPE_MAP = {
    "new_inquiry": "inquiry_received",
    "quotation": "quotation_generated",
    "quotation_created": "quotation_generated",
    "financial_review": "approval_submitted",
    "approval": "approval_approved",
    "customer_sync": "order_synced",
}


def upgrade() -> None:
    bind = op.get_bind()

    # 1. 迁移 knowledge_graphs.edges JSON 中的 relation 值
    kg_table = sa.table(
        'knowledge_graphs',
        sa.column('id', sa.String),
        sa.column('edges', sa.Text),  # SQLite 用 Text 存 JSON
    )

    rows = bind.execute(
        sa.select(kg_table.c.id, kg_table.c.edges)
    ).fetchall()

    updated_count = 0
    for row in rows:
        if not row.edges:
            continue
        try:
            edges = json.loads(row.edges) if isinstance(row.edges, str) else row.edges
        except (json.JSONDecodeError, TypeError):
            continue

        changed = False
        for edge in edges:
            if isinstance(edge, dict) and 'relation' in edge:
                old_val = edge['relation']
                if old_val in RELATION_MAP:
                    edge['relation'] = RELATION_MAP[old_val]
                    changed = True

        if changed:
            bind.execute(
                kg_table.update().where(kg_table.c.id == row.id).values(
                    edges=json.dumps(edges, ensure_ascii=False)
                )
            )
            updated_count += 1

    logger.info(f"relation 值迁移: 更新了 {updated_count} 条 knowledge_graphs 记录")

    # 2. 迁移 collaboration_events.event_type
    ce_table = sa.table(
        'collaboration_events',
        sa.column('id', sa.String),
        sa.column('event_type', sa.String),
    )

    event_updated = 0
    for old_val, new_val in EVENT_TYPE_MAP.items():
        result = bind.execute(
            ce_table.update().where(
                ce_table.c.event_type == old_val
            ).values(event_type=new_val)
        )
        event_updated += result.rowcount or 0

    logger.info(f"event_type 值迁移: 更新了 {event_updated} 条 collaboration_events 记录")


def downgrade() -> None:
    # 数据迁移不可逆（旧值可能已被覆盖，无法精确还原）
    # 如需回滚，请从备份恢复
    pass
