"""add_product_events

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-08-22 10:00:00.000000

为经营行动简报与首次导览保存企业级、最小化的产品行为事件。
该表不保存提示词、文件名、路径、业务正文、IP、User-Agent 或密钥；
安全关键操作仍使用 HMAC 审计链，不与高频体验事件混用。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "enterprise_id",
            sa.String(length=36),
            sa.ForeignKey("enterprises.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("event_name", sa.String(length=80), nullable=False),
        sa.Column("surface", sa.String(length=64), nullable=False, server_default="company_overview"),
        sa.Column("journey_state", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=False),
    )
    op.create_index("ix_product_events_enterprise_id", "product_events", ["enterprise_id"])
    op.create_index("ix_product_events_user_id", "product_events", ["user_id"])
    op.create_index("ix_product_events_session_id", "product_events", ["session_id"])
    op.create_index("ix_product_events_event_name", "product_events", ["event_name"])
    op.create_index("ix_product_events_journey_state", "product_events", ["journey_state"])
    op.create_index("ix_product_events_action", "product_events", ["action"])
    op.create_index(
        "ix_product_events_enterprise_created",
        "product_events",
        ["enterprise_id", "created_at"],
    )
    op.create_index(
        "ix_product_events_enterprise_name_created",
        "product_events",
        ["enterprise_id", "event_name", "created_at"],
    )
    op.create_index(
        "ix_product_events_user_created",
        "product_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_product_events_session_created",
        "product_events",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("product_events")
