"""add_audit_chain_state

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-21 09:00:00.000000

将审计链尾指针持久化为可行锁的全局单例，避免多 API/Worker 进程使用
各自的内存缓存并发追加审计日志时形成分叉链。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GENESIS_HASH = "0" * 64


def upgrade() -> None:
    op.create_table(
        "audit_chain_state",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_signature", sa.String(length=128), nullable=False),
    )
    # 固定单例，应用侧会使用 SELECT ... FOR UPDATE 锁住该行并事务性推进链尾。
    # 对已有库必须从最后一条已签名日志续接；错误地从创世哈希初始化会让升级后的
    # 下一条日志与历史链断开。
    op.execute(
        "INSERT INTO audit_chain_state (id, created_at, updated_at, last_signature) "
        "SELECT 'global', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
        "COALESCE((SELECT candidate.signature FROM audit_logs AS candidate "
        "WHERE candidate.signature IS NOT NULL "
        "AND candidate.signature NOT IN (SELECT prev_hash FROM audit_logs "
        "WHERE prev_hash IS NOT NULL) ORDER BY candidate.created_at DESC LIMIT 1), '"
        + GENESIS_HASH
        + "')"
    )


def downgrade() -> None:
    op.drop_table("audit_chain_state")
