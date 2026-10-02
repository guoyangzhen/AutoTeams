"""智能化生成 Skill。

基于企业知识库 + 对话历史，AI 分析企业需要什么技能，自动生成 Skill 定义，
并进入待审批状态。
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.file import File
from app.models.message import Message
from app.models.skill import Skill
from app.services.llm_service import llm_service
from app.services.prompt_security import wrap_untrusted, safe_json_array_extract, SYSTEM_PROMPT_GUARDRAIL
from app.services.skill_screener import SkillScreener
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


# 常见可复用 Skill 关键词启发式（当 LLM 不可用时作为 fallback）
_SKILL_HEURISTICS = [
    ("合同", "合同条款提取", "data_extraction", "从合同文本中提取关键条款、金额、期限与违约责任"),
    ("周报", "周报生成", "text_generation", "基于本周工作记录自动生成结构化周报"),
    ("摘要", "文档摘要", "text_summarization", "为长文档生成简洁摘要"),
    ("分类", "智能分类", "text_classification", "将文本按预定义类别自动分类"),
    ("翻译", "多语言翻译", "translation", "将指定文本翻译为目标语言"),
    ("代码", "代码生成", "code_generation", "根据自然语言描述生成示例代码"),
    ("FAQ", "FAQ 生成", "text_generation", "基于知识库内容生成常见问题与答案"),
    ("数据", "数据提取", "data_extraction", "从非结构化文本中提取结构化数据"),
]

_GENERATION_PROMPT = """你是一名企业 AI 技能设计师。请根据以下信息，为该智能体设计 1-{max_skills} 个可复用 Skill。

智能体名称：{agent_name}
智能体描述：{agent_description}

最近用户高频问题：
{questions}

知识库文件：
{files}

要求：
1. 每个 Skill 必须能解决一个真实、重复出现的业务问题。
2. Skill 类型必须是以下之一：text_generation, text_summarization, text_classification, data_extraction, translation, code_generation, custom。
3. 返回 JSON 数组，每个元素包含：
{{
    "name": "技能名称",
    "skill_type": "类型",
    "description": "一句话描述用途",
    "input_type": "text|image",
    "output_type": "text",
    "config": {{"prompt_template": "...", 其他必要参数}},
    "permissions": ["execute:llm"]
}}

只返回 JSON 数组，不要其他解释。"""


class SkillGenerator:
    """基于对话历史与知识库自动生成 Skill 定义。"""

    def __init__(self):
        self.screener = SkillScreener()

    async def generate_for_agent(
        self,
        db: AsyncSession,
        agent: Agent,
        max_skills: int = 3,
    ) -> list[Skill]:
        """为指定 Agent 生成待审批 Skill。"""
        questions = await self._fetch_recent_user_questions(db, agent.id)

        file_result = await db.execute(
            select(File.original_name).where(File.agent_id == agent.id)
        )
        file_names = [r[0] for r in file_result.all()]

        # 优先使用 LLM 生成；无 API Key 或失败时回退到启发式规则
        proposals: list[dict[str, Any]] = []
        try:
            proposals = await self._llm_generate(
                agent=agent,
                questions=questions,
                file_names=file_names,
                max_skills=max_skills,
            )
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning(f"LLM 生成 Skill 失败，使用启发式 fallback: {e}", exc_info=True)
            proposals = self._heuristic_proposals(questions, file_names, max_skills)

        created: list[Skill] = []
        for proposal in proposals[:max_skills]:
            skill = Skill(
                agent_id=agent.id,
                name=proposal.get("name", "未命名 Skill"),
                description=proposal.get("description", ""),
                skill_type=proposal.get("skill_type", "custom"),
                input_type=proposal.get("input_type", "text"),
                output_type=proposal.get("output_type", "text"),
                config=proposal.get("config", {}),
                permissions=proposal.get("permissions", ["execute:llm"]),
                source="generated",
                status="pending",
                generation_context={
                    "trigger": "manual_generation",
                    "sample_queries": questions[:10],
                    "file_count": len(file_names),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            # 自动生成后先做静态筛查，记录结果
            screening = self.screener.screen(skill)
            skill.review_result = screening.to_dict()
            # 静态筛查未通过则直接标记为 rejected，避免进入 pending
            if not screening.passed:
                skill.status = "rejected"
            db.add(skill)
            created.append(skill)

        return created

    async def _fetch_recent_user_questions(
        self,
        db: AsyncSession,
        agent_id: str,
        days: int = 30,
        limit: int = 50,
    ) -> list[str]:
        """获取最近用户提问（按出现频率排序）。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await db.execute(
            select(Message.content)
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id == agent_id,
                    Message.role == "user",
                    Message.created_at >= cutoff,
                    Message.is_deleted.is_(False),
                )
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return [r[0] for r in result.all() if r[0]]

    async def _llm_generate(
        self,
        agent: Agent,
        questions: list[str],
        file_names: list[str],
        max_skills: int,
    ) -> list[dict[str, Any]]:
        prompt = _GENERATION_PROMPT.format(
            max_skills=max_skills,
            agent_name=agent.name,
            agent_description=agent.description or "",
            questions="\n".join(f"- {q}" for q in questions[:20]) or "（暂无）",
            files="\n".join(f"- {f}" for f in file_names[:20]) or "（暂无）",
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {"role": "user", "content": wrap_untrusted(prompt, "生成任务")},
        ]
        response = await llm_service.chat(messages, tier="cheap")
        proposals = safe_json_array_extract(response) or []
        if not isinstance(proposals, list):
            raise ValueError(f"LLM 返回的不是 JSON 数组: {type(proposals)}")
        return proposals

    def _heuristic_proposals(
        self,
        questions: list[str],
        file_names: list[str],
        max_skills: int,
    ) -> list[dict[str, Any]]:
        """LLM 不可用时，基于关键词匹配生成 Skill 提案。"""
        all_text = "\n".join(questions + file_names)
        matched: list[dict[str, Any]] = []
        seen_names = set()
        for keyword, name, skill_type, desc in _SKILL_HEURISTICS:
            if keyword in all_text and name not in seen_names:
                seen_names.add(name)
                matched.append(
                    {
                        "name": name,
                        "skill_type": skill_type,
                        "description": desc,
                        "input_type": "text",
                        "output_type": "text",
                        "config": {"prompt_template": f"请根据输入完成{name}任务：\n{{input}}"},
                        "permissions": ["execute:llm"],
                    }
                )
            if len(matched) >= max_skills:
                break
        return matched


skill_generator = SkillGenerator()
