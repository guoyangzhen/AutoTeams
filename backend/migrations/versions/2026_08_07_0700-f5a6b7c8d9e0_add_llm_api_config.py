"""add_llm_api_config

Revision ID: f5a6b7c8d9e0
Revises: e1f2a3b4c5d6
Create Date: 2026-08-07 07:00:00.000000

企业级模型 API 配置表。API Key 列仅存 Fernet 密文，绝不落明文。
依据：设置页「模型 API 配置」需求（OpenAI 兼容 / Anthropic，加密持久化）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_api_configs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('enterprise_id', sa.String(length=36), nullable=False),
        sa.Column('openai_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('openai_api_base', sa.String(), nullable=True),
        sa.Column('openai_api_key', sa.String(), nullable=True),
        sa.Column('openai_model', sa.String(), nullable=True),
        sa.Column('anthropic_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('anthropic_api_base', sa.String(), nullable=True),
        sa.Column('anthropic_api_key', sa.String(), nullable=True),
        sa.Column('anthropic_model', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('llm_api_configs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_llm_api_configs_enterprise_id'), ['enterprise_id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('llm_api_configs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_llm_api_configs_enterprise_id'))
    op.drop_table('llm_api_configs')