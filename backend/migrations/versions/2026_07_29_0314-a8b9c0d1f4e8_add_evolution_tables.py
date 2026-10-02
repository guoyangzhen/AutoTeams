"""add_evolution_tables

Revision ID: a8b9c0d1f4e8
Revises: a7b8c9d0f3d7
Create Date: 2026-07-29 03:14:00.000000

WT4 进化层表：AI Advisor 建议 + AI 组织分析指标。
依据：重构方案_v3.md §7.7（WT4 数据模型）。
- advisor_suggestions: 4 类建议（knowledge/process/capability/organization）
- org_metrics: 5 类指标 + L1-L5 成熟度评级数据
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8b9c0d1f4e8'
down_revision: Union[str, None] = 'a7b8c9d0f3d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AI Advisor 建议
    op.create_table(
        'advisor_suggestions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        # 建议类型：knowledge / process / capability / organization
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('impact', sa.Text(), nullable=True),
        # 状态：pending / applied / rejected
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('advisor_suggestions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_advisor_suggestions_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index('idx_advisor_suggestions_ent_status', ['enterprise_id', 'status'], unique=False)

    # AI 组织分析指标
    op.create_table(
        'org_metrics',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        # 指标类型：agent_workload / process_efficiency / tool_usage / business_impact / maturity
        sa.Column('metric_type', sa.String(length=32), nullable=False),
        # 指标值（JSON：{value, unit, details, ...}）
        sa.Column('metric_value', sa.JSON(), nullable=False),
        sa.Column('period', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('org_metrics', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_org_metrics_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index('idx_org_metrics_ent_type_period', ['enterprise_id', 'metric_type', 'period'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('org_metrics', schema=None) as batch_op:
        batch_op.drop_index('idx_org_metrics_ent_type_period')
        batch_op.drop_index(batch_op.f('ix_org_metrics_enterprise_id'))
    op.drop_table('org_metrics')

    with op.batch_alter_table('advisor_suggestions', schema=None) as batch_op:
        batch_op.drop_index('idx_advisor_suggestions_ent_status')
        batch_op.drop_index(batch_op.f('ix_advisor_suggestions_enterprise_id'))
    op.drop_table('advisor_suggestions')
