"""add_agent_kpi

Revision ID: f7a3c9e1b2d4
Revises: ea618fff2a58
Create Date: 2026-07-24 00:10:00.000000

新建 agent_kpis 表：按天/周/月聚合智能体业务绩效指标，
支撑业务效果仪表盘（ROI / 成本 / 覆盖 / 效率）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a3c9e1b2d4'
down_revision: Union[str, None] = 'ea618fff2a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_kpis',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('period_type', sa.String(length=16), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('task_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_response_time', sa.Float(), nullable=False, server_default='0'),
        sa.Column('first_resolution_rate', sa.Float(), nullable=False, server_default='0'),
        sa.Column('escalation_rate', sa.Float(), nullable=False, server_default='0'),
        sa.Column('accuracy', sa.Float(), nullable=False, server_default='0'),
        sa.Column('satisfaction_score', sa.Float(), nullable=False, server_default='0'),
        sa.Column('token_usage', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estimated_cost', sa.Float(), nullable=False, server_default='0'),
        sa.Column('business_metrics', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_id', 'period_type', 'period_start', name='uq_agent_kpi_agent_period'),
    )
    with op.batch_alter_table('agent_kpis', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_kpis_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_kpis_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index(
            'idx_agent_kpi_enterprise_period',
            ['enterprise_id', 'period_type', 'period_start'],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('agent_kpis', schema=None) as batch_op:
        batch_op.drop_index('idx_agent_kpi_enterprise_period')
        batch_op.drop_index(batch_op.f('ix_agent_kpis_enterprise_id'))
        batch_op.drop_index(batch_op.f('ix_agent_kpis_agent_id'))

    op.drop_table('agent_kpis')
