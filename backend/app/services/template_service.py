"""数字员工能力模板服务。

职责：
1. 预置 4 类垂直岗位模板（客服/销售/HR/运营）+ 对应 SkillTemplate
2. list_templates(role?) 返回预置 + 企业私有模板
3. apply_agent_template 套用模板：扫描文件夹 → 预填 system_prompt → 实例化 SkillTemplate → 链式编排

设计原则：
- 预置模板内容中文专业（符合演示要求），技术数据准确，专业术语替代通用词
- 复用 build_agent_via_graph 完成文件扫描/向量化/默认 skills 创建
- 应用模板 = 后置覆写 system_prompt + 追加模板 skills（不删除默认 skills，避免破坏现有数据）
- 链式编排通过 next_skill_id 串联：上一步 output 作下一步 input
"""
import logging
import uuid
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_template import AgentTemplate
from app.models.skill import Skill
from app.models.skill_template import SkillTemplate
from app.services import templates

logger = logging.getLogger(__name__)


# ============================================================
# 预置 SkillTemplate（按 code 索引，供 AgentTemplate.skill_ids 引用）
# ============================================================
# 字段对齐 Skill.skill_type：text_generation/text_summarization/text_classification/
# data_extraction/translation/code_generation/custom
# 4.0 预置模板已外提为 JSON 资产（assets/legacy_roles.json），加载期做 schema 校验。
# 见 app/services/templates/__init__.py —— 本文件不再内联模板字面量。
PRESET_SKILL_TEMPLATES: list[dict] = templates.preset_skill_templates()
PRESET_AGENT_TEMPLATES: list[dict] = templates.preset_agent_templates()

# AutoTeams 5.0 §决策一：6 大中小企业行业模板（智能制造/电商零售/跨境出海/
# 专业咨询/教育培训/医疗健康），同样是 JSON 资产，见 app/services/templates/assets/。
INDUSTRY_SKILL_TEMPLATES: list[dict] = templates.industry_skill_templates()
INDUSTRY_AGENT_TEMPLATES: list[dict] = templates.industry_agent_templates()


# ============================================================
# 预置模板初始化（首次调用 list_templates / apply_agent_template 时幂等写入）
# ============================================================

async def ensure_preset_templates(db: AsyncSession) -> None:
    """幂等写入预置 AgentTemplate + SkillTemplate。

    - 通过 code/is_preset 判定是否已存在，避免重复写入
    - 单次事务内完成，由调用方决定是否 commit
    - 5.3.5: 通过缓存标志跳过冗余 SELECT 查询。预置模板在正常运营中不可变，
      首次 seed 后即长期有效，因此用 1 小时 TTL 的缓存标志避免每次 list_templates
      都做两次 SELECT。Redis 不可用时透明降级为原逻辑。
    """
    # 5.3.5: 缓存命中则直接跳过 SELECT，避免冗余查询
    from app.utils.cache import cache_get, cache_set
    if cache_get("presets:seeded") == "1":
        return

    # 1. SkillTemplate
    existing_skill_tpls_result = await db.execute(
        select(SkillTemplate).where(SkillTemplate.is_preset.is_(True))
    )
    existing_skill_codes = {
        st.code for st in existing_skill_tpls_result.scalars().all()
    }

    new_skill_tpls = [
        SkillTemplate(
            id=str(uuid.uuid4()),
            code=tpl["code"],
            name=tpl["name"],
            skill_type=tpl["skill_type"],
            description=tpl["description"],
            config=tpl["config"],
            output_schema=tpl["output_schema"],
            is_preset=True,
        )
        for tpl in PRESET_SKILL_TEMPLATES
        if tpl["code"] not in existing_skill_codes
    ]
    if new_skill_tpls:
        db.add_all(new_skill_tpls)
        await db.flush()
        logger.info(f"B6: 写入 {len(new_skill_tpls)} 个预置 SkillTemplate")

    # 2. AgentTemplate（在 SkillTemplate 写入后，确保 code 引用可解析）
    existing_agent_tpls_result = await db.execute(
        select(AgentTemplate).where(AgentTemplate.is_preset.is_(True))
    )
    existing_roles = {
        at.role for at in existing_agent_tpls_result.scalars().all()
    }

    new_agent_tpls = [
        AgentTemplate(
            id=str(uuid.uuid4()),
            role=tpl["role"],
            name=tpl["name"],
            description=tpl["description"],
            system_prompt=tpl["system_prompt"],
            skill_ids=tpl["skill_ids"],
            knowledge_structure=tpl["knowledge_structure"],
            sample_dialogues=tpl["sample_dialogues"],
            is_preset=True,
        )
        for tpl in PRESET_AGENT_TEMPLATES
        if tpl["role"] not in existing_roles
    ]
    if new_agent_tpls:
        db.add_all(new_agent_tpls)
        await db.flush()
        logger.info(f"B6: 写入 {len(new_agent_tpls)} 个预置 AgentTemplate")

    # 5.3.5: 已确认 seed 完成（无论本次是否真正写入），写缓存标志避免下次再查
    # TTL=3600s：1 小时后过期重新核查，应对极端情况下预置模板被外部删除
    cache_set("presets:seeded", "1", ttl=3600)


