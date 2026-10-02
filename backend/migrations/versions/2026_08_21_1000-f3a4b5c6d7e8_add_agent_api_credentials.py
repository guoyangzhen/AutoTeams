"""add_agent_api_credentials

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-21 10:00:00.000000

为外部 Agent REST API 提供仅存哈希的企业级机器凭证、细粒度 scope、
Agent allow-list、到期与撤销能力。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_api_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), sa.ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("allowed_agent_ids", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_api_credentials_enterprise_id", "agent_api_credentials", ["enterprise_id"])
    op.create_index("ix_agent_api_credentials_owner_user_id", "agent_api_credentials", ["owner_user_id"])
    op.create_index("ix_agent_api_credentials_key_prefix", "agent_api_credentials", ["key_prefix"])
    op.create_index("ix_agent_api_credentials_key_hash", "agent_api_credentials", ["key_hash"], unique=True)
    op.create_index("ix_agent_api_credentials_expires_at", "agent_api_credentials", ["expires_at"])
    op.create_index(
        "ix_agent_api_credentials_enterprise_active",
        "agent_api_credentials",
        ["enterprise_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_table("agent_api_credentials")
