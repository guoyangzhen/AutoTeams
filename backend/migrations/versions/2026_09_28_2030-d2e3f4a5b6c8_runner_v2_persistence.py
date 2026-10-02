"""runner_v2_persistence

Revision ID: d2e3f4a5b6c8
Revises: c1d2e3f4a5b7

AUD-04 / AUD-18：把 Local Runner 2.0 的设备档案、任务账本、视窗帧、双因子
挑战、审计留痕与端侧指令从进程内存迁到数据库。

- runner_devices               设备档案，注册时固化 enterprise_id / owner_user_id
- runner_device_credentials    逐设备长期凭据（只存 SHA-256 哈希，可单独撤销）
- runner_device_tokens         逐设备短期访问令牌（只存哈希，默认 15 分钟）
- runner_tasks                 物理任务账本（企业 + 设备双重绑定）
- runner_task_frames           视窗灰度帧（任务维度唯一 seq + 环形上界索引）
- runner_challenges            高危操作双因子挑战（确认码 Fernet 密文落库）
- runner_audit_entries         操作审计留痕
- runner_device_commands       云端 → 设备的一次性指令（端侧工具桥接载体）

结构与 ``app/models/runner_v2.py`` 一一对应；只建表与索引，不含数据搬迁。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c8"
down_revision: Union[str, None] = "c1d2e3f4a5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runner_devices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("runner_id", sa.String(length=96), nullable=False),
        sa.Column("device_label", sa.String(length=128), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_id", "runner_id", name="uq_runner_devices_enterprise_runner"
        ),
    )
    op.create_index("ix_runner_devices_enterprise_id", "runner_devices", ["enterprise_id"])
    op.create_index("ix_runner_devices_owner_user_id", "runner_devices", ["owner_user_id"])

    op.create_table(
        "runner_device_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["runner_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runner_device_credentials_device_id", "runner_device_credentials", ["device_id"]
    )
    op.create_index(
        "ix_runner_device_credentials_secret_hash", "runner_device_credentials", ["secret_hash"]
    )

    op.create_table(
        "runner_device_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["runner_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runner_device_tokens_device_id", "runner_device_tokens", ["device_id"])
    op.create_index("ix_runner_device_tokens_token_hash", "runner_device_tokens", ["token_hash"])

    op.create_table(
        "runner_tasks",
        sa.Column("task_id", sa.String(length=40), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("verdict", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("challenge_id", sa.String(length=40), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("last_receipt_id", sa.String(length=64), nullable=True),
        sa.Column("frame_seq", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["runner_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("ix_runner_tasks_enterprise_id", "runner_tasks", ["enterprise_id"])
    op.create_index("ix_runner_tasks_device_id", "runner_tasks", ["device_id"])
    op.create_index("ix_runner_tasks_state", "runner_tasks", ["state"])
    op.create_index("ix_runner_tasks_challenge_id", "runner_tasks", ["challenge_id"])
    op.create_index("ix_runner_tasks_device_state", "runner_tasks", ["device_id", "state"])

    op.create_table(
        "runner_task_frames",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("payload_b64", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["runner_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "seq", name="uq_runner_task_frames_task_seq"),
    )
    op.create_index("ix_runner_task_frames_task_id", "runner_task_frames", ["task_id"])

    op.create_table(
        "runner_challenges",
        sa.Column("challenge_id", sa.String(length=40), nullable=False),
        sa.Column("task_id", sa.String(length=40), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("device_code_encrypted", sa.Text(), nullable=False),
        sa.Column("endpoint_confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("endpoint_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["runner_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["runner_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("challenge_id"),
    )
    op.create_index("ix_runner_challenges_task_id", "runner_challenges", ["task_id"])
    op.create_index("ix_runner_challenges_enterprise_id", "runner_challenges", ["enterprise_id"])
    op.create_index("ix_runner_challenges_device_id", "runner_challenges", ["device_id"])
    op.create_index("ix_runner_challenges_state", "runner_challenges", ["state"])

    op.create_table(
        "runner_audit_entries",
        sa.Column("trace_id", sa.String(length=40), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=40), nullable=True),
        sa.Column("device_id", sa.String(length=36), nullable=True),
        sa.Column("runner_id", sa.String(length=96), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_runner_audit_entries_enterprise_id", "runner_audit_entries", ["enterprise_id"])
    op.create_index("ix_runner_audit_entries_task_id", "runner_audit_entries", ["task_id"])
    op.create_index("ix_runner_audit_entries_device_id", "runner_audit_entries", ["device_id"])
    op.create_index("ix_runner_audit_entries_event", "runner_audit_entries", ["event"])

    op.create_table(
        "runner_device_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=True),
        sa.Column("max_bytes", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["runner_devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runner_device_commands_enterprise_id", "runner_device_commands", ["enterprise_id"]
    )
    op.create_index("ix_runner_device_commands_device_id", "runner_device_commands", ["device_id"])
    op.create_index("ix_runner_device_commands_state", "runner_device_commands", ["state"])
    op.create_index(
        "ix_runner_device_commands_device_state", "runner_device_commands", ["device_id", "state"]
    )


def downgrade() -> None:
    op.drop_index("ix_runner_device_commands_device_state", table_name="runner_device_commands")
    op.drop_index("ix_runner_device_commands_state", table_name="runner_device_commands")
    op.drop_index("ix_runner_device_commands_device_id", table_name="runner_device_commands")
    op.drop_index("ix_runner_device_commands_enterprise_id", table_name="runner_device_commands")
    op.drop_table("runner_device_commands")

    op.drop_index("ix_runner_audit_entries_event", table_name="runner_audit_entries")
    op.drop_index("ix_runner_audit_entries_device_id", table_name="runner_audit_entries")
    op.drop_index("ix_runner_audit_entries_task_id", table_name="runner_audit_entries")
    op.drop_index("ix_runner_audit_entries_enterprise_id", table_name="runner_audit_entries")
    op.drop_table("runner_audit_entries")

    op.drop_index("ix_runner_challenges_state", table_name="runner_challenges")
    op.drop_index("ix_runner_challenges_device_id", table_name="runner_challenges")
    op.drop_index("ix_runner_challenges_enterprise_id", table_name="runner_challenges")
    op.drop_index("ix_runner_challenges_task_id", table_name="runner_challenges")
    op.drop_table("runner_challenges")

    op.drop_index("ix_runner_task_frames_task_id", table_name="runner_task_frames")
    op.drop_table("runner_task_frames")

    op.drop_index("ix_runner_tasks_device_state", table_name="runner_tasks")
    op.drop_index("ix_runner_tasks_challenge_id", table_name="runner_tasks")
    op.drop_index("ix_runner_tasks_state", table_name="runner_tasks")
    op.drop_index("ix_runner_tasks_device_id", table_name="runner_tasks")
    op.drop_index("ix_runner_tasks_enterprise_id", table_name="runner_tasks")
    op.drop_table("runner_tasks")

    op.drop_index("ix_runner_device_tokens_token_hash", table_name="runner_device_tokens")
    op.drop_index("ix_runner_device_tokens_device_id", table_name="runner_device_tokens")
    op.drop_table("runner_device_tokens")

    op.drop_index(
        "ix_runner_device_credentials_secret_hash", table_name="runner_device_credentials"
    )
    op.drop_index("ix_runner_device_credentials_device_id", table_name="runner_device_credentials")
    op.drop_table("runner_device_credentials")

    op.drop_index("ix_runner_devices_owner_user_id", table_name="runner_devices")
    op.drop_index("ix_runner_devices_enterprise_id", table_name="runner_devices")
    op.drop_table("runner_devices")
