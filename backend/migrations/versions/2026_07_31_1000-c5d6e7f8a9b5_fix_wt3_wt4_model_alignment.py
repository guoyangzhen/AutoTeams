"""fix_wt3_wt4_model_alignment

Revision ID: c5d6e7f8a9b5
Revises: b4c5d6e7f8a4
Create Date: 2026-07-31 10:00:00.000000

WT6 修正迁移（第三批）：对齐 WT3/WT4 最终模型定义。

基于 check_drift.py 生成的偏差报告（38 项有意义偏差），本迁移处理
需通过迁移修正的项（加列 + nullable 收紧）。

修正项（5 类）：
1. interview_questions.created_at（WT4 模型有，迁移 a9b0c1d2f5e9 缺失）—— 加列
2. users.refresh_token_family_id（P2-T8b 模型有，迁移缺失）—— 加列
3. compilation_jobs.confidence/completeness nullable True→False（WT1 模型 NOT NULL）
4. runtime_versions.enterprise_id/event_type/updated_at nullable True→False
   （WT2 模型 NOT NULL，b3c4d5e6f7a3 加性迁移时放宽为 nullable）
5. users.role nullable True→False（模型 NOT NULL）

不在本迁移处理（模型侧声明缺失，非迁移问题）：
- FK ondelete 声明缺失（compilation_jobs/artifacts enterprise_id、
  enterprise_operating_models/profiles/knowledge_graphs enterprise_id、
  optimization_histories agent_id）—— DB 已有 CASCADE FK，模型未声明 ondelete，
  应由 WT 负责人在模型侧补声明，不应从 DB 删除 FK（破坏性）。
- UniqueConstraint 声明缺失（enterprise_profiles/knowledge_graphs 的
  (enterprise_id, version)、skill_templates.code）—— DB 已有约束，模型未声明。
- 索引名不一致（idx_* 系列）—— 功能等价，仅 autogenerate 噪声。
- JSON 类型噪声（modify_type JSON→JSON）—— SQLite 类型映射差异，已过滤。
- invitations.token 唯一索引 —— legacy 表，WT1 范围，不在 WT3/WT4 对齐范围。

遵循 spec.md §3.2 加性迁移优先 + 禁止修改已合并迁移（新建迁移修正）。
所有 nullable 收紧前先回填默认值，确保已有行不违反 NOT NULL 约束。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d6e7f8a9b5'
down_revision: Union[str, None] = 'b4c5d6e7f8a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 1. interview_questions: 补 created_at 列（WT4 模型 line 83） ===
    # 模型: Column(DateTime, nullable=False, default=utcnow)
    # 迁移 a9b0c1d2f5e9 创建表时遗漏此字段
    # SQLite 加 NOT NULL 列需 server_default 以回填已有行
    with op.batch_alter_table('interview_questions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'created_at',
                sa.DateTime(),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            )
        )

    # === 2. users: 补 refresh_token_family_id 列（P2-T8b） ===
    # 模型: Column(String(36), nullable=True)
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('refresh_token_family_id', sa.String(length=36), nullable=True)
        )

    # === 3. compilation_jobs: confidence/completeness nullable True→False ===
    # WT1 模型: Column(Float, nullable=False, default=0.0)
    # 迁移 a2b3c4d5e8f2 创建时设 nullable=True
    op.execute(
        "UPDATE compilation_jobs SET confidence = 0.0 WHERE confidence IS NULL"
    )
    op.execute(
        "UPDATE compilation_jobs SET completeness = 0.0 WHERE completeness IS NULL"
    )
    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.alter_column(
            'confidence',
            existing_type=sa.Float(),
            nullable=False,
        )
        batch_op.alter_column(
            'completeness',
            existing_type=sa.Float(),
            nullable=False,
        )

    # === 4. runtime_versions: enterprise_id/event_type/updated_at nullable True→False ===
    # WT2 模型: enterprise_id NOT NULL, event_type NOT NULL (default='save'),
    #           updated_at NOT NULL (TimestampMixin)
    # 迁移 b3c4d5e6f7a3 添加时设 nullable=True（加性迁移放宽，注释说明应用层保证非空）
    # 本迁移收紧为 NOT NULL，回填策略：
    # - enterprise_id: 从关联的 enterprise_runtimes 表获取
    # - event_type: 回填 'save'（与 server_default 一致）
    # - updated_at: 回填 created_at（同一时刻）
    op.execute(
        "UPDATE runtime_versions SET enterprise_id = ("
        "  SELECT rt.enterprise_id FROM enterprise_runtimes rt "
        "  WHERE rt.id = runtime_versions.runtime_id"
        ") WHERE enterprise_id IS NULL"
    )
    op.execute(
        "UPDATE runtime_versions SET event_type = 'save' WHERE event_type IS NULL"
    )
    op.execute(
        "UPDATE runtime_versions SET updated_at = created_at WHERE updated_at IS NULL"
    )
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        batch_op.alter_column(
            'enterprise_id',
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch_op.alter_column(
            'event_type',
            existing_type=sa.String(length=32),
            nullable=False,
        )
        batch_op.alter_column(
            'updated_at',
            existing_type=sa.DateTime(),
            nullable=False,
        )

    # === 5. users.role: nullable True→False ===
    # 模型: Column(String(32), nullable=False, default="member")
    # CheckConstraint 'uq_user_role_valid' 已在 e5b2c3d4f5a6 迁移中创建
    op.execute(
        "UPDATE users SET role = 'member' WHERE role IS NULL"
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column(
            'role',
            existing_type=sa.String(length=32),
            nullable=False,
        )


def downgrade() -> None:
    # === 5. users.role: 还原 nullable=True ===
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column(
            'role',
            existing_type=sa.String(length=32),
            nullable=True,
        )

    # === 4. runtime_versions: 还原 nullable=True ===
    with op.batch_alter_table('runtime_versions', schema=None) as batch_op:
        batch_op.alter_column(
            'updated_at',
            existing_type=sa.DateTime(),
            nullable=True,
        )
        batch_op.alter_column(
            'event_type',
            existing_type=sa.String(length=32),
            nullable=True,
        )
        batch_op.alter_column(
            'enterprise_id',
            existing_type=sa.String(length=36),
            nullable=True,
        )

    # === 3. compilation_jobs: 还原 nullable=True ===
    with op.batch_alter_table('compilation_jobs', schema=None) as batch_op:
        batch_op.alter_column(
            'completeness',
            existing_type=sa.Float(),
            nullable=True,
        )
        batch_op.alter_column(
            'confidence',
            existing_type=sa.Float(),
            nullable=True,
        )

    # === 2. users: 删 refresh_token_family_id 列 ===
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('refresh_token_family_id')

    # === 1. interview_questions: 删 created_at 列 ===
    with op.batch_alter_table('interview_questions', schema=None) as batch_op:
        batch_op.drop_column('created_at')
