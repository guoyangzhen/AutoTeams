"""add_knowledge_snapshot_to_agent_versions

Revision ID: d4a1b2c3d4e5
Revises: a8f3d2e7c1b9
Create Date: 2026-07-26 03:00:00.000000

D4 6.5: 为 agent_versions 表新增 knowledge_snapshot 字段（JSON, nullable=True），
用于在增量更新前持久化知识库状态快照（文件列表/向量集合名/分块策略/RAG 配置），
支撑知识库版本管理与回滚。

加性迁移（add_column nullable=True），不破坏既有数据。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a1b2c3d4e5'
down_revision: Union[str, None] = 'a8f3d2e7c1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # D4 6.5: agent_versions 表新增 knowledge_snapshot 字段
    # nullable=True 以兼容历史版本记录（D4 迁移前的数据无此字段）
    with op.batch_alter_table('agent_versions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('knowledge_snapshot', sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('agent_versions', schema=None) as batch_op:
        batch_op.drop_column('knowledge_snapshot')
