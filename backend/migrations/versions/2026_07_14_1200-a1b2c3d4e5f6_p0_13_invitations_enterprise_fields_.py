"""P0-13: invitations table, enterprise fields, conversations/user indexes, applied_at type fix

Revision ID: a1b2c3d4e5f6
Revises: 22af9efcfa3d
Create Date: 2026-07-14 12:00:00.000000

修复 P0-13 数据库与模型不一致问题：
1. 创建 invitations 表（初始迁移遗漏）
2. 给 enterprises 加 is_active / invite_max_uses / invite_used_count 字段
3. 给 processing_tasks.progress 加 server_default='0.0'（NOT NULL 无默认值会导致插入失败）
4. 给 conversations.user_id / agent_id 加索引（多租户 JOIN 查询性能）
5. 给 users.enterprise_id 加索引
6. 修改 optimization_histories.applied_at 类型 String → DateTime
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '22af9efcfa3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 创建 invitations 表（初始迁移遗漏）
    op.create_table(
        'invitations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('invited_by_user_id', sa.String(length=36), nullable=False),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('used_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id']),
        sa.ForeignKeyConstraint(['invited_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['used_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_invitations_enterprise_id'),
            ['enterprise_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_invitations_invited_by_user_id'),
            ['invited_by_user_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_invitations_token'),
            ['token'], unique=False,
        )

    # 2. 给 enterprises 加 is_active / invite_max_uses / invite_used_count 字段
    # 用 batch_alter_table 兼容 SQLite（无法直接 ALTER TABLE ADD COLUMN with default）
    with op.batch_alter_table('enterprises', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column('invite_max_uses', sa.Integer(), nullable=False, server_default='10')
        )
        batch_op.add_column(
            sa.Column('invite_used_count', sa.Integer(), nullable=False, server_default='0')
        )

    # 3. 给 processing_tasks.progress 加 server_default（已 apply 的迁移未设置默认值）
    # 使用 batch_alter_table 重建表以修改 server_default
    with op.batch_alter_table('processing_tasks', schema=None) as batch_op:
        batch_op.alter_column(
            'progress',
            existing_type=sa.Float(),
            server_default=sa.text('0.0'),
            existing_nullable=False,
        )

    # 4. 给 conversations.user_id / agent_id 加索引
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_conversations_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_conversations_agent_id'), ['agent_id'], unique=False
        )

    # 5. 给 users.enterprise_id 加索引
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_users_enterprise_id'), ['enterprise_id'], unique=False
        )

    # 6. 修改 optimization_histories.applied_at 类型 String → DateTime
    # SQLite 不支持 ALTER COLUMN TYPE，batch_alter_table 会重建表
    with op.batch_alter_table('optimization_histories', schema=None) as batch_op:
        batch_op.alter_column(
            'applied_at',
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=True,
            # PostgreSQL 转换 String → Timestamp 需显式 USING 表达式，
            # 否则列中存在非空值时会报 "cannot be cast automatically"。
            # 值以 ISO 文本存储，可被 ::timestamp 直接解析。
            postgresql_using='applied_at::timestamp',
        )


def downgrade() -> None:
    # 6. 回退 applied_at 类型
    with op.batch_alter_table('optimization_histories', schema=None) as batch_op:
        batch_op.alter_column(
            'applied_at',
            existing_type=sa.DateTime(),
            type_=sa.String(),
            existing_nullable=True,
        )

    # 5. 删除 users.enterprise_id 索引
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_enterprise_id'))

    # 4. 删除 conversations 索引
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversations_agent_id'))
        batch_op.drop_index(batch_op.f('ix_conversations_user_id'))

    # 3. 回退 processing_tasks.progress server_default
    with op.batch_alter_table('processing_tasks', schema=None) as batch_op:
        batch_op.alter_column(
            'progress',
            existing_type=sa.Float(),
            server_default=None,
            existing_nullable=False,
        )

    # 2. 删除 enterprises 新增字段
    with op.batch_alter_table('enterprises', schema=None) as batch_op:
        batch_op.drop_column('invite_used_count')
        batch_op.drop_column('invite_max_uses')
        batch_op.drop_column('is_active')

    # 1. 删除 invitations 表
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invitations_token'))
        batch_op.drop_index(batch_op.f('ix_invitations_invited_by_user_id'))
        batch_op.drop_index(batch_op.f('ix_invitations_enterprise_id'))

    op.drop_table('invitations')
