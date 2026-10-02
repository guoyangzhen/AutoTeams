"""fix_naive_timestamps_to_timestamptz

Revision ID: b0c1d2e3f4a5
Revises: a7b8c9d0e1f2
Create Date: 2026-08-09 09:00:00.000000

修复 PostgreSQL 生产环境时区兼容问题（登录等写操作 500）：
应用代码统一使用带时区（aware）的 UTC 时间（utcnow / datetime.now(timezone.utc)），
但本仓库此前创建的迁移把所有时间戳列建成 naive（timestamp without time zone），
asyncpg 拒绝将 aware 时间写入 naive 列，报
"can't subtract offset-naive and offset-aware datetimes" 导致整请求 500。

本迁移把 public 架构下所有仍为 naive 的时间戳列转换为 timestamptz，
已存在的 naive 值统一按 UTC 语义解释（AT TIME ZONE 'UTC'）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = 'b0c1d2e3f4a5'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # 动态遍历 public 下所有 naive 时间戳列，转为 timestamptz（aware）。
    # 幂等：转换后不再是 naive，重复执行不会重复处理。
    op.execute(text(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT table_schema, table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type = 'timestamp without time zone'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC''',
                    r.table_schema, r.table_name, r.column_name, r.column_name
                );
            END LOOP;
        END $$;
        """
    ))


def downgrade() -> None:
    # 逆操作：将 public 下 timestamptz 列转回 naive（按 UTC 丢弃时区）。
    # 注意：仅用于回滚本次修复；若库中本就有 timestamptz 列会被一并转换，需谨慎。
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    op.execute(text(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT table_schema, table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type = 'timestamp with time zone'
            LOOP
                EXECUTE format(
                    'ALTER TABLE %I.%I ALTER COLUMN %I TYPE timestamp WITHOUT TIME ZONE USING %I AT TIME ZONE ''UTC''',
                    r.table_schema, r.table_name, r.column_name, r.column_name
                );
            END LOOP;
        END $$;
        """
    ))
