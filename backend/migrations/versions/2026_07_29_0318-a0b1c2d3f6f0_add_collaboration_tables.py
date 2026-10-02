"""add_collaboration_tables

Revision ID: a0b1c2d3f6f0
Revises: a9b0c1d2f5e9
Create Date: 2026-07-29 03:18:00.000000

WT4 协作表：事件驱动协作 + 审批门 + 操作快照（回滚支持）。
依据：重构方案_v3.md §7.7（WT4 数据模型）+ PRD §5.8/§5.9/§5.10。
- collaboration_events: 事件总线（7 步演示案例：询盘→报价→审批→成交→售后）
- approval_gates: 审批节点（人机协作 MVP 模式 1：人类审批介入）
- operation_snapshots: Agent 操作前置快照（回滚机制）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a0b1c2d3f6f0'
down_revision: Union[str, None] = 'a9b0c1d2f5e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 事件驱动协作：事件总线
    op.create_table(
        'collaboration_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        # 事件类型：new_inquiry / product_query / quotation / financial_review /
        # approval / deal_closed / customer_sync / after_sales / ...
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('source_agent_id', sa.String(length=36), nullable=True),
        sa.Column('target_agent_id', sa.String(length=36), nullable=True),
        # 事件状态：pending / processed / failed
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_agent_id'], ['agents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_agent_id'], ['agents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('collaboration_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_collaboration_events_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index('idx_collaboration_events_type', ['event_type'], unique=False)
        batch_op.create_index('idx_collaboration_events_ent_type', ['enterprise_id', 'event_type'], unique=False)

    # 审批门：人机协作（MVP 模式 1：人类审批介入）
    op.create_table(
        'approval_gates',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('process_id', sa.String(length=64), nullable=False),
        sa.Column('node_id', sa.String(length=64), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=True),
        # 审批状态：pending / approved / rejected
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('approver_id', sa.String(length=36), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['approver_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('approval_gates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approval_gates_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index('idx_approval_gates_status', ['status'], unique=False)
        batch_op.create_index('idx_approval_gates_process', ['process_id', 'node_id'], unique=False)

    # 操作快照：Agent 操作前置快照（回滚机制）
    op.create_table(
        'operation_snapshots',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=False),
        sa.Column('operation', sa.String(length=128), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('operation_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_operation_snapshots_agent_id'), ['agent_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('operation_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_operation_snapshots_agent_id'))
    op.drop_table('operation_snapshots')

    with op.batch_alter_table('approval_gates', schema=None) as batch_op:
        batch_op.drop_index('idx_approval_gates_process')
        batch_op.drop_index('idx_approval_gates_status')
        batch_op.drop_index(batch_op.f('ix_approval_gates_enterprise_id'))
    op.drop_table('approval_gates')

    with op.batch_alter_table('collaboration_events', schema=None) as batch_op:
        batch_op.drop_index('idx_collaboration_events_ent_type')
        batch_op.drop_index('idx_collaboration_events_type')
        batch_op.drop_index(batch_op.f('ix_collaboration_events_enterprise_id'))
    op.drop_table('collaboration_events')
