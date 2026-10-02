"""flow_run_state

Revision ID: e3f4a5b6c7d9
Revises: d2e3f4a5b6c8

AUD-15：Flow 执行改为服务器侧状态机后，运行态与审批记录必须落库，
否则 HTTP 进程重启就会丢失"执行到哪一步、谁批的"。

- flow_runs      ：执行运行态（企业隔离、当前节点、状态 JSON、version 乐观锁）
- flow_approvals ：审批记录，绑定 (run_id, node_id, 审批人身份) 与审批结论
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d9"
down_revision: Union[str, None] = "d2e3f4a5b6c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("flow_id", sa.String(length=64), nullable=False),
        sa.Column("flow_version", sa.String(length=32), nullable=False),
        sa.Column("current_node_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("last_output", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_by", sa.String(length=36), nullable=True),
        sa.Column("last_actor_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["started_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flow_runs_enterprise_id", "flow_runs", ["enterprise_id"], unique=False)
    op.create_index("ix_flow_runs_enterprise_created", "flow_runs", ["enterprise_id", "created_at"], unique=False)
    op.create_index("ix_flow_runs_flow_id", "flow_runs", ["flow_id"], unique=False)
    op.create_index("ix_flow_runs_last_actor_id", "flow_runs", ["last_actor_id"], unique=False)
    op.create_index("ix_flow_runs_started_by", "flow_runs", ["started_by"], unique=False)
    op.create_index("ix_flow_runs_status", "flow_runs", ["status"], unique=False)

    op.create_table(
        "flow_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("approver_user_id", sa.String(length=36), nullable=False),
        sa.Column("approver_email", sa.String(length=255), nullable=False),
        sa.Column("approver_role", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("applied_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approver_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["flow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flow_approvals_approver_user_id", "flow_approvals", ["approver_user_id"], unique=False)
    op.create_index("ix_flow_approvals_enterprise_id", "flow_approvals", ["enterprise_id"], unique=False)
    op.create_index("ix_flow_approvals_run_id", "flow_approvals", ["run_id"], unique=False)
    op.create_index("ix_flow_approvals_run_node", "flow_approvals", ["run_id", "node_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_flow_approvals_run_node", table_name="flow_approvals")
    op.drop_index("ix_flow_approvals_run_id", table_name="flow_approvals")
    op.drop_index("ix_flow_approvals_enterprise_id", table_name="flow_approvals")
    op.drop_index("ix_flow_approvals_approver_user_id", table_name="flow_approvals")
    op.drop_table("flow_approvals")

    op.drop_index("ix_flow_runs_status", table_name="flow_runs")
    op.drop_index("ix_flow_runs_started_by", table_name="flow_runs")
    op.drop_index("ix_flow_runs_last_actor_id", table_name="flow_runs")
    op.drop_index("ix_flow_runs_enterprise_id", table_name="flow_runs")
    op.drop_index("ix_flow_runs_enterprise_created", table_name="flow_runs")
    op.drop_table("flow_runs")
