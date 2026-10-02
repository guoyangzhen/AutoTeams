"""B6: 数字员工能力模板与 Skill 链式编排测试。

覆盖：
1. test_list_templates — 预置模板列表 + role 过滤
2. test_apply_template_creates_agent — 套用模板：覆写 system_prompt + 链式编排技能
3. test_skill_chain_execution — 链式执行：next_skill_id 串联 + 聚合输出
4. test_low_risk_auto_approve — 低风险自动放行：search 类型 + 双重筛查通过
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent import Agent
from app.models.agent_template import AgentTemplate
from app.models.enterprise import Enterprise
from app.models.skill import Skill
from app.models.skill_execution import SkillExecution
from app.models.skill_template import SkillTemplate
from app.models.user import User
from app.services.skill_executor import skill_executor
from app.services.skill_screener import SkillScreener
from app.services.template_service import (
    PRESET_AGENT_TEMPLATES,
    PRESET_SKILL_TEMPLATES,
    apply_agent_template,
    create_private_template,
    list_templates,
)


# ============================================================
# 公共 fixture：创建企业 / Agent / 用户
# ============================================================

async def _create_enterprise_agent_user(db_session, name_prefix="测试"):
    """创建企业、Agent、用户并提交，返回 (enterprise, agent, user)。"""
    enterprise = Enterprise(name=f"{name_prefix}企业")
    db_session.add(enterprise)
    await db_session.flush()

    agent = Agent(
        enterprise_id=enterprise.id,
        name=f"{name_prefix}助手",
        description="",
        file_count=12,
        knowledge_count=340,
    )
    db_session.add(agent)
    await db_session.flush()

    user = User(
        email=f"{name_prefix}@test.com",
        password_hash="hash",
        name=name_prefix,
        enterprise_id=enterprise.id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(enterprise)
    await db_session.refresh(agent)
    await db_session.refresh(user)
    return enterprise, agent, user


def _make_factory(test_engine):
    """从 test_engine 构造 async_session_factory（供 apply_agent_template 使用）。"""
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


# ============================================================
# 测试 1：list_templates
# ============================================================

class TestListTemplates:
    """预置模板列表查询。"""

    @pytest.mark.asyncio
    async def test_list_templates_returns_all_presets(self, db_session):
        """无 role 过滤时返回全部 10 类预置模板（含 WT3 扩展的售前/售后）。"""
        templates = await list_templates(db_session, enterprise_id=None)

        assert len(templates) == 10
        roles = {t.role for t in templates}
        assert roles == {"customer_service", "sales", "pre_sales", "after_sales", "hr", "ops", "finance", "medical", "education", "legal"}
        # 全部为预置模板
        assert all(t.is_preset for t in templates)
        # 每个模板关联 4 个 SkillTemplate code
        assert all(len(t.skill_ids) == 4 for t in templates)

    @pytest.mark.asyncio
    async def test_list_templates_filter_by_role(self, db_session):
        """role 过滤只返回对应岗位模板。"""
        templates = await list_templates(
            db_session, enterprise_id=None, role="customer_service"
        )
        assert len(templates) == 1
        assert templates[0].role == "customer_service"
        assert templates[0].name == "客服数字员工"

    @pytest.mark.asyncio
    async def test_list_templates_idempotent(self, db_session):
        """多次调用 ensure_preset_templates 不会重复写入。"""
        await list_templates(db_session)
        first_count = (
            await db_session.execute(
                select(AgentTemplate).where(AgentTemplate.is_preset.is_(True))
            )
        ).scalars().all()
        await list_templates(db_session)
        second_count = (
            await db_session.execute(
                select(AgentTemplate).where(AgentTemplate.is_preset.is_(True))
            )
        ).scalars().all()
        assert len(first_count) == len(second_count) == 10

    @pytest.mark.asyncio
    async def test_list_templates_includes_enterprise_private(self, db_session):
        """企业私有模板与预置模板同时返回。"""
        enterprise, _, _ = await _create_enterprise_agent_user(db_session, "私企")

        # 写入一条企业私有模板
        private_tpl = AgentTemplate(
            role="customer_service",
            name="私企客服模板",
            description="企业自定义",
            system_prompt="私有 prompt",
            skill_ids=["cs_faq_match"],
            is_preset=False,
            enterprise_id=enterprise.id,
        )
        db_session.add(private_tpl)
        await db_session.commit()

        templates = await list_templates(db_session, enterprise_id=enterprise.id)
        # 10 预置（含 WT3 售前/售后）+ 1 私有
        assert len(templates) == 11
        private = [t for t in templates if not t.is_preset]
        assert len(private) == 1
        assert private[0].enterprise_id == enterprise.id


# ============================================================
# 测试 1b：create_private_template（企业私有模板「另存为」）
# ============================================================

class TestCreatePrivateTemplate:
    """企业私有模板「另存为」功能。"""

    @pytest.mark.asyncio
    async def test_create_private_template_success(self, db_session):
        """企业用户可将自定义配置另存为私有模板。"""
        enterprise, _, _ = await _create_enterprise_agent_user(db_session, "私有模板")

        template = await create_private_template(
            db=db_session,
            enterprise_id=enterprise.id,
            role="customer_service",
            name="我司专属客服模板",
            description="基于客服模板增加话务合规要求",
            system_prompt="你是「{agent_name}」，必须遵守我司话务合规规范。",
            skill_ids=["cs_ticket_classification", "cs_faq_match"],
            knowledge_structure={"categories": ["产品手册", "合规手册"]},
            sample_dialogues=[
                {"user": "你好", "assistant": "您好，请问有什么可以帮您？"}
            ],
        )

        # 断言字段写入正确
        assert template.id is not None
        assert template.role == "customer_service"
        assert template.name == "我司专属客服模板"
        assert template.is_preset is False
        assert template.enterprise_id == enterprise.id
        assert "话务合规" in template.system_prompt
        assert template.skill_ids == ["cs_ticket_classification", "cs_faq_match"]

        # 断言模板可见性：仅当前企业可见
        visible = await list_templates(db_session, enterprise_id=enterprise.id)
        private = [t for t in visible if not t.is_preset and t.id == template.id]
        assert len(private) == 1

        # 其他企业不可见
        other_ent = Enterprise(name="其他企业")
        db_session.add(other_ent)
        await db_session.commit()
        other_visible = await list_templates(db_session, enterprise_id=other_ent.id)
        assert all(t.id != template.id for t in other_visible), "私有模板不应被其他企业看到"

    @pytest.mark.asyncio
    async def test_create_private_template_rejects_invalid_role(self, db_session):
        """role 不在白名单内时拒绝创建。"""
        enterprise, _, _ = await _create_enterprise_agent_user(db_session, "非法角色")

        with pytest.raises(ValueError, match="不支持的岗位角色"):
            await create_private_template(
                db=db_session,
                enterprise_id=enterprise.id,
                role="invalid_role",
                name="非法模板",
                description="",
                system_prompt="测试",
            )

    @pytest.mark.asyncio
    async def test_create_private_template_rejects_empty_prompt(self, db_session):
        """system_prompt 为空时拒绝创建。"""
        enterprise, _, _ = await _create_enterprise_agent_user(db_session, "空 Prompt")

        with pytest.raises(ValueError, match="system_prompt"):
            await create_private_template(
                db=db_session,
                enterprise_id=enterprise.id,
                role="hr",
                name="空 Prompt 模板",
                description="",
                system_prompt="   ",  # 仅空白字符
            )


# ============================================================
# 测试 2：apply_agent_template
# ============================================================

class TestApplyTemplate:
    """套用模板创建 Agent。"""

    @pytest.mark.asyncio
    async def test_apply_template_creates_agent(
        self, db_session, test_engine
    ):
        """套用预置客服模板：覆写 system_prompt + 实例化链式技能。"""
        enterprise, agent, _ = await _create_enterprise_agent_user(db_session, "模板")
        factory = _make_factory(test_engine)

        # 写入预置 SkillTemplate + AgentTemplate（模拟 ensure_preset_templates 已执行）
        skill_tpls = [
            SkillTemplate(
                code=tpl["code"],
                name=tpl["name"],
                skill_type=tpl["skill_type"],
                description=tpl["description"],
                config=tpl["config"],
                output_schema=tpl["output_schema"],
                is_preset=True,
            )
            for tpl in PRESET_SKILL_TEMPLATES[:4]  # 仅客服 4 个
        ]
        db_session.add_all(skill_tpls)

        agent_tpl = AgentTemplate(
            role="customer_service",
            name="客服数字员工",
            description="测试模板",
            system_prompt=(
                "你是「{agent_name}」，隶属于「{enterprise_name}」。"
                "知识库：{file_count} 文件，{knowledge_count} 片段。"
            ),
            skill_ids=["cs_ticket_classification", "cs_faq_match",
                       "cs_sentiment_analysis", "cs_escalation"],
            is_preset=True,
        )
        db_session.add(agent_tpl)
        await db_session.commit()

        # mock build_agent_via_graph 返回预创建的 agent_id
        mock_build = AsyncMock(return_value={
            "agent_id": agent.id,
            "status": "completed",
            "messages": ["扫描完成", "向量化完成"],
        })

        with patch(
            "app.services.agent_graph.build_agent_via_graph", mock_build
        ):
            result = await apply_agent_template(
                db=db_session,
                db_session_factory=factory,
                template_id=agent_tpl.id,
                enterprise_id=enterprise.id,
                folder_path="/tmp/test_kb",
                name_override="客服一号",
            )

        # 断言返回结构
        assert result["agent_id"] == agent.id
        assert result["template_id"] == agent_tpl.id
        assert result["system_prompt_applied"] is True
        assert result["skills_created"] == 4
        assert result["build_status"] == "completed"

        # 断言 build_agent_via_graph 被调用且参数正确
        mock_build.assert_awaited_once()
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["enterprise_id"] == enterprise.id
        assert call_kwargs["name"] == "客服一号"
        assert call_kwargs["folder_path"] == "/tmp/test_kb"

        # 断言 system_prompt 已覆写并填充占位符
        await db_session.refresh(agent)
        assert "客服一号" in agent.system_prompt
        assert enterprise.name in agent.system_prompt
        assert "12" in agent.system_prompt  # file_count
        assert "340" in agent.system_prompt  # knowledge_count

        # 断言技能已创建且链式串联
        skill_result = await db_session.execute(
            select(Skill).where(Skill.agent_id == agent.id).order_by(Skill.created_at)
        )
        skills = skill_result.scalars().all()
        assert len(skills) == 4

        # 验证 template_id 标记来源模板
        assert all(s.template_id is not None for s in skills)
        assert all(s.source == "manual" for s in skills)
        assert all(s.status == "approved" for s in skills)

        # 验证链式编排：next_skill_id 串联
        # skill[0].next_skill_id == skill[1].id
        # skill[1].next_skill_id == skill[2].id
        # skill[2].next_skill_id == skill[3].id
        # skill[3].next_skill_id is None（链尾）
        for i in range(3):
            assert skills[i].next_skill_id == skills[i + 1].id, (
                f"链式编排断裂：skill[{i}].next_skill_id 应指向 skill[{i+1}].id"
            )
        assert skills[3].next_skill_id is None, "链尾 next_skill_id 应为 None"


# ============================================================
# 测试 3：execute_chain
# ============================================================

class TestSkillChainExecution:
    """链式执行 Skill。"""

    @pytest.mark.asyncio
    async def test_skill_chain_execution(self, db_session):
        """三节点链：生成 → 摘要 → 分类，聚合输出正确。"""
        _, agent, user = await _create_enterprise_agent_user(db_session, "链式")

        # 创建 3 个已审批技能，链式串联
        skill1 = Skill(
            agent_id=agent.id,
            name="文本生成",
            skill_type="text_generation",
            input_type="text",
            output_type="text",
            config={"prompt_template": "生成：{input}"},
            status="approved",
        )
        db_session.add(skill1)
        await db_session.flush()

        skill2 = Skill(
            agent_id=agent.id,
            name="内容摘要",
            skill_type="text_summarization",
            input_type="text",
            output_type="text",
            config={},
            status="approved",
        )
        db_session.add(skill2)
        await db_session.flush()

        skill3 = Skill(
            agent_id=agent.id,
            name="分类判定",
            skill_type="text_classification",
            input_type="text",
            output_type="text",
            config={"categories": ["A", "B"]},
            status="approved",
        )
        db_session.add(skill3)
        await db_session.flush()

        # 设置链式编排
        skill1.next_skill_id = skill2.id
        skill2.next_skill_id = skill3.id
        # skill3.next_skill_id 默认 None（链尾）
        await db_session.commit()
        await db_session.refresh(skill1)
        await db_session.refresh(skill2)
        await db_session.refresh(skill3)

        # mock llm_service.chat：依次返回三步输出
        mock_chat = AsyncMock(side_effect=[
            "生成结果文本",   # skill1: text_generation
            "摘要内容",       # skill2: text_summarization
            "A",             # skill3: text_classification
        ])

        with patch("app.services.skill_executor.llm_service.chat", mock_chat):
            result = await skill_executor.execute_chain(
                db=db_session,
                skill_id=skill1.id,
                input_data={"text": "原始输入"},
                user_id=user.id,
            )

        # 断言链式执行结果
        assert result["status"] == "success"
        assert result["total_steps"] == 3
        assert len(result["steps"]) == 3

        # 验证每步状态
        assert result["steps"][0]["skill_name"] == "文本生成"
        assert result["steps"][0]["status"] == "success"
        assert result["steps"][1]["skill_name"] == "内容摘要"
        assert result["steps"][2]["skill_name"] == "分类判定"

        # 验证聚合输出：最后一步的 output 作为主结果
        aggregated = result["aggregated_output"]
        assert aggregated["total_steps"] == 3
        assert aggregated["final_output"]["classification"] == "A"
        # chain 元信息记录全链
        chain = aggregated["chain"]
        assert len(chain) == 3
        assert chain[0]["skill_id"] == skill1.id
        assert chain[2]["skill_id"] == skill3.id

        # 验证 llm_service.chat 被调用 3 次（每步一次）
        assert mock_chat.await_count == 3

        # 验证 SkillExecution 记录已写入（每步一条）
        exec_result = await db_session.execute(
            select(SkillExecution).where(
                SkillExecution.skill_id.in_([skill1.id, skill2.id, skill3.id])
            )
        )
        executions = exec_result.scalars().all()
        assert len(executions) == 3

    @pytest.mark.asyncio
    async def test_chain_cycle_detection(self, db_session):
        """循环引用检测：next_skill_id 形成环时提前终止。"""
        _, agent, user = await _create_enterprise_agent_user(db_session, "环检测")

        skill_a = Skill(
            agent_id=agent.id, name="技能A", skill_type="text_generation",
            input_type="text", output_type="text",
            config={"prompt_template": "{input}"}, status="approved",
        )
        db_session.add(skill_a)
        await db_session.flush()

        skill_b = Skill(
            agent_id=agent.id, name="技能B", skill_type="text_generation",
            input_type="text", output_type="text",
            config={"prompt_template": "{input}"}, status="approved",
        )
        db_session.add(skill_b)
        await db_session.flush()

        # 构造环：A -> B -> A
        skill_a.next_skill_id = skill_b.id
        skill_b.next_skill_id = skill_a.id
        await db_session.commit()

        mock_chat = AsyncMock(return_value="输出")
        with patch("app.services.skill_executor.llm_service.chat", mock_chat):
            result = await skill_executor.execute_chain(
                db=db_session,
                skill_id=skill_a.id,
                input_data={"text": "输入"},
                user_id=user.id,
            )

        # 环检测：执行 2 步后检测到循环，status 为 partial
        assert result["status"] == "partial"
        assert result["total_steps"] == 2


# ============================================================
# 测试 4：低风险自动放行（通过 API 端点）
# ============================================================

class TestLowRiskAutoApprove:
    """generate_skills 端点的低风险自动放行逻辑。"""

    @pytest.mark.asyncio
    async def test_low_risk_auto_approve(self, client, test_engine):
        """search 类型 + 双重筛查通过 + 无 permissions → 自动 approved。"""
        # 1. 创建企业 + Agent
        ent_id = f"ent-auto-{uuid.uuid4().hex[:8]}"
        agent_id = f"agent-auto-{uuid.uuid4().hex[:8]}"
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            ent = Enterprise(id=ent_id, name="自动放行测试企业")
            s.add(ent)
            agent = Agent(
                id=agent_id, enterprise_id=ent_id, name="自动放行Agent",
                status="ready", file_count=5, knowledge_count=100,
            )
            s.add(agent)
            await s.commit()

        # 2. 注册用户并绑定企业
        from tests.conftest import register_user
        email = f"auto-{uuid.uuid4().hex[:8]}@test.com"
        await register_user(client, email, "pass1234", "自动放行用户")
        async with factory() as s:
            user_result = await s.execute(
                select(User).where(User.email == email)
            )
            user = user_result.scalar_one()
            user.enterprise_id = ent_id
            await s.commit()

        # 3. mock skill_generator.generate_for_agent：生成低风险 search 技能
        async def mock_generate(db, agent, max_skills=3):
            skills = []
            for i in range(2):
                skill = Skill(
                    agent_id=agent.id,
                    name=f"知识检索技能{i}",
                    description="基于知识库的语义检索",
                    skill_type="search",
                    input_type="text",
                    output_type="text",
                    config={"prompt_template": "请基于知识库检索以下内容：{input}"},
                    source="generated",
                    status="pending",
                    permissions=[],
                )
                db.add(skill)
                skills.append(skill)
            await db.flush()
            return skills

        # 4. mock SkillScreener.review_run：返回通过结果（screen 真实执行）
        mock_review_run = AsyncMock(return_value={
            "sample_input": {"text": "测试输入"},
            "executed": True,
            "execution_status": "success",
            "output_data": {"result": "检索结果"},
            "output_risks": [],
            "passed": True,
        })

        with patch.object(
            SkillScreener, "review_run", mock_review_run
        ), patch.object(
            __import__("app.api.skills", fromlist=["skill_generator"]).skill_generator,
            "generate_for_agent",
            mock_generate,
        ):
            resp = await client.post("/api/v1/skills/generate", json={
                "agent_id": agent_id,
                "max_skills": 2,
            })

        assert resp.status_code == 200, f"生成失败: {resp.text}"
        data = resp.json()["data"]
        assert data["generated"] == 2

        # 5. 断言两个技能均被自动放行
        for skill in data["skills"]:
            assert skill["status"] == "approved"
            assert skill["source"] == "generated"
            review = skill["review_result"]
            assert review["static_screening"]["passed"] is True
            assert review["review_run"]["passed"] is True
            assert review["auto_approved"]["reason"] == "low_risk_auto_approve"
            assert "timestamp" in review["auto_approved"]
