"""add_user_role_check_constraint

Revision ID: e5b2c3d4f5a6
Revises: d4a1b2c3d4e5
Create Date: 2026-07-26 10:00:00.000000

3.4.6: 为 users 表 User.role 字段添加 CHECK 约束，限制 role 取值为
('admin', 'member') 之一，防止 DB 层写入非法值（如 'superadmin'、'' 等）。

注意事项：
- 此 migration 会在 users 表上添加 CHECK 约束，要求 role 必须是 ('admin', 'member') 之一。
- 如果数据库中已存在非法 role 值，此 migration 会失败。执行前请先运行数据修复脚本：
  UPDATE users SET role = 'member' WHERE role NOT IN ('admin', 'member') OR role IS NULL;
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b2c3d4f5a6'
down_revision: Union[str, None] = 'd4a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 3.4.6: users 表新增 role CHECK 约束（仅允许 'admin' / 'member'）
    # batch_alter_table 在 SQLite 上自动重建表以支持约束，PostgreSQL 直接添加
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'uq_user_role_valid',
            "role IN ('admin', 'member')",
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_user_role_valid', type_='check')
