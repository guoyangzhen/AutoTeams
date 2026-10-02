"""processing_task_leases

Revision ID: a5b6c7d8e9f1
Revises: f4a5b6c7d8e0
Create Date: 2026-09-28 21:00:00.000000

AUD-17：文档处理任务此前靠进程内 `asyncio.create_task` 执行，重启即丢；
启动时又把所有 `processing` 任务一律标记 failed，会误伤仍由其他实例持有的任务。

本迁移为 `processing_tasks` 增加租约字段，使任务可以被耐久 worker 领取：

- `lease_owner` / `lease_until` / `heartbeat_at`：租约持有人与续租；
- `attempt`：投递次数（崩溃后可重投）；
- `cancel_requested`：跨进程取消信号；
- `worker_id`：执行该任务的 worker 身份；
- `idempotency_key` / `external_run_id` / `run_config`：幂等与重投所需的配置快照。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f1"
down_revision: Union[str, None] = "f4a5b6c7d8e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("processing_tasks", sa.Column("lease_owner", sa.String(length=128), nullable=True))
    op.add_column("processing_tasks", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("processing_tasks", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "processing_tasks",
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "processing_tasks",
        sa.Column("cancel_requested", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("processing_tasks", sa.Column("worker_id", sa.String(length=128), nullable=True))
    op.add_column("processing_tasks", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column("processing_tasks", sa.Column("external_run_id", sa.String(length=128), nullable=True))
    op.add_column("processing_tasks", sa.Column("run_config", sa.JSON(), nullable=True))
    op.create_index("ix_processing_tasks_lease_until", "processing_tasks", ["lease_until"])
    op.create_index("ix_processing_tasks_lease_owner", "processing_tasks", ["lease_owner"])
    op.create_index("ix_processing_tasks_idempotency_key", "processing_tasks", ["idempotency_key"])
    op.create_index(
        "ix_processing_tasks_status_lease",
        "processing_tasks",
        ["status", "lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_processing_tasks_status_lease", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_idempotency_key", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_lease_owner", table_name="processing_tasks")
    op.drop_index("ix_processing_tasks_lease_until", table_name="processing_tasks")
    op.drop_column("processing_tasks", "run_config")
    op.drop_column("processing_tasks", "external_run_id")
    op.drop_column("processing_tasks", "idempotency_key")
    op.drop_column("processing_tasks", "worker_id")
    op.drop_column("processing_tasks", "cancel_requested")
    op.drop_column("processing_tasks", "attempt")
    op.drop_column("processing_tasks", "heartbeat_at")
    op.drop_column("processing_tasks", "lease_until")
    op.drop_column("processing_tasks", "lease_owner")
