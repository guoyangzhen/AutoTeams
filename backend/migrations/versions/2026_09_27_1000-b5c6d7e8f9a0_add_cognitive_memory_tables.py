"""add_cognitive_memory_tables

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-27 10:00:00.000000

AutoTeams 5.0 战役 2：分层长程认知记忆中枢。
- episodic_traces：情景因果链轨迹（Layer 1），caused_by_event_id 自引用形成可多跳回放的因果图。
- procedural_genes：自愈规程经验基因卡（Layer 3），由高绩效轨迹蒸馏而来。
Layer 2 语义实体记忆复用既有 knowledge_graphs 表，此迁移不重复建模。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "episodic_traces",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "enterprise_id",
            sa.String(length=36),
            sa.ForeignKey("enterprises.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("badge", sa.String(length=64), nullable=True),
        sa.Column("caused_by_event_id", sa.String(length=36), nullable=True),
        sa.Column("task_summary", sa.Text(), nullable=False),
        sa.Column("causal_chain_json", sa.JSON(), nullable=False),
        sa.Column("reflection_notes", sa.Text(), nullable=True),
        sa.Column("outcome_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("context_tags", sa.JSON(), nullable=False),
        sa.Column("steps_detail", sa.JSON(), nullable=True),
    )
    # AUD-11: SQLite 不支持 ALTER ... ADD CONSTRAINT。batch_alter_table 在 SQLite 上
    # 改写成"建临时表 → 拷贝 → 改名"，在 PostgreSQL 上退化为原生 DDL。
    with op.batch_alter_table("episodic_traces") as batch_op:
        batch_op.create_foreign_key(
            "fk_episodic_traces_caused_by",
            "episodic_traces",
            ["caused_by_event_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_episodic_traces_enterprise_id", "episodic_traces", ["enterprise_id"])
    op.create_index("ix_episodic_traces_badge", "episodic_traces", ["badge"])
    op.create_index("ix_episodic_traces_caused_by_event_id", "episodic_traces", ["caused_by_event_id"])
    op.create_index(
        "ix_episodic_traces_enterprise_created", "episodic_traces", ["enterprise_id", "created_at"]
    )
    op.create_index(
        "ix_episodic_traces_enterprise_badge", "episodic_traces", ["enterprise_id", "badge"]
    )

    op.create_table(
        "procedural_genes",
        sa.Column("gene_id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "enterprise_id",
            sa.String(length=36),
            sa.ForeignKey("enterprises.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("badge", sa.String(length=64), nullable=True),
        sa.Column("trigger_pattern", sa.Text(), nullable=False),
        sa.Column("successful_sop_patch", sa.Text(), nullable=False),
        sa.Column("confidence_rating", sa.Float(), nullable=False, server_default="0"),
        sa.Column("support_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_trace_ids", sa.JSON(), nullable=False),
    )
    op.create_index("ix_procedural_genes_enterprise_id", "procedural_genes", ["enterprise_id"])
    op.create_index("ix_procedural_genes_badge", "procedural_genes", ["badge"])
    op.create_index(
        "ix_procedural_genes_enterprise_badge", "procedural_genes", ["enterprise_id", "badge"]
    )


def downgrade() -> None:
    op.drop_index("ix_procedural_genes_enterprise_badge", table_name="procedural_genes")
    op.drop_index("ix_procedural_genes_badge", table_name="procedural_genes")
    op.drop_index("ix_procedural_genes_enterprise_id", table_name="procedural_genes")
    op.drop_table("procedural_genes")

    op.drop_index("ix_episodic_traces_enterprise_badge", table_name="episodic_traces")
    op.drop_index("ix_episodic_traces_enterprise_created", table_name="episodic_traces")
    op.drop_index("ix_episodic_traces_caused_by_event_id", table_name="episodic_traces")
    op.drop_index("ix_episodic_traces_enterprise_id", table_name="episodic_traces")
    # batch_alter_table 的降级同样需要镜像写法
    with op.batch_alter_table("episodic_traces") as batch_op:
        batch_op.drop_constraint("fk_episodic_traces_caused_by", type_="foreignkey")
    op.drop_table("episodic_traces")
