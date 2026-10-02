"""b6_digital_employee_templates

Revision ID: b6a1c2d3e4f5
Revises: ea618fff2a58
Create Date: 2026-07-23 23:40:00.000000

B6: 数字员工能力模板与 Skill 链式编排
- 新建 agent_templates 表（AgentTemplate 模型）
- 新建 skill_templates 表（SkillTemplate 模型）
- skills 表加性追加 template_id / next_skill_id 字段（均可空，不破坏现有数据）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6a1c2d3e4f5'
down_revision: Union[str, None] = 'ea618fff2a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # B6: 创建技能模板表
    op.create_table('skill_templates',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('skill_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('output_schema', sa.JSON(), nullable=True),
        sa.Column('is_preset', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_skill_templates_code'),
    )
    with op.batch_alter_table('skill_templates', schema=None) as batch_op:
        batch_op.create_index('ix_skill_templates_code', ['code'], unique=True)
        batch_op.create_index('ix_skill_templates_is_preset', ['is_preset'], unique=False)

    # B6: 创建 Agent 模板表
    op.create_table('agent_templates',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('skill_ids', sa.JSON(), nullable=True),
        sa.Column('knowledge_structure', sa.JSON(), nullable=True),
        sa.Column('sample_dialogues', sa.JSON(), nullable=True),
        sa.Column('is_preset', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('enterprise_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['enterprise_id'], ['enterprises.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('agent_templates', schema=None) as batch_op:
        batch_op.create_index('ix_agent_templates_role', ['role'], unique=False)
        batch_op.create_index('ix_agent_templates_is_preset', ['is_preset'], unique=False)
        batch_op.create_index('ix_agent_templates_enterprise_id', ['enterprise_id'], unique=False)

    # B6: skills 表加性追加链式编排字段（均可空，不破坏现有数据）
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.add_column(sa.Column('template_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('next_skill_id', sa.String(length=36), nullable=True))
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.create_index('ix_skills_template_id', ['template_id'], unique=False)
        batch_op.create_index('ix_skills_next_skill_id', ['next_skill_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.drop_index('ix_skills_next_skill_id')
        batch_op.drop_index('ix_skills_template_id')
        batch_op.drop_column('next_skill_id')
        batch_op.drop_column('template_id')

    with op.batch_alter_table('agent_templates', schema=None) as batch_op:
        batch_op.drop_index('ix_agent_templates_enterprise_id')
        batch_op.drop_index('ix_agent_templates_is_preset')
        batch_op.drop_index('ix_agent_templates_role')
    op.drop_table('agent_templates')

    with op.batch_alter_table('skill_templates', schema=None) as batch_op:
        batch_op.drop_index('ix_skill_templates_is_preset')
        batch_op.drop_index('ix_skill_templates_code')
    op.drop_table('skill_templates')
