"""add_workforce_tables

Revision ID: a5b6c7d8f1b5
Revises: a4b5c6d7f0a4
Create Date: 2026-07-29 03:08:00.000000

WT3 Workforce 表：AI 数字员工生命周期管理。
依据：重构方案_v3.md §6.7（WT3 数据模型）。
- workforce_lifecycle: 9 阶段生命周期记录（MVP 3 阶段：recruit/training/production）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5b6c7d8f1b5'
down_revision: Union[str, None] = 'a4b5c6d7f0a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workforce_lifecycle',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        # 生命周期阶段：recruit / training / production / evaluation / ...
        sa.Column('stage', sa.String(length=32), nullable=False),
        sa.Column('stage_entered_at', sa.DateTime(), nullable=False),
        sa.Column('transition_reason', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('workforce_lifecycle', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_workforce_lifecycle_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_workforce_lifecycle_enterprise_id'), ['enterprise_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('workforce_lifecycle', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_workforce_lifecycle_enterprise_id'))
        batch_op.drop_index(batch_op.f('ix_workforce_lifecycle_agent_id'))
    op.drop_table('workforce_lifecycle')
