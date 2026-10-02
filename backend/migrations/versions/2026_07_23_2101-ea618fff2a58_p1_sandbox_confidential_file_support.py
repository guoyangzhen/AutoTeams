"""p1_sandbox_confidential_file_support

Revision ID: ea618fff2a58
Revises: 7924959bd8db
Create Date: 2026-07-23 21:01:03.944221

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea618fff2a58'
down_revision: Union[str, None] = '7924959bd8db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P1-SANDBOX: 创建涉密文件授权访问表
    op.create_table('confidential_file_accesses',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('file_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('granted_by', sa.String(length=36), nullable=False),
    sa.Column('granted_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('confidential_file_accesses', schema=None) as batch_op:
        batch_op.create_index('idx_confidential_file_user', ['file_id', 'user_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_confidential_file_accesses_file_id'), ['file_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_confidential_file_accesses_user_id'), ['user_id'], unique=False)

    # P1-SANDBOX: 为 files 表扩展涉密文件字段
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_highly_confidential', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('confidential_status', sa.String(length=32), nullable=False, server_default=sa.text("'none'")))


def downgrade() -> None:
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.drop_column('confidential_status')
        batch_op.drop_column('is_highly_confidential')

    with op.batch_alter_table('confidential_file_accesses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_confidential_file_accesses_user_id'))
        batch_op.drop_index(batch_op.f('ix_confidential_file_accesses_file_id'))
        batch_op.drop_index('idx_confidential_file_user')

    op.drop_table('confidential_file_accesses')
