"""add_memory_tables

Revision ID: a6b7c8d9f2c6
Revises: a5b6c7d8f1b5
Create Date: 2026-07-29 03:10:00.000000

WT3 记忆表：三层记忆架构（长期记忆 + 实体记忆）。
依据：重构方案_v3.md §6.7（WT3 数据模型）+ PRD §5.12。
- long_term_memories: 交互摘要写入向量库（复用 vector_store，collection 前缀 memory_lt_）
- entity_memories: 实体关键属性（客户/商机/产品，关联知识图谱）
注：短期记忆用内存/Redis，不需要独立表。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6b7c8d9f2c6'
down_revision: Union[str, None] = 'a5b6c7d8f1b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 长期记忆：交互摘要（向量化后写入 ChromaDB，本表记录摘要元数据）
    op.create_table(
        'long_term_memories',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        # 向量库中的 ID（ChromaDB vector_id）
        sa.Column('vector_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('long_term_memories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_long_term_memories_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_long_term_memories_enterprise_id'), ['enterprise_id'], unique=False)

    # 实体记忆：客户/商机/产品关键属性（关联知识图谱）
    op.create_table(
        'entity_memories',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('agent_id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        # 实体类型：customer / opportunity / product / order / ...
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        # 实体 ID（如 C-001 / OPP-001 / SL-T100）
        sa.Column('entity_id', sa.String(length=64), nullable=False),
        # 实体属性（JSON，关键属性键值对）
        sa.Column('attributes', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_id', 'entity_type', 'entity_id', name='uq_entity_memory_agent_entity'),
    )
    with op.batch_alter_table('entity_memories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entity_memories_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_memories_enterprise_id'), ['enterprise_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('entity_memories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entity_memories_enterprise_id'))
        batch_op.drop_index(batch_op.f('ix_entity_memories_agent_id'))
    op.drop_table('entity_memories')

    with op.batch_alter_table('long_term_memories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_long_term_memories_enterprise_id'))
        batch_op.drop_index(batch_op.f('ix_long_term_memories_agent_id'))
    op.drop_table('long_term_memories')
