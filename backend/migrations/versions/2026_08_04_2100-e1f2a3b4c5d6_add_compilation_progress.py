"""add_compilation_progress

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-04 21:00:00.000000

UI v4 P3：编译任务真实进度 + 阶段耗时。

背景（docs/UI重构方案_v4_深空仪表.md §一 罪二）：
schemas/compiler.py 的 CompilationJobResponse 已声明 progress 字段，
但 CompilationJob 模型无对应列，导致永远返回 0.0。前端只能用
「已完成阶段数 / 5」估算，10-20 分钟的编译长期卡在 20% 阶梯跳变。

本迁移补齐：
- compilation_jobs.progress        真实进度 0.0-1.0（Pipeline 逐级写入）
- compilation_jobs.stage_started_at 当前阶段开始时间（用于阶段耗时/预估剩余）
- compilation_artifacts.duration_ms 各级实际耗时（回放模式按真实时序播放的依据）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('progress', sa.Float(), nullable=False, server_default='0.0')
        )
        batch_op.add_column(
            sa.Column('stage_started_at', sa.DateTime(), nullable=True)
        )

    with op.batch_alter_table('compilation_artifacts', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('duration_ms', sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('compilation_artifacts', schema=None) as batch_op:
        batch_op.drop_column('duration_ms')

    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.drop_column('stage_started_at')
        batch_op.drop_column('progress')
