"""fix_compiler_missing_fields_and_artifacts

Revision ID: b2c3d4e5f6a2
Revises: b1c2d3e4f5a1
Create Date: 2026-07-29 03:22:00.000000

WT6 修正迁移：补全 WT1 编译器表缺失字段 + 创建 compilation_artifacts 表。

依据 WT1 实际模型定义（backend/app/models/compiler.py）与
WT6 已创建迁移（a2b3c4d5e8f2）的差异：

1. compilation_jobs 补字段：
   - trigger_source（WT1 模型 default='manual'）
   - affected_stages（WT1 模型 nullable=True）

2. 新建 compilation_artifacts 表（WT1 的 base.py/pipeline.py/compiler API
   直接读写此表，WT6 原迁移完全缺失此表，为致命缺口）

遵循 spec.md §3.2 加性迁移优先 + 禁止修改已合并迁移（新建迁移修正）。

注：WT6 原迁移中的 input_ref/output_ref 列保留不删（WT1 未使用但不破坏功能，
删除列属破坏性操作，违反加性迁移原则）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a2'
down_revision: Union[str, None] = 'b1c2d3e4f5a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. compilation_jobs: 补 trigger_source 列
    # WT1 模型: Column(String(64), nullable=False, default="manual")
    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('trigger_source', sa.String(length=64), nullable=False, server_default='manual')
        )

    # 2. compilation_jobs: 补 affected_stages 列
    # WT1 模型: Column(String(256), nullable=True) —— 增量重编译受影响层级
    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('affected_stages', sa.String(length=256), nullable=True)
        )

    # 3. 新建 compilation_artifacts 表（WT1 编译产物存储）
    # WT1 模型: CompilationArtifact(base.py + pipeline.py + compiler API 直接读写)
    op.create_table(
        'compilation_artifacts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('job_id', sa.String(length=36), nullable=False),
        sa.Column('stage', sa.String(length=32), nullable=False),
        sa.Column('output', sa.JSON(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0'),
        sa.Column('discovered_summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['compilation_jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('compilation_artifacts', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_compilation_artifacts_enterprise_id'),
            ['enterprise_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_compilation_artifacts_job_id'),
            ['job_id'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('compilation_artifacts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_compilation_artifacts_job_id'))
        batch_op.drop_index(batch_op.f('ix_compilation_artifacts_enterprise_id'))
    op.drop_table('compilation_artifacts')

    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.drop_column('affected_stages')

    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.drop_column('trigger_source')
