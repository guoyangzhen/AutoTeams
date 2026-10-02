"""fix_ondelete_missing_tables

Revision ID: g1b2c3d4e5f6
Revises: b2c3d4e5f6a7
Create Date: 2026-07-28 01:00:00.000000

技术审计 R2 C-2 + H-1: 补全 P1-03 遗漏的三张表的 ondelete 策略。

问题根因:
- P1-03 (b1c2d3e4f5a6) 为 conversations/files/skills/messages/skill_executions
  添加了 ondelete=CASCADE，但遗漏了 processing_tasks、task_plans、agent_versions。
- 模型层已声明 ondelete（processing_tasks/task_plans=SET NULL,
  agent_versions=CASCADE），但从未写入数据库 schema，导致模型与 DB 不一致。
- 删除 Agent 时，若存在关联的 ProcessingTask/TaskPlan 记录，
  SQLite 抛 IntegrityError: FOREIGN KEY constraint failed（API 返回 500）。

修复:
- processing_tasks.agent_id → ondelete=SET NULL（与模型声明一致，任务保留但解绑）
- task_plans.agent_id → ondelete=SET NULL（同上）
- agent_versions.agent_id → ondelete=CASCADE（与模型声明一致，版本快照随 Agent 删除）

SQLite 用 batch_alter 重建表；PostgreSQL 走 ALTER CONSTRAINT。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g1b2c3d4e5f6'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
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
    # processing_tasks: agent_id 加 ondelete=SET NULL（任务保留，解绑 Agent）
    _drop_fk_safe('processing_tasks', 'processing_tasks_agent_id_fkey')
    _create_fk('processing_tasks', 'processing_tasks_agent_id_fkey', 'agents', ['agent_id'], ['id'], ondelete='SET NULL')

    # task_plans: agent_id 加 ondelete=SET NULL
    _drop_fk_safe('task_plans', 'task_plans_agent_id_fkey')
    _create_fk('task_plans', 'task_plans_agent_id_fkey', 'agents', ['agent_id'], ['id'], ondelete='SET NULL')

    # agent_versions: agent_id 加 ondelete=CASCADE（版本快照随 Agent 删除）
    _drop_fk_safe('agent_versions', 'agent_versions_agent_id_fkey')
    _create_fk('agent_versions', 'agent_versions_agent_id_fkey', 'agents', ['agent_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    # 回滚：移除 ondelete（恢复为默认 RESTRICT/NO ACTION）
    _drop_fk_safe('agent_versions', 'agent_versions_agent_id_fkey')
    _create_fk('agent_versions', 'agent_versions_agent_id_fkey', 'agents', ['agent_id'], ['id'])

    _drop_fk_safe('task_plans', 'task_plans_agent_id_fkey')
    _create_fk('task_plans', 'task_plans_agent_id_fkey', 'agents', ['agent_id'], ['id'])

    _drop_fk_safe('processing_tasks', 'processing_tasks_agent_id_fkey')
    _create_fk('processing_tasks', 'processing_tasks_agent_id_fkey', 'agents', ['agent_id'], ['id'])
