"""extend_enterprise_fields

Revision ID: a4b5c6d7f0a4
Revises: a3b4c5d6e9f3
Create Date: 2026-07-29 03:06:00.000000

WT2 Enterprise 表加字段：关联当前激活的 Runtime。
依据：重构方案_v3.md §5.2/§5.7（WT2 受限改动 + 数据模型）。
加性迁移：仅新增 current_runtime_version_id 字段，不改现有字段。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4b5c6d7f0a4'
down_revision: Union[str, None] = 'a3b4c5d6e9f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('enterprises', schema=None) as batch_op:
        # 当前激活的 Runtime 版本 ID（指向 runtime_versions.id）
        batch_op.add_column(sa.Column('current_runtime_version_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('enterprises', schema=None) as batch_op:
        batch_op.drop_column('current_runtime_version_id')
