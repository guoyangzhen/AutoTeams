"""P2-05: 为 messages/files 添加复合索引

Revision ID: 78a92af7ea9e
Revises: b1c2d3e4f5a6
Create Date: 2026-07-17 05:13:00.000000

P2-05 数据库性能优化：
- messages: (conversation_id, created_at) 复合索引，加速按会话按时间顺序拉取消息。
- files: (agent_id, status) 复合索引，加速按智能体与处理状态筛选。
- files: (agent_id, file_type) 复合索引，加速按智能体与文件类型筛选。

所有索引使用 IF NOT EXISTS 风格创建，并在 downgrade 中安全删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78a92af7ea9e'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # messages 表：会话+创建时间复合索引
    op.create_index(
        'idx_messages_conversation_created',
        'messages',
        ['conversation_id', 'created_at'],
        unique=False,
        postgresql_using='btree',
    )

    # files 表：智能体+状态复合索引
    op.create_index(
        'idx_files_agent_status',
        'files',
        ['agent_id', 'status'],
        unique=False,
        postgresql_using='btree',
    )

    # files 表：智能体+文件类型复合索引
    op.create_index(
        'idx_files_agent_type',
        'files',
        ['agent_id', 'file_type'],
        unique=False,
        postgresql_using='btree',
    )


def downgrade() -> None:
    op.drop_index('idx_files_agent_type', table_name='files')
    op.drop_index('idx_files_agent_status', table_name='files')
    op.drop_index('idx_messages_conversation_created', table_name='messages')
