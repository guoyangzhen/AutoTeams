"""RAG 自动评估服务。

每次 assistant 消息生成后，基于检索结果与回答计算标准化指标。
支持 LLM 评分（优先）与启发式 fallback（无 API Key 时）。
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.message import Message
from app.models.conversation import Conversation
from app.models.agent import Agent
from app.models.rag_evaluation import RAGEvaluation
from app.services.llm_service import llm_service, ModelTier
from app.services.prompt_security import wrap_untrusted, safe_json_extract, SYSTEM_PROMPT_GUARDRAIL
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


_EVAL_PROMPT = """你是 RAG 质量评估专家。请对以下问答进行评分，返回 JSON：
{{
    "faithfulness": 0-1,        // 回答是否忠实于检索内容，有无幻觉
    "answer_relevancy": 0-1,    // 回答是否与用户问题相关
    "context_precision": 0-1,   // 检索到的片段中有多少是相关的
    "context_recall": 0-1       // 回答问题所需信息有多少被检索到
}}

用户问题：
{query}

检索到的片段：
{context}

助手回答：
{answer}
"""


class RAGEvaluator:
    """RAG 评估器：支持 LLM 评分与启发式 fallback。"""

    async def evaluate_message(self, message_id: str) -> Optional[RAGEvaluation]:
        """对指定 assistant 消息执行评估并落库。"""
        async with async_session_factory() as db:
            result = await db.execute(
                select(Message, Conversation, Agent)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .join(Agent, Conversation.agent_id == Agent.id)
                .where(
                    Message.id == message_id,
                    Message.role == "assistant",
                    Message.is_deleted.is_(False),
                )
            )
            row = result.first()
            if not row:
                logger.warning(f"找不到可评估的消息: {message_id}")
                return None

            message, conversation, agent = row

            # 查找对应用户问题（该会话中当前 assistant 消息之前的最后一条 user 消息）
            user_result = await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.role == "user",
                    Message.created_at <= message.created_at,
                    Message.is_deleted.is_(False),
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            user_msg = user_result.scalar_one_or_none()
            query = (user_msg.content or "") if user_msg else ""
            answer = message.content or ""
            sources = message.sources or []

            scores = await self._score(query, answer, sources)

            # 幂等：同一 message 只保留一条评估记录
            existing_result = await db.execute(
                select(RAGEvaluation).where(RAGEvaluation.message_id == message_id)
            )
            existing = existing_result.scalar_one_or_none()
            if existing:
                existing.faithfulness = scores["faithfulness"]
                existing.answer_relevancy = scores["answer_relevancy"]
                existing.context_precision = scores["context_precision"]
                existing.context_recall = scores["context_recall"]
                existing.details = scores["details"]
                existing.evaluated_at = datetime.now(timezone.utc)
                evaluation = existing
            else:
                evaluation = RAGEvaluation(
                    agent_id=agent.id,
                    conversation_id=conversation.id,
                    message_id=message_id,
                    **{k: scores[k] for k in ("faithfulness", "answer_relevancy", "context_precision", "context_recall")},
                    details=scores["details"],
                )
                db.add(evaluation)

            await db.commit()
            await db.refresh(evaluation)
            return evaluation

    async def _score(self, query: str, answer: str, sources: list[dict]) -> dict:
        """计算 RAG 指标，优先使用 LLM，失败则回退启发式。"""
        try:
            return await self._llm_score(query, answer, sources)
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning(f"LLM RAG 评估失败，使用启发式 fallback: {e}", exc_info=True)
            return self._heuristic_score(query, answer, sources)

    async def _llm_score(self, query: str, answer: str, sources: list[dict]) -> dict:
        context = "\n\n".join(
            f"[{s.get('file_name', s.get('source', '未知'))}] {s.get('content', '')[:400]}"
            for s in sources[:5]
        ) or "（无检索来源）"

        prompt = _EVAL_PROMPT.format(
            query=wrap_untrusted(query, "用户问题"),
            context=wrap_untrusted(context, "检索结果"),
            answer=wrap_untrusted(answer, "助手回答"),
        )
        response = await llm_service.chat(
            [{"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
             {"role": "user", "content": prompt}],
            tier=ModelTier.CHEAP,
        )
        parsed = safe_json_extract(response)
        if not isinstance(parsed, dict):
            raise ValueError(f"LLM 评估返回格式错误: {type(parsed)}")

        return {
            "faithfulness": self._clamp(parsed.get("faithfulness", 0)),
            "answer_relevancy": self._clamp(parsed.get("answer_relevancy", 0)),
            "context_precision": self._clamp(parsed.get("context_precision", 0)),
            "context_recall": self._clamp(parsed.get("context_recall", 0)),
            "details": {
                "method": "llm",
                "source_count": len(sources),
                "raw": parsed,
            },
        }

    def _heuristic_score(self, query: str, answer: str, sources: list[dict]) -> dict:
        """无 LLM 时的轻量启发式评分（基于来源数量、回答长度、满意度占位）。"""
        source_count = len(sources)
        answer_len = len(answer)
        query_len = len(query)

        # 有来源且回答非空则忠实度较高
        faithfulness = 0.7 if source_count > 0 and answer_len > 20 else 0.3
        # 回答包含问题关键词越多，相关性越高
        query_keywords = set(query.lower().split()) if query else set()
        answer_words = set(answer.lower().split())
        overlap = len(query_keywords & answer_words) / max(len(query_keywords), 1)
        answer_relevancy = 0.5 + 0.4 * overlap
        # 来源越多，检索精度可能下降；1-3 条为佳
        context_precision = 0.9 if 1 <= source_count <= 3 else 0.6 if source_count > 0 else 0.2
        # 召回率与来源数正相关（上限 5 条）
        context_recall = min(source_count / 4, 1.0) if source_count else 0.1

        return {
            "faithfulness": self._clamp(faithfulness),
            "answer_relevancy": self._clamp(answer_relevancy),
            "context_precision": self._clamp(context_precision),
            "context_recall": self._clamp(context_recall),
            "details": {
                "method": "heuristic",
                "source_count": source_count,
                "answer_length": answer_len,
                "query_length": query_len,
            },
        }

    @staticmethod
    def _clamp(value: float) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        return max(0.0, min(1.0, value))

    async def get_agent_trend(
        self,
        db: AsyncSession,
        agent_id: str,
        days: int = 30,
    ) -> list[dict]:
        """按日期聚合的 RAG 质量趋势（用于 LoopDashboard）。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await db.execute(
            select(
                func.date(RAGEvaluation.evaluated_at).label("date"),
                func.avg(RAGEvaluation.faithfulness).label("faithfulness"),
                func.avg(RAGEvaluation.answer_relevancy).label("answer_relevancy"),
                func.avg(RAGEvaluation.context_precision).label("context_precision"),
                func.avg(RAGEvaluation.context_recall).label("context_recall"),
                func.count(RAGEvaluation.id).label("count"),
            )
            .where(
                RAGEvaluation.agent_id == agent_id,
                RAGEvaluation.evaluated_at >= cutoff,
            )
            .group_by(func.date(RAGEvaluation.evaluated_at))
            .order_by(func.date(RAGEvaluation.evaluated_at))
        )
        rows = result.all()
        return [
            {
                "date": str(r.date),
                "faithfulness": round(float(r.faithfulness or 0), 3),
                "answer_relevancy": round(float(r.answer_relevancy or 0), 3),
                "context_precision": round(float(r.context_precision or 0), 3),
                "context_recall": round(float(r.context_recall or 0), 3),
                "count": int(r.count or 0),
            }
            for r in rows
        ]


rag_evaluator = RAGEvaluator()
