"""add_interview_tables

Revision ID: a9b0c1d2f5e9
Revises: a8b9c0d1f4e8
Create Date: 2026-07-29 03:16:00.000000

WT4 访谈表：交互式企业访谈会话 + 问题记录。
依据：重构方案_v3.md §7.7（WT4 数据模型）+ PRD §5.7。
- interview_sessions: 访谈会话（7 大类问题库）
- interview_questions: 问题与回答记录（含受影响运行模型字段）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9b0c1d2f5e9'
down_revision: Union[str, None] = 'a8b9c0d1f4e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 访谈会话
    op.create_table(
        'interview_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        # 状态：active / completed
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('answered_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('interview_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_interview_sessions_enterprise_id'), ['enterprise_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_interview_sessions_user_id'), ['user_id'], unique=False)

    # 访谈问题与回答
    op.create_table(
        'interview_questions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        # 问题分类（7 大类）：sales / customer_service / procurement / finance / hr / data / kpi
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('expected_output', sa.Text(), nullable=True),
        # 受影响的运行模型字段
        sa.Column('affected_field', sa.String(length=128), nullable=True),
        # 优先级：P0 / P1 / P2
        sa.Column('priority', sa.String(length=4), nullable=False, server_default='P1'),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('answered_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('interview_questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_interview_questions_session_id'), ['session_id'], unique=False)
        batch_op.create_index('idx_interview_questions_category', ['category'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('interview_questions', schema=None) as batch_op:
        batch_op.drop_index('idx_interview_questions_category')
        batch_op.drop_index(batch_op.f('ix_interview_questions_session_id'))
    op.drop_table('interview_questions')

    with op.batch_alter_table('interview_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_interview_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_interview_sessions_enterprise_id'))
    op.drop_table('interview_sessions')
