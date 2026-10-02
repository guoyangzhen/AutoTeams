"""P2-06: 为 agents 表增加 folder_path 字段

Revision ID: 1064f365842c
Revises: 78a92af7ea9e
Create Date: 2026-07-18 05:13:00.000000

BE-REL-04 可靠性优化：
- agents 表新增 folder_path 字段，在 Agent 构建时持久化监控目录。
- 避免文件监控/增量更新依赖第一个 File 记录的路径推导，防止首个文件被删除或路径变更后监控失效。

字段使用 Text 类型以兼容长路径，允许为 NULL 以兼容旧数据。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1064f365842c'
down_revision: Union[str, None] = '78a92af7ea9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'agents',
        sa.Column('folder_path', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('agents', 'folder_path')
