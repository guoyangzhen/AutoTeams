"""add_missing_business_tables

Revision ID: c1d2e3f4a5b7
Revises: b5c6d7e8f9a0
Create Date: 2026-09-28 20:10:55.984388

AUD-05：11 张已接入业务但从未建表的 ORM 表在此补齐迁移，外加预留的
`background_jobs`（BackgroundJob 模型已存在，尚未接入消费链路，但必须先有表，
否则 ORM 与迁移元数据比对永远不一致）。

- channel_accounts / channel_identities ：多渠道接入账号与外部身份绑定
- counterfactual_diffs / shadow_evaluation_sessions：反事实评估
- flow_cards                        ：SOP 规程卡
- workgroup_teams / matrix_tasks / task_selection_bids / shared_blackboard_entries：
                                     ：矩阵团队协同
- strike_teams                      ：动态敏捷特遣队
- workforce_profiles                ：数字员工岗位档案

结构由 `alembic revision --autogenerate` 对照 ORM 元数据生成后手工裁剪，
只保留建表与索引，不包含任何 alter_column / drop_index 噪音。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b7"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "strike_teams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("mission_statement", sa.Text(), nullable=False),
        sa.Column("initiator_badge", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("allocated_compute_budget", sa.Float(), nullable=False),
        sa.Column("total_hp_stake", sa.Float(), nullable=False),
        sa.Column("shared_blackboard_id", sa.String(length=36), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column("subtask_graph", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strike_teams_enterprise_id", "strike_teams", ["enterprise_id"])
    op.create_index("ix_strike_teams_status", "strike_teams", ["status"])

    op.create_table(
        "channel_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("encrypted_credentials", sa.JSON(), nullable=False),
        sa.Column("mounted_profile_ids", sa.JSON(), nullable=False),
        sa.Column("default_profile_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_accounts_channel_type", "channel_accounts", ["channel_type"])
    op.create_index("ix_channel_accounts_enterprise_id", "channel_accounts", ["enterprise_id"])

    op.create_table(
        "flow_cards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("flow_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("flow_data", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flow_cards_enterprise_id", "flow_cards", ["enterprise_id"])
    op.create_index("ix_flow_cards_flow_id", "flow_cards", ["flow_id"])

    op.create_table(
        "shadow_evaluation_sessions",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("employee_badge", sa.String(length=64), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column("human_action_snapshot", sa.Text(), nullable=False),
        sa.Column("agent_proposal_snapshot", sa.Text(), nullable=False),
        sa.Column("human_duration_seconds", sa.Float(), nullable=False),
        sa.Column("agent_duration_seconds", sa.Float(), nullable=False),
        sa.Column("human_cost_yuan", sa.Float(), nullable=False),
        sa.Column("agent_cost_yuan", sa.Float(), nullable=False),
        sa.Column("semantic_alignment_score", sa.Float(), nullable=False),
        sa.Column("time_saving_seconds", sa.Float(), nullable=False),
        sa.Column("cost_delta_yuan", sa.Float(), nullable=False),
        sa.Column("expected_net_benefit_yuan", sa.Float(), nullable=False),
        sa.Column("guardrail_breach_count", sa.Integer(), nullable=False),
        sa.Column("is_qualified", sa.Boolean(), nullable=False),
        sa.Column("consecutive_pass_streak", sa.Integer(), nullable=False),
        sa.Column("is_auto_promoted", sa.Boolean(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        "ix_shadow_evaluation_sessions_consecutive_pass_streak",
        "shadow_evaluation_sessions",
        ["consecutive_pass_streak"],
    )
    op.create_index(
        "ix_shadow_evaluation_sessions_employee_badge",
        "shadow_evaluation_sessions",
        ["employee_badge"],
    )
    op.create_index(
        "ix_shadow_evaluation_sessions_enterprise_id",
        "shadow_evaluation_sessions",
        ["enterprise_id"],
    )
    op.create_index(
        "ix_shadow_evaluation_sessions_is_qualified",
        "shadow_evaluation_sessions",
        ["is_qualified"],
    )

    op.create_table(
        "workgroup_teams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("leader_profile_id", sa.String(length=36), nullable=False),
        sa.Column("member_profile_ids", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workgroup_teams_enterprise_id", "workgroup_teams", ["enterprise_id"])

    op.create_table(
        "background_jobs",
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("progress", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_log", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_background_jobs_enterprise_id", "background_jobs", ["enterprise_id"])
    op.create_index("ix_background_jobs_idempotency_key", "background_jobs", ["idempotency_key"])
    op.create_index("ix_background_jobs_job_type", "background_jobs", ["job_type"])
    op.create_index("ix_background_jobs_lease_owner", "background_jobs", ["lease_owner"])
    op.create_index("ix_background_jobs_lease_until", "background_jobs", ["lease_until"])
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"])
    op.create_index("ix_background_jobs_user_id", "background_jobs", ["user_id"])

    op.create_table(
        "channel_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("external_user_id", sa.String(length=128), nullable=False),
        sa.Column("external_user_name", sa.String(length=128), nullable=True),
        sa.Column("internal_user_id", sa.String(length=36), nullable=True),
        sa.Column("bind_token", sa.String(length=32), nullable=True),
        sa.Column("bind_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_bound", sa.Boolean(), nullable=False),
        sa.Column("active_profile_id", sa.String(length=36), nullable=True),
        sa.Column("sop_window_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_flow_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["internal_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_channel_identities_bind_token", "channel_identities", ["bind_token"])
    op.create_index("ix_channel_identities_channel_type", "channel_identities", ["channel_type"])
    op.create_index("ix_channel_identities_enterprise_id", "channel_identities", ["enterprise_id"])
    op.create_index(
        "ix_channel_identities_external_user_id", "channel_identities", ["external_user_id"]
    )
    op.create_index(
        "ix_channel_identities_internal_user_id", "channel_identities", ["internal_user_id"]
    )

    op.create_table(
        "counterfactual_diffs",
        sa.Column("diff_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("human_value", sa.Text(), nullable=False),
        sa.Column("agent_value", sa.Text(), nullable=False),
        sa.Column("delta_score", sa.Float(), nullable=False),
        sa.Column("assessment", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["shadow_evaluation_sessions.session_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("diff_id"),
    )
    op.create_index("ix_counterfactual_diffs_session_id", "counterfactual_diffs", ["session_id"])

    op.create_table(
        "matrix_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=False),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("suggested_profile_id", sa.String(length=36), nullable=True),
        sa.Column("assignee_profile_id", sa.String(length=36), nullable=True),
        sa.Column("deliverable_report", sa.JSON(), nullable=True),
        sa.Column("review_feedback", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["workgroup_teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_matrix_tasks_parent_task_id", "matrix_tasks", ["parent_task_id"])
    op.create_index("ix_matrix_tasks_team_id", "matrix_tasks", ["team_id"])

    op.create_table(
        "shared_blackboard_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_profile_id", sa.String(length=36), nullable=False),
        sa.Column("source_task_id", sa.String(length=36), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["workgroup_teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shared_blackboard_entries_team_id", "shared_blackboard_entries", ["team_id"])
    op.create_index("ix_shared_blackboard_entries_topic", "shared_blackboard_entries", ["topic"])

    op.create_table(
        "workforce_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("enterprise_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("employee_badge", sa.String(length=48), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("job_title", sa.String(length=64), nullable=False),
        sa.Column("department", sa.String(length=64), nullable=False),
        sa.Column("duty_boundaries", sa.JSON(), nullable=False),
        sa.Column("tone_style", sa.String(length=64), nullable=False),
        sa.Column("authorized_flows", sa.JSON(), nullable=False),
        sa.Column("accessible_knowledge_buckets", sa.JSON(), nullable=False),
        sa.Column("authorized_tools", sa.JSON(), nullable=False),
        sa.Column("employment_status", sa.String(length=32), nullable=False),
        sa.Column("performance_score", sa.Float(), nullable=False),
        sa.Column("avatar_url", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workforce_profiles_agent_id", "workforce_profiles", ["agent_id"])
    op.create_index(
        "ix_workforce_profiles_employee_badge", "workforce_profiles", ["employee_badge"], unique=True
    )
    op.create_index("ix_workforce_profiles_enterprise_id", "workforce_profiles", ["enterprise_id"])

    op.create_table(
        "task_selection_bids",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_profile_id", sa.String(length=36), nullable=False),
        sa.Column("bid_round", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("score_rationale", sa.Text(), nullable=True),
        sa.Column("current_hp", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["matrix_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_selection_bids_candidate_profile_id", "task_selection_bids", ["candidate_profile_id"]
    )
    op.create_index("ix_task_selection_bids_task_id", "task_selection_bids", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_selection_bids_task_id", table_name="task_selection_bids")
    op.drop_index("ix_task_selection_bids_candidate_profile_id", table_name="task_selection_bids")
    op.drop_table("task_selection_bids")

    op.drop_index("ix_workforce_profiles_enterprise_id", table_name="workforce_profiles")
    op.drop_index("ix_workforce_profiles_employee_badge", table_name="workforce_profiles")
    op.drop_index("ix_workforce_profiles_agent_id", table_name="workforce_profiles")
    op.drop_table("workforce_profiles")

    op.drop_index("ix_shared_blackboard_entries_topic", table_name="shared_blackboard_entries")
    op.drop_index("ix_shared_blackboard_entries_team_id", table_name="shared_blackboard_entries")
    op.drop_table("shared_blackboard_entries")

    op.drop_index("ix_matrix_tasks_team_id", table_name="matrix_tasks")
    op.drop_index("ix_matrix_tasks_parent_task_id", table_name="matrix_tasks")
    op.drop_table("matrix_tasks")

    op.drop_index("ix_counterfactual_diffs_session_id", table_name="counterfactual_diffs")
    op.drop_table("counterfactual_diffs")

    op.drop_index("ix_channel_identities_internal_user_id", table_name="channel_identities")
    op.drop_index("ix_channel_identities_external_user_id", table_name="channel_identities")
    op.drop_index("ix_channel_identities_enterprise_id", table_name="channel_identities")
    op.drop_index("ix_channel_identities_channel_type", table_name="channel_identities")
    op.drop_index("ix_channel_identities_bind_token", table_name="channel_identities")
    op.drop_table("channel_identities")

    for index_name in (
        "ix_background_jobs_user_id",
        "ix_background_jobs_status",
        "ix_background_jobs_lease_until",
        "ix_background_jobs_lease_owner",
        "ix_background_jobs_job_type",
        "ix_background_jobs_idempotency_key",
        "ix_background_jobs_enterprise_id",
    ):
        op.drop_index(index_name, table_name="background_jobs")
    op.drop_table("background_jobs")

    op.drop_index("ix_workgroup_teams_enterprise_id", table_name="workgroup_teams")
    op.drop_table("workgroup_teams")

    op.drop_index("ix_shadow_evaluation_sessions_is_qualified", table_name="shadow_evaluation_sessions")
    op.drop_index(
        "ix_shadow_evaluation_sessions_enterprise_id", table_name="shadow_evaluation_sessions"
    )
    op.drop_index(
        "ix_shadow_evaluation_sessions_employee_badge", table_name="shadow_evaluation_sessions"
    )
    op.drop_index(
        "ix_shadow_evaluation_sessions_consecutive_pass_streak",
        table_name="shadow_evaluation_sessions",
    )
    op.drop_table("shadow_evaluation_sessions")

    op.drop_index("ix_flow_cards_flow_id", table_name="flow_cards")
    op.drop_index("ix_flow_cards_enterprise_id", table_name="flow_cards")
    op.drop_table("flow_cards")

    op.drop_index("ix_channel_accounts_enterprise_id", table_name="channel_accounts")
    op.drop_index("ix_channel_accounts_channel_type", table_name="channel_accounts")
    op.drop_table("channel_accounts")

    op.drop_index("ix_strike_teams_status", table_name="strike_teams")
    op.drop_index("ix_strike_teams_enterprise_id", table_name="strike_teams")
    op.drop_table("strike_teams")
