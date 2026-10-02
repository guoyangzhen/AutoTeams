"""drop_agent_metrics_column

Revision ID: f1a2b3c4d5e6
Revises: e5b2c3d4f5a6
Create Date: 2026-07-27 01:00:00.000000

3.4.3: 移除 agents 表中完全无读写的 metrics 字段（死代码）。
- 审计证据：rg "Agent\.metrics|agent\.metrics" 在生产代码与测试中均无命中
- 该字段曾预留给"缓存的性能指标"，但实际未启用
- 与 AgentKPI 模型功能重叠（AgentKPI 也是死代码，独立处理）

注意事项：
- 此 migration 执行 DROP COLUMN，不可逆（downgrade 会重建空列，但历史数据已丢失）
- 由于 metrics 字段从未被写入，DROP 不会造成业务数据损失
- batch_alter_table 在 SQLite 上自动重建表（DROP COLUMN 在旧 SQLite 不支持）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e5b2c3d4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 3.4.3: 安全 drop agents.metrics 字段（无任何读写点）
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.drop_column('metrics')


def downgrade() -> None:
    # 重建空 metrics 列（历史数据无法恢复，因为该字段从未被写入）
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('metrics', sa.JSON(), nullable=True)
        )
