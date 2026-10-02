"""fix_cognition_missing_fields

Revision ID: b1c2d3e4f5a1
Revises: a0b1c2d3f6f0
Create Date: 2026-07-29 03:20:00.000000

WT6 修正迁移：补全 WT1 认知层表缺失字段。

依据 WT1 实际模型定义（backend/app/models/cognition.py）与
WT6 已创建迁移（a1b2c3d4e7f1）的差异，加性补列：
- knowledge_graphs: 补 is_active 列（WT1 模型 default=True）
- enterprise_profiles: 补 completeness_score 列（WT1 模型 default=0）
- enterprise_operating_models: 补 completeness 列（WT1 模型 default=0）

并修正 version 字段 server_default：
- WT1 模型 default='v1.0.0'，WT6 迁移 server_default='1'
  由于 SQLite batch_op 修改 server_default 行为不一致，且修改 server_default
  非加性操作，此处仅补字段，不修改现有 server_default（应用层 default 会覆盖）。

遵循 spec.md §3.2 加性迁移优先 + 禁止修改已合并迁移（新建迁移修正）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a1'
down_revision: Union[str, None] = 'a0b1c2d3f6f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. knowledge_graphs: 补 is_active 列
    # WT1 模型 is_active = Column(Boolean, nullable=False, default=True)
    with op.batch_alter_table('knowledge_graphs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true())
        )

    # 2. enterprise_profiles: 补 completeness_score 列
    # WT1 模型 completeness_score = Column(Integer, nullable=False, default=0)
    with op.batch_alter_table('enterprise_profiles', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('completeness_score', sa.Integer(), nullable=False, server_default='0')
        )

    # 3. enterprise_operating_models: 补 completeness 列
    # WT1 模型 completeness = Column(Integer, nullable=False, default=0)
    with op.batch_alter_table('enterprise_operating_models', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('completeness', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    with op.batch_alter_table('enterprise_operating_models', schema=None) as batch_op:
        batch_op.drop_column('completeness')

    with op.batch_alter_table('enterprise_profiles', schema=None) as batch_op:
        batch_op.drop_column('completeness_score')

    with op.batch_alter_table('knowledge_graphs', schema=None) as batch_op:
        batch_op.drop_column('is_active')
