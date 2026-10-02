"""c5_audit_hmac_and_data_security

Revision ID: a8f3d2e7c1b9
Revises: b6a1c2d3e4f5, f7a3c9e1b2d4
Create Date: 2026-07-24 02:00:00.000000

C5: 数据层与审计安全
- 5.3.7: audit_logs 表新增 prev_hash / signature 字段（HMAC 链式防篡改）
- L3: user_agent 字段长度限制为 255（截断在应用层完成）
- DB-02: files 表新增 (agent_id, content_hash) 唯一约束，防止重复内容入库
- T22: PostgreSQL 行级安全（RLS）策略（仅 PostgreSQL 执行，SQLite 跳过）

合并迁移：将 b6_digital_employee_templates 和 add_agent_kpi 两个分支合并为单 head。
"""
from typing import Sequence, Union

import logging

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8f3d2e7c1b9'
down_revision: Union[str, Sequence[str], None] = ('b6a1c2d3e4f5', 'f7a3c9e1b2d4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 5.3.7: audit_logs 新增 HMAC 链式签名字段
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('prev_hash', sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column('signature', sa.String(length=128), nullable=True)
        )

    # DB-02: files 表新增 (agent_id, content_hash) 唯一约束
    # batch_alter_table 在 SQLite 上自动处理约束，PostgreSQL 直接添加
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_files_agent_content_hash',
            ['agent_id', 'content_hash'],
        )

    # T22: PostgreSQL 行级安全（RLS）策略
    # 仅在 PostgreSQL 上执行；SQLite 不支持 RLS，安全隔离由应用层保证
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        _apply_postgres_rls(bind)


def downgrade() -> None:
    bind = op.get_bind()

    # T22: 回滚 PostgreSQL RLS
    if bind.dialect.name == 'postgresql':
        _revert_postgres_rls(bind)

    # DB-02: 回滚 files 唯一约束
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.drop_constraint('uq_files_agent_content_hash', type_='unique')

    # 5.3.7: 回滚 audit_logs HMAC 字段
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_column('signature')
        batch_op.drop_column('prev_hash')


def _apply_postgres_rls(bind) -> None:
    """T22: 为核心业务表启用 PostgreSQL 行级安全策略。

    策略：企业成员只能访问本企业(enterprise_id 匹配)的数据行。
    需要应用层在每次请求开始时 SET LOCAL ROLE 或设置 session 变量
    app.current_enterprise_id，RLS 策略基于此变量过滤。
    """
    # 需要启用 RLS 的表及其 enterprise_id 列名
    rls_tables = {
        'agents': 'enterprise_id',
        'files': None,  # files 通过 agent_id 间接关联，暂不直接加 RLS
        'audit_logs': None,  # audit_logs 通过 user_id 间接关联
        'agent_kpis': 'enterprise_id',
    }

    for table_name, ent_col in rls_tables.items():
        if ent_col is None:
            continue
        # 启用 RLS
        op.execute(f'ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY')
        # 创建策略：仅允许访问本企业数据
        # 使用 session 变量 app.current_enterprise_id（由应用层设置）
        op.execute(
            f"""CREATE POLICY {table_name}_enterprise_isolation
            ON {table_name}
            USING ({ent_col}::text = current_setting('app.current_enterprise_id', true))
            WITH CHECK ({ent_col}::text = current_setting('app.current_enterprise_id', true))
            """
        )
        logger_info = f"[RLS] 已为表 {table_name} 启用企业级行级安全策略"
        logging.getLogger("alembic.runtime.migration").info(logger_info)


def _revert_postgres_rls(bind) -> None:
    """T22: 回滚 PostgreSQL RLS 策略。"""
    rls_tables = {
        'agents': 'enterprise_id',
        'agent_kpis': 'enterprise_id',
    }

    for table_name in rls_tables:
        op.execute(f'DROP POLICY IF EXISTS {table_name}_enterprise_isolation ON {table_name}')
        op.execute(f'ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY')
