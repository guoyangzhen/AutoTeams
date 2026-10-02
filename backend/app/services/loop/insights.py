"""Loop LLM 洞察分析（insights）。

从 ``app/services/loop_engine.py`` 拆分（原 43-83、103-341 行）。
闭环的「分析」阶段：把原始对话信号转成可执行的优化建议，并落库为
``OptimizationHistory`` 记录供后续 :meth:`~app.services.loop.engine.LoopCoreMixin.apply_optimization` 应用。

包含：
- ``analyze_feedback``        满意度不达标的根因分析（最多 5 条，并发 LLM 调用）
- ``analyze_knowledge_gaps``  高频问题 × 现有知识库 → 知识缺口
- ``ANALYSIS_PROMPT`` / ``KNOWLEDGE_GAP_PROMPT``  两个 Prompt 模板
"""
import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.optimization_history import OptimizationHistory
from app.services.llm_service import llm_service
from app.services.prompt_security import (
    SYSTEM_PROMPT_GUARDRAIL,
    safe_json_extract,
    wrap_untrusted,
)
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """分析以下对话记录，找出用户不满意的原因和改进方向。

对话内容：
{conversation_content}

用户满意度：{satisfaction}

请分析：
1. 不满意的主要原因（检索不准确/回答不完整/回答错误/其他）
2. 用户实际想要的信息
3. 建议的改进措施

以JSON格式返回：
{{
    "reason": "不满意原因",
    "expected_info": "用户期望的信息",
    "improvement": "建议改进措施",
    "priority": "high/medium/low"
}}
"""

KNOWLEDGE_GAP_PROMPT = """分析以下高频问题和现有知识库，找出知识缺口。

高频问题：
{frequent_questions}

现有知识概况：
{knowledge_summary}

请分析：
1. 哪些问题在知识库中找不到答案
2. 需要补充什么类型的知识
3. 建议的知识补充方向

以JSON格式返回：
{{
    "gaps": ["知识缺口1", "知识缺口2"],
    "suggestions": ["建议1", "建议2"],
    "priority_questions": ["优先处理的问题1", "优先处理的问题2"]
}}
"""


