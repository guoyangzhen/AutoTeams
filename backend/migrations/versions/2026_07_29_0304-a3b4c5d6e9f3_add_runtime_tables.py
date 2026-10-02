"""add_runtime_tables

Revision ID: a3b4c5d6e9f3
Revises: a2b3c4d5e8f2
Create Date: 2026-07-29 03:04:00.000000

WT2 Runtime 表：Enterprise Runtime 存储 + 版本管理。
依据：重构方案_v3.md §5.7（WT2 数据模型）。
- enterprise_runtimes: 编译产出的可执行组织实体（含 9 字段块 JSONB）
- runtime_versions: 版本快照 + 回滚 + diff 支持
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e9f3'
down_revision: Union[str, None] = 'a2b3c4d5e8f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enterprise Runtime：编译产出的可执行组织实体
    op.create_table(
        'enterprise_runtimes',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        # 语义化版本号 v1.0.0
        sa.Column('version', sa.String(length=32), nullable=False),
        sa.Column('model_version', sa.String(length=32), nullable=True),
        sa.Column('compiled_at', sa.DateTime(), nullable=False),
        # 完成度 0-100
        sa.Column('completeness', sa.Float(), nullable=False, server_default='0'),
        # Runtime 数据（9 字段块：organization/agents/process_engines/collaboration_graph/knowledge_index/tool_registry/audit_trail/...）
        sa.Column('runtime_data', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('enterprise_id', 'version', name='uq_runtime_ent_version'),
    )
    with op.batch_alter_table('enterprise_runtimes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_enterprise_runtimes_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index('ix_enterprise_runtimes_active', ['enterprise_id', 'is_active'], unique=False)

    # Runtime 版本快照
    op.create_table(
        'runtime_versions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('runtime_id', sa.String(length=36), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False),
        sa.Column('changelog', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['runtime_id'], ['enterprise_runtimes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_runtime_versions_runtime_id'), ['runtime_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_runtime_versions_runtime_id'))
    op.drop_table('runtime_versions')

    with op.batch_alter_table('enterprise_runtimes', schema=None) as batch_op:
        batch_op.drop_index('ix_enterprise_runtimes_active')
        batch_op.drop_index(batch_op.f('ix_enterprise_runtimes_enterprise_id'))
    op.drop_table('enterprise_runtimes')