# ============================================================
# 查询：list_templates
# ============================================================

async def list_templates(
    db: AsyncSession,
    enterprise_id: Optional[str] = None,
    role: Optional[str] = None,
) -> list[AgentTemplate]:
    """返回预置 + 企业私有模板。

    - 预置模板（is_preset=True）：所有企业可见
    - 企业私有模板（is_preset=False, enterprise_id 匹配）：仅本企业可见
    - role 过滤：可选，按岗位角色筛选

    首次调用会触发 ensure_preset_templates 幂等写入。
    """
    # 幂等写入预置模板
    await ensure_preset_templates(db)
    await db.commit()

    # 构造查询：预置 OR 当前企业私有
    query = select(AgentTemplate).where(
        or_(
            AgentTemplate.is_preset.is_(True),
            AgentTemplate.enterprise_id == enterprise_id,
        )
    )
    if role:
        query = query.where(AgentTemplate.role == role)
    query = query.order_by(AgentTemplate.is_preset.desc(), AgentTemplate.created_at.asc())

    result = await db.execute(query)
    return list(result.scalars().all())


# ============================================================
# 创建：企业私有模板另存为
# ============================================================

# 允许的企业私有模板 role 白名单（与预置模板一致，保证岗位语义统一）
_ALLOWED_PRIVATE_ROLES = {
    "customer_service",
    "sales",
    "pre_sales",
    "after_sales",
    "hr",
    "ops",
    "finance",
    "medical",
    "education",
    "legal",
}


async def create_private_template(
    db: AsyncSession,
    enterprise_id: str,
    role: str,
    name: str,
    description: str,
    system_prompt: str,
    skill_ids: Optional[list[str]] = None,
    knowledge_structure: Optional[dict] = None,
    sample_dialogues: Optional[list[dict]] = None,
) -> AgentTemplate:
    """企业将自定义配置另存为私有模板。

    - 仅写入当前企业可见的私有模板（is_preset=False, enterprise_id=enterprise_id）
    - role 必须在白名单内，避免传入任意字符串造成展示混乱
    - system_prompt 不允许为空，保证模板可用性
    - 显式 commit（遵循 hard constraint：service 层写操作必须显式 commit）
    """
    if role not in _ALLOWED_PRIVATE_ROLES:
        raise ValueError(f"不支持的岗位角色: {role}，允许值: {sorted(_ALLOWED_PRIVATE_ROLES)}")
    if not name or not name.strip():
        raise ValueError("模板名称不能为空")
    if not system_prompt or not system_prompt.strip():
        raise ValueError("system_prompt 不能为空")

    template = AgentTemplate(
        id=str(uuid.uuid4()),
        role=role,
        name=name.strip(),
        description=(description or "").strip() or None,
        system_prompt=system_prompt.strip(),
        skill_ids=skill_ids or [],
        knowledge_structure=knowledge_structure or {},
        sample_dialogues=sample_dialogues or [],
        is_preset=False,
        enterprise_id=enterprise_id,
    )
    db.add(template)
    await db.flush()
    await db.commit()
    await db.refresh(template)

    logger.info(
        f"B6: 企业 {enterprise_id} 另存为私有模板: {template.id} (role={role}, name={name})"
    )
    return template


# ============================================================
# 应用模板：apply_agent_template
# ============================================================

