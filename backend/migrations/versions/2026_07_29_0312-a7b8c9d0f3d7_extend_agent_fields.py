"""extend_agent_fields

Revision ID: a7b8c9d0f3d7
Revises: a6b7c8d9f2c6
Create Date: 2026-07-29 03:12:00.000000

WT3 Agent 表加字段：生命周期阶段 + 记忆配置 + KPI 关联。
依据：重构方案_v3.md §6.2/§6.7（WT3 受限改动 + 数据模型）。
加性迁移：仅新增字段，不改现有字段。
- lifecycle_stage: MVP 3 阶段（recruit/training/production）
- memory_config: 记忆配置（short_term/long_term/entity_memory 配置）
- kpi_ids: 关联的 KPI ID 列表
- position_id: 关联的岗位 ID（来自 Runtime 能力矩阵）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0f3d7'
down_revision: Union[str, None] = 'a6b7c8d9f2c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('agents', schema=None) as batch_op:
        # 生命周期阶段：recruit / training / production（MVP 3 阶段）
        batch_op.add_column(sa.Column('lifecycle_stage', sa.String(length=32), nullable=True, server_default='recruit'))
        # 记忆配置（JSON：{short_term:{max_turns:20}, long_term:{enabled:true}, entity_memory:{enabled:true}}）
        batch_op.add_column(sa.Column('memory_config', sa.JSON(), nullable=True))
        # 关联的 KPI ID 列表（JSON array of strings）
        batch_op.add_column(sa.Column('kpi_ids', sa.JSON(), nullable=True))
        # 关联的岗位 ID（来自 Runtime 能力矩阵的 position_id）
        batch_op.add_column(sa.Column('position_id', sa.String(length=64), nullable=True))

    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.create_index('ix_agents_lifecycle_stage', ['lifecycle_stage'], unique=False)
        batch_op.create_index('ix_agents_position_id', ['position_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.drop_index('ix_agents_position_id')
        batch_op.drop_index('ix_agents_lifecycle_stage')

    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.drop_column('position_id')
        batch_op.drop_column('kpi_ids')
        batch_op.drop_column('memory_config')
        batch_op.drop_column('lifecycle_stage')
