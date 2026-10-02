"""channel_bind_tokens

Revision ID: f4a5b6c7d8e0
Revises: e3f4a5b6c7d9
Create Date: 2026-09-28 20:50:00.000000

AUD-08：渠道身份绑定码必须有服务端台账。

历史实现里 `POST /connectors/bind/generate` 只返回一个随机 6 位串，不落库；
`/绑定 <6位码>` 分支只检查长度是否等于 6，就直接把 `is_bound=true` 写上，
而 `internal_user_id` 仍然是 NULL —— 外部消息因此可以伪造"已绑定"。

本迁移新增绑定码台账：
- 只存 token 的 SHA-256 摘要，不存明文；
- 记录发起绑定的内部用户、企业与可用渠道；
- 短 TTL + 一次性消费（`consumed_at`）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e0"
down_revision: Union[str, None] = "e3f4a5b6c7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_bind_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("internal_user_id", sa.String(length=36), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_identity_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["internal_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_bind_tokens_enterprise_id", "channel_bind_tokens", ["enterprise_id"])
    op.create_index("ix_channel_bind_tokens_internal_user_id", "channel_bind_tokens", ["internal_user_id"])
    op.create_index("ix_channel_bind_tokens_token_hash", "channel_bind_tokens", ["token_hash"], unique=True)
    op.create_index("ix_channel_bind_tokens_expires_at", "channel_bind_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_channel_bind_tokens_expires_at", table_name="channel_bind_tokens")
    op.drop_index("ix_channel_bind_tokens_token_hash", table_name="channel_bind_tokens")
    op.drop_index("ix_channel_bind_tokens_internal_user_id", table_name="channel_bind_tokens")
    op.drop_index("ix_channel_bind_tokens_enterprise_id", table_name="channel_bind_tokens")
    op.drop_table("channel_bind_tokens")