class LoopInsightsMixin:
    """LLM 洞察分析（mixin，由 :class:`app.services.loop.engine.LoopEngine` 组装）。"""

    async def analyze_feedback(
        self,
        db: AsyncSession,
        agent_id: str,
        days: int = 7,
    ) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        result = await db.execute(
            select(Message)
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id == agent_id,
                    Message.role == "assistant",
                    Message.satisfaction.isnot(None),
                    Message.created_at >= cutoff,
                )
            )
            .order_by(Message.created_at.desc())
        )
        messages = result.scalars().all()

        if not messages:
            return {
                "total_feedback": 0,
                "satisfaction_rate": 0,
                "issues": [],
                "suggestions": [],
            }

        total = len(messages)
        satisfied = sum(1 for m in messages if m.satisfaction == "satisfied")
        satisfaction_rate = satisfied / total if total > 0 else 0

        dissatisfied_messages = [
            m for m in messages
            if m.satisfaction in ("unsatisfied", "dissatisfied")
        ]

        issues = []
        # 3.2.3: 将 5 次串行 LLM 调用 + 5 次串行 DB 查询并行化
        # 1) DB 查询合并为 1 次 IN 查询（5 次 RTT → 1 次）
        # 2) LLM 调用用 asyncio.gather 并发执行，Semaphore(3) 限流防 API 限流
        target_messages = dissatisfied_messages[:5]

        # 3.2.3 Step 1: 合并 DB 查询 — 一次性获取所有相关对话的消息
        conv_ids = list({m.conversation_id for m in target_messages if m.conversation_id})
        all_conv_messages: dict[str, list[Message]] = {}
        if conv_ids:
            conv_result = await db.execute(
                select(Message)
                .where(Message.conversation_id.in_(conv_ids))
                .order_by(Message.conversation_id, Message.created_at)
            )
            for m in conv_result.scalars().all():
                all_conv_messages.setdefault(m.conversation_id, []).append(m)

        # 3.2.3 Step 2: 并行执行 LLM 分析（并发上限 3，防 LLM API 限流）
        _LLM_CONCURRENCY = asyncio.Semaphore(3)

        async def _analyze_one(msg: Message) -> dict | None:
            """分析单条不满意消息，返回 issue dict 或 None。"""
            conv_messages = all_conv_messages.get(msg.conversation_id, [])
            conv_content = "\n".join(
                [f"{m.role}: {(m.content or '')[:200]}" for m in conv_messages[-6:]]
            )

            # P0-08: 包裹不可信内容
            safe_conv = wrap_untrusted(conv_content, "对话内容")
            analysis_prompt = ANALYSIS_PROMPT.format(
                conversation_content=safe_conv,
                satisfaction=msg.satisfaction,
            )

            async with _LLM_CONCURRENCY:
                analysis = await llm_service.chat([
                    {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
                    {"role": "user", "content": analysis_prompt}
                ])

            # P0-08: 使用 safe_json_extract 替代 find/rfind
            issue = safe_json_extract(analysis)
            if issue is not None:
                return issue
            return {"reason": (analysis or "")[:200], "priority": "medium"}

        # 并发执行，return_exceptions=True 避免单条失败影响其他
        results = await asyncio.gather(
            *[_analyze_one(m) for m in target_messages],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                errors_total.labels(module=__name__, exception_type=type(r).__name__).inc()
                logger.error(f"分析反馈失败: {r}", exc_info=True)
                continue
            if r is not None:
                issues.append(r)

        # 持久化反馈分析结果
        if issues:
            history = OptimizationHistory(
                agent_id=agent_id,
                type="feedback",
                input_data={"days": days, "total": total},
                output_data={
                    "satisfaction_rate": round(satisfaction_rate, 2),
                    "dissatisfied_count": total - satisfied,
                    "issues": issues,
                },
                applied=False,
            )
            db.add(history)
            await db.flush()
            await db.commit()

        return {
            "total_feedback": total,
            "satisfaction_rate": round(satisfaction_rate, 2),
            "dissatisfied_count": total - satisfied,
            "issues": issues,
        }

    async def analyze_knowledge_gaps(
        self,
        db: AsyncSession,
        agent_id: str,
        days: int = 30,
    ) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        result = await db.execute(
            select(Message.content)
            .join(Conversation)
            .where(
                and_(
                    Conversation.agent_id == agent_id,
                    Message.role == "user",
                    Message.created_at >= cutoff,
                )
            )
            .order_by(Message.created_at.desc())
            .limit(100)
        )
        user_questions = [row[0] for row in result.fetchall()]

        if not user_questions:
            return {
                "total_questions": 0,
                "gaps": [],
                "suggestions": [],
            }

        question_keywords = []
        for q in user_questions:
            words = (
                q.replace("？", "")
                .replace("?", "")
                .replace("，", " ")
                .replace(",", " ")
                .split()
            )
            question_keywords.extend([w for w in words if len(w) > 1])

        keyword_counts = Counter(question_keywords)
        top_keywords = keyword_counts.most_common(20)

        agent_result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        knowledge_summary = ""
        if agent:
            knowledge_summary = (
                f"知识库包含 {agent.knowledge_count} 条知识，"
                f"{agent.file_count} 个文件"
            )

        frequent_questions = "\n".join(
            [f"- {q[:100]}" for q in user_questions[:20]]
        )

        try:
            # P0-08: 包裹不可信内容
            safe_questions = wrap_untrusted(frequent_questions, "高频问题")
            safe_summary = wrap_untrusted(knowledge_summary, "知识摘要")
            analysis_prompt = KNOWLEDGE_GAP_PROMPT.format(
                frequent_questions=safe_questions,
                knowledge_summary=safe_summary,
            )

            analysis = await llm_service.chat([
                {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
                {"role": "user", "content": analysis_prompt}
            ])

            # P0-08: 使用 safe_json_extract
            gaps_analysis = safe_json_extract(analysis)
            if gaps_analysis is None:
                gaps_analysis = {"gaps": [], "suggestions": [(analysis or "")[:500]]}

        except (httpx.HTTPError, ValueError, RuntimeError, json.JSONDecodeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"分析知识缺口失败: {e}", exc_info=True)
            gaps_analysis = {"gaps": [], "suggestions": []}
        except (OSError, TypeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"分析知识缺口失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"分析知识缺口失败: {e}") from e

        # 持久化知识缺口分析结果
        history = OptimizationHistory(
            agent_id=agent_id,
            type="gap",
            input_data={
                "days": days,
                "total_questions": len(user_questions),
                "top_keywords": [{"keyword": k, "count": c} for k, c in top_keywords[:10]],
            },
            output_data={
                "gaps": gaps_analysis.get("gaps", []),
                "suggestions": gaps_analysis.get("suggestions", []),
                "priority_questions": gaps_analysis.get("priority_questions", []),
            },
            applied=False,
        )
        db.add(history)
        await db.flush()
        await db.commit()

        return {
            "total_questions": len(user_questions),
            "top_keywords": [{"keyword": k, "count": c} for k, c in top_keywords[:10]],
            "gaps": gaps_analysis.get("gaps", []),
            "suggestions": gaps_analysis.get("suggestions", []),
            "priority_questions": gaps_analysis.get("priority_questions", []),
        }
