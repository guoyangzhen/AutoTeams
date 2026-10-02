"""add_compiler_tables

Revision ID: a2b3c4d5e8f2
Revises: a1b2c3d4e7f1
Create Date: 2026-07-29 03:02:00.000000

WT1 编译器表：编译任务记录。
依据：重构方案_v3.md §4.7（WT1 数据模型）。
- compilation_jobs: 五级编译器任务（information/knowledge/process/capability/runtime），
  记录编译阶段、状态、输入输出引用、置信度、完成度。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2b3c4d5e8f2'
down_revision: Union[str, None] = 'a1b2c3d4e7f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'compilation_jobs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        # 编译阶段：information / knowledge / process / capability / runtime
        sa.Column('stage', sa.String(length=32), nullable=False),
        # 状态：pending / running / completed / failed
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        # 输入/输出引用（指向 JSON 存储或外部引用）
        sa.Column('input_ref', sa.Text(), nullable=True),
        sa.Column('output_ref', sa.Text(), nullable=True),
        # 置信度 0-1
        sa.Column('confidence', sa.Float(), nullable=True),
        # 完成度 0-100
        sa.Column('completeness', sa.Float(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_compilation_jobs_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index(
            'idx_compilation_jobs_ent_stage_status',
            ['enterprise_id', 'stage', 'status'],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.drop_index('idx_compilation_jobs_ent_stage_status')
        batch_op.drop_index(batch_op.f('ix_compilation_jobs_enterprise_id'))
    op.drop_table('compilation_jobs')
