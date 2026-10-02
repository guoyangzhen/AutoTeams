"""fix_runtime_nullable_fk_and_index_names

Revision ID: b4c5d6e7f8a4
Revises: b3c4d5e6f7a3
Create Date: 2026-07-29 03:26:00.000000

WT6 修正迁移（第二批）：修复 runtime 表 nullable/FK/索引名对齐 WT2 模型。

依据 WT2 实际模型定义（backend/app/models/runtime.py）与
WT6 已创建迁移（a3b4c5d6e9f3 + b3c4d5e6f7a3）的剩余差异：

P0 级差异（数据完整性/FK 约束缺失）：
1. enterprise_runtimes.model_version: nullable=True → NOT NULL
   （WT2 模型 nullable=False，spec §10.2 要求 model_version 必填）
2. enterprise_runtimes.created_by: 补 FK→users.id ON DELETE SET NULL
   （b3c4d5e6f7a3 仅 add_column 未建 FK）
3. runtime_versions.enterprise_id: 补 FK→enterprises.id ON DELETE CASCADE
   （b3c4d5e6f7a3 仅 add_column 未建 FK）

P1 级差异（索引名对齐 WT2 模型，消除 alembic autogenerate schema drift）：
4. 重命名唯一约束: uq_runtime_ent_version → uq_runtime_enterprise_version
5. 重命名复合索引: ix_enterprise_runtimes_active → ix_runtime_enterprise_active

用户确认的 ondelete 策略：
- 保留 WT6 原始迁移的 ondelete（CASCADE for enterprise_id, SET NULL for created_by）
- 这是更安全的策略，不会产生孤儿数据
- WT2 模型应补声明对应 ondelete（WT2 侧修复）

遵循 spec.md §3.2 加性迁移优先 + 禁止修改已合并迁移（新建迁移修正）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4c5d6e7f8a4'
down_revision: Union[str, None] = 'b3c4d5e6f7a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 回填 model_version 的 NULL 值（改为 NOT NULL 前置步骤）
    op.execute(
        "UPDATE enterprise_runtimes SET model_version = 'unknown' "
        "WHERE model_version IS NULL"
    )

    # 2. enterprise_runtimes: model_version nullable → NOT NULL
    #    + 补 created_by FK 约束
    #    + 重命名唯一约束 uq_runtime_ent_version → uq_runtime_enterprise_version
    #    + 重命名复合索引 ix_enterprise_runtimes_active → ix_runtime_enterprise_active
    #    SQLite batch_op 会重建表，可一次性处理多个变更
    with op.batch_alter_table('enterprise_runtimes', schema=None) as batch_op:
        # model_version: nullable=True → nullable=False
        batch_op.alter_column(
            'model_version',
            existing_type=sa.String(length=32),
            nullable=False,
        )
        # 补 created_by FK 约束（b3c4d5e6f7a3 仅 add_column 未建 FK）
        batch_op.create_foreign_key(
            'fk_enterprise_runtimes_created_by',
            'users',
            ['created_by'],
            ['id'],
            ondelete='SET NULL',
        )
        # 重命名唯一约束：drop 旧 + create 新
        batch_op.drop_constraint('uq_runtime_ent_version', type_='unique')
        batch_op.create_unique_constraint(
            'uq_runtime_enterprise_version',
            ['enterprise_id', 'version'],
        )
        # 重命名复合索引：drop 旧 + create 新
        batch_op.drop_index('ix_enterprise_runtimes_active')
        batch_op.create_index(
            'ix_runtime_enterprise_active',
            ['enterprise_id', 'is_active'],
            unique=False,
        )

    # 3. runtime_versions: 补 enterprise_id FK 约束
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        batch_op.create_foreign_key(
            'fk_runtime_versions_enterprise_id',
            'enterprises',
            ['enterprise_id'],
            ['id'],
            ondelete='CASCADE',
        )


def downgrade() -> None:
    # 3. runtime_versions: 删除 enterprise_id FK 约束
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_runtime_versions_enterprise_id', type_='foreignkey')

    # 2. enterprise_runtimes: 还原
    with op.batch_alter_table('enterprise_runtimes', schema=None) as batch_op:
        # 还原复合索引名
        batch_op.drop_index('ix_runtime_enterprise_active')
        batch_op.create_index(
            'ix_enterprise_runtimes_active',
            ['enterprise_id', 'is_active'],
            unique=False,
        )
        # 还原唯一约束名
        batch_op.drop_constraint('uq_runtime_enterprise_version', type_='unique')
        batch_op.create_unique_constraint(
            'uq_runtime_ent_version',
            ['enterprise_id', 'version'],
        )
        # 删除 created_by FK 约束
        batch_op.drop_constraint('fk_enterprise_runtimes_created_by', type_='foreignkey')
        # 还原 model_version nullable=True
        batch_op.alter_column(
            'model_version',
            existing_type=sa.String(length=32),
            nullable=True,
        )