async def apply_agent_template(
    db: AsyncSession,
    db_session_factory,
    template_id: str,
    enterprise_id: str,
    folder_path: str,
    name_override: Optional[str] = None,
    description_override: Optional[str] = None,
) -> dict:
    """套用模板创建 Agent。

    流程：
    1. 加载模板并校验访问权限（预置 / 企业私有）
    2. 调 build_agent_via_graph 完成文件扫描/向量化/默认 skills 创建
    3. 后置覆写 Agent.system_prompt 为模板的中文专业 prompt
    4. 按 template.skill_ids 实例化 SkillTemplate 为 Skill，链式串联 next_skill_id

    Args:
        db: 当前请求的 AsyncSession（用于读取模板 + 后置覆写）
        db_session_factory: async_session_factory，供 build_agent_via_graph 内部独立 session 使用
        template_id: AgentTemplate.id
        enterprise_id: 当前用户的企业 ID
        folder_path: 知识源文件夹路径
        name_override: 可选，覆盖模板默认的 Agent 名称
        description_override: 可选，覆盖模板默认的 Agent 描述

    Returns:
        {
            "agent_id": str,
            "template_id": str,
            "template_name": str,
            "system_prompt_applied": bool,
            "skills_created": int,
            "build_status": str,
            "build_messages": list[str],
        }
    """
    # 延迟导入，避免循环依赖
    from app.services.agent_graph import build_agent_via_graph

    # 1. 加载模板
    tpl_result = await db.execute(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    template = tpl_result.scalar_one_or_none()
    if not template:
        raise ValueError(f"模板不存在: {template_id}")

    # 2. 校验访问权限：预置模板放行；企业私有模板需 enterprise_id 匹配
    if not template.is_preset:
        if not template.enterprise_id or template.enterprise_id != enterprise_id:
            raise PermissionError("无权访问此企业私有模板")

    # 3. 调 build_agent_via_graph（使用独立 session 工厂，避免与当前 db 事务冲突）
    agent_name = name_override or template.name
    agent_description = description_override or template.description or ""
    build_result = await build_agent_via_graph(
        db_session_factory=db_session_factory,
        enterprise_id=enterprise_id,
        name=agent_name,
        description=agent_description,
        folder_path=folder_path,
        require_approval=False,
    )

    agent_id = build_result.get("agent_id")
    if not agent_id:
        raise RuntimeError(
            f"模板应用失败：build_agent_via_graph 未返回 agent_id。状态: {build_result.get('status')}"
        )

    # 4. 后置覆写 system_prompt 为模板的中文专业 prompt
    system_prompt_applied = False
    async with db_session_factory() as post_db:
        agent_result = await post_db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent:
            # 模板 system_prompt 含 {agent_name}/{enterprise_name}/{file_count}/{knowledge_count} 占位符
            # B6: 用 Agent 已统计的真实数据填充
            try:
                # 查询企业名
                from app.models.enterprise import Enterprise
                ent_result = await post_db.execute(
                    select(Enterprise).where(Enterprise.id == enterprise_id)
                )
                enterprise = ent_result.scalar_one_or_none()
                enterprise_name = enterprise.name if enterprise else "未知企业"

                agent.system_prompt = template.system_prompt.format(
                    agent_name=agent_name,
                    enterprise_name=enterprise_name,
                    file_count=agent.file_count,
                    knowledge_count=agent.knowledge_count,
                )
                system_prompt_applied = True
            except (KeyError, ValueError) as e:
                logger.warning(
                    f"模板 system_prompt 占位符填充失败，使用原文: {e}",
                    exc_info=True,
                )
                agent.system_prompt = template.system_prompt
                system_prompt_applied = True

        # 5. 按 template.skill_ids 实例化 SkillTemplate 为 Skill，链式串联
        skills_created = 0
        skill_codes = template.skill_ids or []
        if skill_codes:
            # 查询引用的 SkillTemplate
            skill_tpls_result = await post_db.execute(
                select(SkillTemplate).where(SkillTemplate.code.in_(skill_codes))
            )
            skill_tpls_by_code = {
                st.code: st for st in skill_tpls_result.scalars().all()
            }

            # 按 template.skill_ids 顺序实例化（保持链式顺序）
            created_skill_ids: list[str] = []
            for code in skill_codes:
                skill_tpl = skill_tpls_by_code.get(code)
                if not skill_tpl:
                    logger.warning(f"模板引用的 SkillTemplate 不存在: {code}，跳过")
                    continue

                new_skill_id = str(uuid.uuid4())
                skill = Skill(
                    id=new_skill_id,
                    agent_id=agent_id,
                    name=skill_tpl.name,
                    description=skill_tpl.description,
                    skill_type=skill_tpl.skill_type,
                    input_type="text",
                    output_type="text",
                    config=skill_tpl.config or {},
                    # B6: 标记来源模板，便于追溯
                    template_id=skill_tpl.code,
                    # 手动从模板实例化的 skill 视为 approved（已通过模板审核）
                    source="manual",
                    status="approved",
                )
                post_db.add(skill)
                created_skill_ids.append(new_skill_id)
                skills_created += 1

            # 设置链式编排：next_skill_id 指向数组中下一个 skill
            # 上一步 output 作为下一步 input，链尾为 None
            for i, skill_id in enumerate(created_skill_ids):
                if i + 1 < len(created_skill_ids):
                    # 重新查询刚创建的 skill 并设置 next_skill_id
                    skill_result = await post_db.execute(
                        select(Skill).where(Skill.id == skill_id)
                    )
                    skill_obj = skill_result.scalar_one_or_none()
                    if skill_obj:
                        skill_obj.next_skill_id = created_skill_ids[i + 1]

        await post_db.commit()

    logger.info(
        f"B6: 模板「{template.name}」应用完成，agent_id={agent_id}, "
        f"system_prompt_applied={system_prompt_applied}, skills_created={skills_created}"
    )

    return {
        "agent_id": agent_id,
        "template_id": template.id,
        "template_name": template.name,
        "system_prompt_applied": system_prompt_applied,
        "skills_created": skills_created,
        "build_status": build_result.get("status", "completed"),
        "build_messages": build_result.get("messages", []),
    }


# ============================================================
# 工具方法：仅查询 SkillTemplate（供 API 暴露技能模板列表）
# ============================================================

async def list_skill_templates(
    db: AsyncSession,
    enterprise_id: Optional[str] = None,
) -> list[SkillTemplate]:
    """返回预置 SkillTemplate 列表（当前仅支持预置，企业私有 SkillTemplate 暂未开放）。"""
    # 幂等写入预置模板（与 list_templates 共用）
    await ensure_preset_templates(db)
    await db.commit()

    result = await db.execute(
        select(SkillTemplate)
        .where(SkillTemplate.is_preset.is_(True))
        .order_by(SkillTemplate.created_at.asc())
    )
    return list(result.scalars().all())
