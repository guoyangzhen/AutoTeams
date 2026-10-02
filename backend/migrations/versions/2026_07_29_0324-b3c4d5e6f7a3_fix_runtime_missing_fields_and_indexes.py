"""fix_runtime_missing_fields_and_indexes

Revision ID: b3c4d5e6f7a3
Revises: b2c3d4e5f6a2
Create Date: 2026-07-29 03:24:00.000000

WT6 修正迁移：补全 WT2 Runtime 表缺失字段 + 索引名对齐。

依据 WT2 实际模型定义（backend/app/models/runtime.py）与
WT6 已创建迁移（a3b4c5d6e9f3）的差异：

1. enterprise_runtimes 补字段：
   - created_by（WT2 模型 String(36), FK→users.id, nullable）

2. runtime_versions 补字段（致命缺失，WT2 service 层会运行时报错）：
   - enterprise_id（WT2 模型 String(36), FK→enterprises.id, NOT NULL, index）
   - event_type（WT2 模型 String(32), default="save"）
   - metadata（WT2 模型 JSON, nullable，DB 列名 metadata，属性名 metadata_json）
   - updated_at（TimestampMixin 提供，WT6 原迁移遗漏）

3. 补缺失索引：
   - ix_enterprise_runtimes_is_active（WT2 模型 is_active index=True）
   - ix_runtime_versions_enterprise_id（WT2 模型 enterprise_id index=True）
   - ix_runtime_version_enterprise（WT2 模型复合索引 (enterprise_id, created_at)）
   - ix_enterprises_current_runtime_version_id（WT2 模型 enterprise.py index=True）

遵循 spec.md §3.2 加性迁移优先 + 禁止修改已合并迁移（新建迁移修正）。

注：
- runtime_versions 新增 enterprise_id 列设为 nullable=True（因已有行需有值），
  应用层会填充。WT2 模型定义为 NOT NULL，但加性迁移无法对已有行回填 NOT NULL，
  故迁移层放宽为 nullable，应用层保证非空。
- 索引名不一致（uq_runtime_ent_version vs uq_runtime_enterprise_version 等）
  由于 SQLite/PG 修改约束名复杂且非阻塞，暂不重命名，保留 WT6 原命名。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3c4d5e6f7a3'
down_revision: Union[str, None] = 'b2c3d4e5f6a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. enterprise_runtimes: 补 created_by 列
    # WT2 模型: Column(String(36), ForeignKey("users.id"), nullable=True)
    with op.batch_alter_table('enterprise_runtimes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('created_by', sa.String(length=36), nullable=True)
        )
        # 补 is_active 单列索引（WT2 模型 is_active index=True）
        batch_op.create_index(
            'ix_enterprise_runtimes_is_active', ['is_active'], unique=False
        )

    # 2. runtime_versions: 补缺失字段（致命缺失）
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        # enterprise_id: WT2 模型 NOT NULL，但加性迁移对已有行设 nullable
        batch_op.add_column(
            sa.Column('enterprise_id', sa.String(length=36), nullable=True)
        )
        # event_type: WT2 模型 default="save"
        batch_op.add_column(
            sa.Column('event_type', sa.String(length=32), nullable=True, server_default='save')
        )
        # metadata: WT2 模型 JSON nullable（DB 列名 metadata，属性名 metadata_json）
        batch_op.add_column(
            sa.Column('metadata', sa.JSON(), nullable=True)
        )
        # updated_at: TimestampMixin 提供，WT6 原迁移遗漏
        batch_op.add_column(
            sa.Column('updated_at', sa.DateTime(), nullable=True)
        )
        # 补索引
        batch_op.create_index(
            'ix_runtime_versions_enterprise_id', ['enterprise_id'], unique=False
        )
        # 补复合索引（WT2 模型 Index("ix_runtime_version_enterprise", "enterprise_id", "created_at")）
        batch_op.create_index(
            'ix_runtime_version_enterprise', ['enterprise_id', 'created_at'], unique=False
        )

    # 3. enterprises: 补 current_runtime_version_id 索引
    # WT2 模型 enterprise.py 第 25 行 index=True，WT6 迁移 a4b5c6d7f0a4 未创建
    with op.batch_alter_table('enterprises', schema=None) as batch_op:
        batch_op.create_index(
            'ix_enterprises_current_runtime_version_id',
            ['current_runtime_version_id'], unique=False
        )


def downgrade() -> None:
    # 3. enterprises: 删索引
    with op.batch_alter_table('enterprises', schema=None) as batch_op:
        batch_op.drop_index('ix_enterprises_current_runtime_version_id')

    # 2. runtime_versions: 删字段与索引
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        batch_op.drop_index('ix_runtime_version_enterprise')
        batch_op.drop_index('ix_runtime_versions_enterprise_id')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('metadata')
        batch_op.drop_column('event_type')
        batch_op.drop_column('enterprise_id')

    # 1. enterprise_runtimes: 删字段与索引
    with op.batch_alter_table('enterprise_runtimes', schema=None) as batch_op:
        batch_op.drop_index('ix_enterprise_runtimes_is_active')
        batch_op.drop_column('created_by')
