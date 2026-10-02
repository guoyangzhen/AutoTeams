"""P1-03: ondelete CASCADE for FK constraints (batch_alter for SQLite)

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-07-14 21:00:00.000000

P1-03 数据库 ondelete 策略：
为所有外键添加 ondelete="CASCADE"，使删除 Agent/User/Conversation/Skill 时
自动级联删除子记录，避免孤立数据。

SQLite 不支持 ALTER TABLE 修改外键约束，必须用 batch_alter 重建表。
PostgreSQL 支持 ALTER TABLE ... DROP/ADD CONSTRAINT，batch_alter 会自动
识别方言走对应路径。

注意：SQLite 的 ondelete CASCADE 需要连接时启用 PRAGMA foreign_keys=ON
（已在 database.py 的 _set_sqlite_pragma 中配置）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_fk_safe(table: str, name: str) -> None:
    """安全删除外键约束：SQLite 外键名不可预期，缺失时忽略。"""
    try:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name, type_='foreignkey')
    except ValueError:
        # 约束不存在（常见于 SQLite 自动命名场景），继续重建
        pass


def _create_fk(table: str, name: str, ref: str, local: list[str], remote: list[str], ondelete: str | None = None) -> None:
    """创建外键约束；batch_alter 会在 SQLite 上重建表以应用变更。"""
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.create_foreign_key(name, ref, local, remote, ondelete=ondelete)


def upgrade() -> None:
    # conversations: user_id / agent_id 加 ondelete=CASCADE
    _drop_fk_safe('conversations', 'conversations_user_id_fkey')
    _create_fk('conversations', 'conversations_user_id_fkey', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    _drop_fk_safe('conversations', 'conversations_agent_id_fkey')
    _create_fk('conversations', 'conversations_agent_id_fkey', 'agents', ['agent_id'], ['id'], ondelete='CASCADE')

    # files: agent_id 加 ondelete=CASCADE
    _drop_fk_safe('files', 'files_agent_id_fkey')
    _create_fk('files', 'files_agent_id_fkey', 'agents', ['agent_id'], ['id'], ondelete='CASCADE')

    # skills: agent_id 加 ondelete=CASCADE
    _drop_fk_safe('skills', 'skills_agent_id_fkey')
    _create_fk('skills', 'skills_agent_id_fkey', 'agents', ['agent_id'], ['id'], ondelete='CASCADE')

    # messages: conversation_id 加 ondelete=CASCADE
    _drop_fk_safe('messages', 'messages_conversation_id_fkey')
    _create_fk('messages', 'messages_conversation_id_fkey', 'conversations', ['conversation_id'], ['id'], ondelete='CASCADE')

    # skill_executions: skill_id / user_id / agent_id 加 ondelete=CASCADE
    _drop_fk_safe('skill_executions', 'skill_executions_skill_id_fkey')
    _create_fk('skill_executions', 'skill_executions_skill_id_fkey', 'skills', ['skill_id'], ['id'], ondelete='CASCADE')
    _drop_fk_safe('skill_executions', 'skill_executions_user_id_fkey')
    _create_fk('skill_executions', 'skill_executions_user_id_fkey', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    _drop_fk_safe('skill_executions', 'skill_executions_agent_id_fkey')
    _create_fk('skill_executions', 'skill_executions_agent_id_fkey', 'agents', ['agent_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    # 回滚：移除 ondelete=CASCADE（恢复为默认 RESTRICT/NO ACTION）
    _drop_fk_safe('skill_executions', 'skill_executions_agent_id_fkey')
    _create_fk('skill_executions', 'skill_executions_agent_id_fkey', 'agents', ['agent_id'], ['id'])
    _drop_fk_safe('skill_executions', 'skill_executions_user_id_fkey')
    _create_fk('skill_executions', 'skill_executions_user_id_fkey', 'users', ['user_id'], ['id'])
    _drop_fk_safe('skill_executions', 'skill_executions_skill_id_fkey')
    _create_fk('skill_executions', 'skill_executions_skill_id_fkey', 'skills', ['skill_id'], ['id'])

    _drop_fk_safe('messages', 'messages_conversation_id_fkey')
    _create_fk('messages', 'messages_conversation_id_fkey', 'conversations', ['conversation_id'], ['id'])

    _drop_fk_safe('skills', 'skills_agent_id_fkey')
    _create_fk('skills', 'skills_agent_id_fkey', 'agents', ['agent_id'], ['id'])

    _drop_fk_safe('files', 'files_agent_id_fkey')
    _create_fk('files', 'files_agent_id_fkey', 'agents', ['agent_id'], ['id'])

    _drop_fk_safe('conversations', 'conversations_agent_id_fkey')
    _create_fk('conversations', 'conversations_agent_id_fkey', 'agents', ['agent_id'], ['id'])
    _drop_fk_safe('conversations', 'conversations_user_id_fkey')
    _create_fk('conversations', 'conversations_user_id_fkey', 'users', ['user_id'], ['id'])
