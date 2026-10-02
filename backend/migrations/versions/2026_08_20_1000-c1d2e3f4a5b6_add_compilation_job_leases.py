"""add_compilation_job_leases

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-08-20 10:00:00.000000

将五级编译从 HTTP 进程内任务升级为可持久领取的队列任务：保存输入快照、幂等键、
Worker 租约、心跳、尝试次数和取消请求。所有字段均可空或带 server default，保证
升级期间已有历史 Job 不受影响。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("compilation_jobs") as batch_op:
        batch_op.add_column(sa.Column("folder_path", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("interview_completion", sa.Float(), nullable=False, server_default="0.0")
        )
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("lease_owner", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index("ix_compilation_jobs_status", ["status"], unique=False)
        batch_op.create_index("ix_compilation_jobs_idempotency_key", ["idempotency_key"], unique=False)
        batch_op.create_index("ix_compilation_jobs_lease_owner", ["lease_owner"], unique=False)
        batch_op.create_index("ix_compilation_jobs_lease_until", ["lease_until"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("compilation_jobs") as batch_op:
        batch_op.drop_index("ix_compilation_jobs_lease_until")
        batch_op.drop_index("ix_compilation_jobs_lease_owner")
        batch_op.drop_index("ix_compilation_jobs_idempotency_key")
        batch_op.drop_index("ix_compilation_jobs_status")
        batch_op.drop_column("cancel_requested")
        batch_op.drop_column("attempt")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("lease_until")
        batch_op.drop_column("lease_owner")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("interview_completion")
        batch_op.drop_column("folder_path")
