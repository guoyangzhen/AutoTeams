"""add_audit_log_created_at_index

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-27 02:00:00.000000

3.2.2: 为 audit_logs.created_at 添加索引，加速 _get_last_signature 热路径
- 该查询在每次 log_audit 调用时执行：ORDER BY created_at DESC LIMIT 1
- 无索引时为全表扫描，百万行级别审计日志表会显著拖慢写入
- 添加索引后查询复杂度降为 O(log n)

注意：此 migration 仅添加索引，不修改任何业务字段。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 3.2.2: 添加 created_at 索引（已存在的索引会被 IF NOT EXISTS 跳过）
    op.create_index(
        'ix_audit_logs_created_at',
        'audit_logs',
        ['created_at'],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs')
