"""add_cognition_tables

Revision ID: a1b2c3d4e7f1
Revises: g1b2c3d4e5f6
Create Date: 2026-07-29 03:00:00.000000

WT1 认知层表：企业知识图谱、企业画像、企业运行模型。
依据：重构方案_v3.md §4.7（WT1 数据模型）。
- knowledge_graphs: PG JSONB + NetworkX 内存图（13 类实体 + 8 类关系）
- enterprise_profiles: 企业画像（行业/规模/复杂度/成熟度标签）
- enterprise_operating_models: 企业运行模型（设计态，6 大块）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e7f1'
down_revision: Union[str, None] = 'g1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 企业知识图谱：nodes/edges 用 JSON 存储（PG JSONB / SQLite TEXT）
    op.create_table(
        'knowledge_graphs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('nodes', sa.JSON(), nullable=False),
        sa.Column('edges', sa.JSON(), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('enterprise_id', 'version', name='uq_knowledge_graph_ent_version'),
    )
    with op.batch_alter_table('knowledge_graphs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_graphs_enterprise_id'), ['enterprise_id'], unique=False)

    # 企业画像
    op.create_table(
        'enterprise_profiles',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('profile', sa.JSON(), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('enterprise_id', 'version', name='uq_enterprise_profile_ent_version'),
    )
    with op.batch_alter_table('enterprise_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_enterprise_profiles_enterprise_id'), ['enterprise_id'], unique=False)

    # 企业运行模型（设计态）
    op.create_table(
        'enterprise_operating_models',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('model', sa.JSON(), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False, server_default='1'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('enterprise_operating_models', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_enterprise_operating_models_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index('ix_enterprise_operating_models_active', ['enterprise_id', 'is_active'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('enterprise_operating_models', schema=None) as batch_op:
        batch_op.drop_index('ix_enterprise_operating_models_active')
        batch_op.drop_index(batch_op.f('ix_enterprise_operating_models_enterprise_id'))
    op.drop_table('enterprise_operating_models')

    with op.batch_alter_table('enterprise_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_enterprise_profiles_enterprise_id'))
    op.drop_table('enterprise_profiles')

    with op.batch_alter_table('knowledge_graphs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_graphs_enterprise_id'))
    op.drop_table('knowledge_graphs')
