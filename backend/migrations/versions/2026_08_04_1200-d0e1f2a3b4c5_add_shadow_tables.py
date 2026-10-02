"""add_shadow_tables

Revision ID: d0e1f2a3b4c5
Revises: c5d6e7f8a9b5
Create Date: 2026-08-04 12:00:00.000000

影子模式：ShadowTask 信任建立任务表。
依据：愿景蓝图 §4.1.1 + 产品完善方案_v3.2 补1。
状态机：shadowing → evaluating → qualified → autonomous。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'd6e7f8a9b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 影子任务：一次 (问题, AI 回答, 真人回答) 的信任建立样本
    op.create_table(
        'shadow_tasks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=True),
        # 任务类型：inquiry / quotation / customer_service / after_sales / ...
        sa.Column('task_type', sa.String(length=64), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('human_answer', sa.Text(), nullable=True),
        sa.Column('ai_answer', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        # 状态机：shadowing / evaluating / qualified / autonomous
        sa.Column('status', sa.String(length=16), nullable=False, server_default='shadowing'),
        # 评估结果：pending / match / mismatch
        sa.Column('eval_result', sa.String(length=16), nullable=True, server_default='pending'),
        sa.Column('promoted_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('shadow_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shadow_tasks_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_shadow_tasks_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index('idx_shadow_tasks_status', ['status'], unique=False)
        batch_op.create_index('idx_shadow_tasks_ent_status', ['enterprise_id', 'status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('shadow_tasks', schema=None) as batch_op:
        batch_op.drop_index('idx_shadow_tasks_ent_status')
        batch_op.drop_index('idx_shadow_tasks_status')
        batch_op.drop_index(batch_op.f('ix_shadow_tasks_agent_id'))
        batch_op.drop_index(batch_op.f('ix_shadow_tasks_enterprise_id'))
    op.drop_table('shadow_tasks')