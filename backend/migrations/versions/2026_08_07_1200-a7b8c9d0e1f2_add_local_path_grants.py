"""add_local_path_grants

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-08-07 12:00:00.000000

协作工作台与本地工具桥接：本地路径授权表。
用户授权本机文件夹，云端仅存授权记录与一次性 setup token 的哈希，
真正的文件读写删除由用户本机的本地守护进程（Local Runner）执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f5a6b7c8d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'local_path_grants',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=True),
        sa.Column('local_path', sa.Text(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False, server_default='read'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('runner_id', sa.String(length=128), nullable=True),
        sa.Column('tool_manifest', sa.JSON(), nullable=True),
        sa.Column('resolved_path', sa.Text(), nullable=True),
        sa.Column('setup_token_hash', sa.String(length=64), nullable=True),
        sa.Column('setup_token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('claimed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('local_path_grants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_local_path_grants_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_local_path_grants_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('local_path_grants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_local_path_grants_user_id'))
        batch_op.drop_index(batch_op.f('ix_local_path_grants_enterprise_id'))
    op.drop_table('local_path_grants')