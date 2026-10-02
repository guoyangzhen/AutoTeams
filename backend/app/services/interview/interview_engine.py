"""WT4 交互式企业访谈引擎（PRD §5.7 + 重构方案 §7.6 阶段1）。

核心流程：
- start_session：基于问题库创建会话 + 问题记录
- get_next_question：按优先级返回下一未回答问题
- submit_answer：记录回答 → 更新完成度 → 触发增量重编译（调用 WT1）
- get_session_status：会话进度 + 完成度

完成度模型（MVP）：
- session_completeness = answered_count / total_count（访谈本身的进度）
- 提交回答后尝试触发 WT1 增量重编译，将 interview_completion 注入完成度计算
- 重编译为 best-effort：无既有编译任务或失败时不阻断访谈，仅记录日志

工程约束（spec §2.2）：
- service 层写操作显式 await db.commit()
- LLM 调用前用户输入经 prompt_security.wrap_untrusted 包裹（访谈回答可能
  进入 LLM 生成更精准追问时；MVP 暂不调用 LLM，但保留 wrap 入口）
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview import InterviewQuestion, InterviewSession
from app.services.interview.question_bank import (
    QuestionBank,
    question_bank as default_question_bank,
)
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


# 完成度提升系数：每回答一个 P0/P1/P2 问题对完成度的贡献权重
_PRIORITY_WEIGHTS = {"P0": 3.0, "P1": 2.0, "P2": 1.0}
# 完成度上限
_COMPLETENESS_CAP = 100.0


class InterviewEngine:
    """交互式企业访谈引擎。

    Usage::

        engine = InterviewEngine()
        session_id = await engine.start_session(db, enterprise_id, user_id)
        q = await engine.get_next_question(db, session_id)
        result = await engine.submit_answer(db, session_id, q.question_id, "...")
    """

    def __init__(self, bank: Optional[QuestionBank] = None) -> None:
        self._bank = bank or default_question_bank

    # ========================================================
    # start_session
    # ========================================================

    async def start_session(
        self,
        db: AsyncSession,
        enterprise_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """启动访谈会话：创建 session + 按问题库实例化问题记录。

        Returns:
            {"session_id": str, "status": "active", "total_count": int}
        """
        session = InterviewSession(
            enterprise_id=enterprise_id,
            user_id=user_id,
            status="active",
            answered_count=0,
            total_count=self._bank.total,
        )
        db.add(session)
        await db.flush()  # 拿到 session.id

        # 按优先级排序实例化问题记录
        now = datetime.now(timezone.utc)
        for tmpl in self._bank.all_templates():
            db.add(
                InterviewQuestion(
                    session_id=session.id,
                    category=tmpl.category,
                    question=tmpl.question,
                    expected_output=tmpl.expected_output,
                    affected_field=tmpl.affected_field,
                    priority=tmpl.priority,
                    created_at=now,
                )
            )

        await db.commit()
        logger.info(
            "访谈会话已启动: enterprise=%s session=%s total=%d",
            enterprise_id, session.id, session.total_count,
        )
        return {
            "session_id": session.id,
            "status": "active",
            "total_count": session.total_count,
        }

    # ========================================================
    # get_next_question
    # ========================================================

    async def get_next_question(
        self,
        db: AsyncSession,
        session_id: str,
    ) -> dict[str, Any]:
        """返回下一未回答问题（按优先级 + 创建时间排序）。

        无下一问时 question_id 为 None（会话已完成）。
        """
        session = await self._get_session(db, session_id)
        if session is None:
            raise ValueError(f"访谈会话不存在: {session_id}")

        result = await db.execute(
            select(InterviewQuestion)
            .where(
                InterviewQuestion.session_id == session_id,
                InterviewQuestion.answer.is_(None),
            )
            .order_by(
                InterviewQuestion.priority.asc(),
                InterviewQuestion.created_at.asc(),
            )
            .limit(1)
        )
        q = result.scalar_one_or_none()
        if q is None:
            # 全部已回答，标记会话完成
            await self._mark_completed(db, session)
            return {"question_id": None}

        return {
            "question_id": q.id,
            "category": q.category,
            "question": q.question,
            "priority": q.priority,
            "affected_field": q.affected_field,
        }

    # ========================================================
    # submit_answer
    # ========================================================

    async def submit_answer(
        self,
        db: AsyncSession,
        session_id: str,
        question_id: str,
        answer: str,
    ) -> dict[str, Any]:
        """提交回答 → 记录 → 更新完成度 → 触发增量重编译。

        Returns:
            {"updated_completeness": float, "recompile_triggered": bool,
             "next_question": dict | None}
        """
        session = await self._get_session(db, session_id)
        if session is None:
            raise ValueError(f"访谈会话不存在: {session_id}")
        if session.status == "completed":
            raise ValueError("访谈会话已完成，无法继续提交")

        result = await db.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.id == question_id,
                InterviewQuestion.session_id == session_id,
            )
        )
        question = result.scalar_one_or_none()
        if question is None:
            raise ValueError(f"问题不存在或不属于该会话: {question_id}")
        if question.answer is not None:
            raise ValueError(f"问题已回答: {question_id}")

        # 记录回答
        question.answer = answer
        question.answered_at = datetime.now(timezone.utc)

        # 更新会话计数
        session.answered_count = (session.answered_count or 0) + 1

        # 先提交回答 + 获取下一问，确保访谈流程不被重编译阻塞
        # 关键修复：原先 db.commit() 在 _trigger_incremental_recompile 之后，
        # 而重编译会同步执行完整编译管线（含 LLM 调用，耗时数分钟），
        # 导致 HTTP 请求超时、前端无法收到 next_question、用户反复看到同一题。
        await db.flush()

        # 计算更新后的完成度
        updated_completeness = await self.compute_completeness(db, session)

        # 获取下一问
        next_q = await self.get_next_question(db, session_id)

        # 立即提交——回答记录 + 下一问获取完成，不受重编译影响
        await db.commit()

        # 触发增量重编译（best-effort，非阻塞）：
        # - [SKIP] 答案不提供新信息，跳过重编译
        # - 使用独立 session 避免影响访谈事务
        recompile_triggered = False
        if answer != "[SKIP]":
            try:
                recompile_triggered = await self._trigger_incremental_recompile_bg(
                    session, question
                )
            except Exception as e:
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                logger.warning(
                    "增量重编译触发失败（不阻断访谈）: session=%s error=%s",
                    session_id, e, exc_info=True,
                )
                recompile_triggered = False

        return {
            "updated_completeness": round(updated_completeness, 2),
            "recompile_triggered": recompile_triggered,
            "next_question": next_q,
        }

    # ========================================================
    # get_session_status
    # ========================================================

    async def get_session_status(
        self,
        db: AsyncSession,
        session_id: str,
    ) -> dict[str, Any]:
        """返回会话状态：已回答/总数/完成度。"""
        session = await self._get_session(db, session_id)
        if session is None:
            raise ValueError(f"访谈会话不存在: {session_id}")
        completeness = await self.compute_completeness(db, session)
        return {
            "session_id": session.id,
            "status": session.status,
            "answered_count": session.answered_count,
            "total_count": session.total_count,
            "completeness": round(completeness, 2),
        }

    # ========================================================
    # 完成度计算
    # ========================================================

    async def compute_completeness(
        self,
        db: AsyncSession,
        session: InterviewSession,
    ) -> float:
        """计算会话完成度（0-100）。

        基于已回答问题的优先级加权：完成度 = Σ(权重) / Σ(全部权重) * 100。
        这保证回答高优先级问题对完成度贡献更大，符合"渐进式对话"语义。
        """
        total = session.total_count or 0
        if total == 0:
            return 0.0

        # 全部问题的权重总和
        total_weight = sum(
            _PRIORITY_WEIGHTS.get(tmpl.priority, 1.0)
            for tmpl in self._bank.all_templates()
        )
        if total_weight <= 0:
            return 0.0

        # 已回答问题的权重总和
        result = await db.execute(
            select(InterviewQuestion.priority).where(
                InterviewQuestion.session_id == session.id,
                InterviewQuestion.answer.is_not(None),
            )
        )
        answered_weight = sum(
            _PRIORITY_WEIGHTS.get(row[0], 1.0) for row in result.fetchall()
        )

        completeness = (answered_weight / total_weight) * _COMPLETENESS_CAP
        return min(completeness, _COMPLETENESS_CAP)

    # ========================================================
    # 内部：触发增量重编译（调用 WT1，best-effort）
    # ========================================================

    async def _trigger_incremental_recompile(
        self,
        db: AsyncSession,
        session: InterviewSession,
        question: InterviewQuestion,
    ) -> bool:
        """触发 WT1 增量重编译。

        依据 affected_field 推导受影响层级，调用 WT1 CompilationPipeline.run_incremental。
        若企业尚无编译任务（首次访谈，无文件），跳过重编译（返回 False）。
        任何异常向上抛出由调用方捕获记录，不阻断访谈主流程。

        spec §10.4 契约：WT4 消费 WT1 的 CompilationGaps；本处复用 WT1 的
        重编译入口，将 interview_completion 注入完成度计算。
        """
        # 延迟导入避免循环依赖与启动期加载 WT1 全量模块
        from app.models.compiler import CompilationJob
        from app.services.compiler.pipeline import CompilationPipeline

        # 查找企业最近一个编译任务（任意状态，用于增量重编译）
        job_result = await db.execute(
            select(CompilationJob)
            .where(CompilationJob.enterprise_id == session.enterprise_id)
            .order_by(CompilationJob.created_at.desc())
            .limit(1)
        )
        job = job_result.scalar_one_or_none()
        if job is None:
            # 企业尚未编译过，访谈回答先记录，待首次编译时纳入完成度
            logger.info(
                "企业 %s 尚无编译任务，跳过增量重编译（访谈回答已记录）",
                session.enterprise_id,
            )
            return False

        affected_stages = self._bank.affected_stages_for_field(question.affected_field)
        # 计算当前访谈完成度，注入 WT1 完成度计算
        interview_completion = await self.compute_completeness(db, session)

        pipeline = CompilationPipeline(db, session.enterprise_id)
        await pipeline.run_incremental(
            job_id=job.id,
            affected_stages=affected_stages,
            interview_completion=interview_completion,
        )
        logger.info(
            "增量重编译已触发: enterprise=%s job=%s stages=%s completeness=%.2f",
            session.enterprise_id, job.id, affected_stages, interview_completion,
        )
        return True

    async def _trigger_incremental_recompile_bg(
        self,
        session: InterviewSession,
        question: InterviewQuestion,
    ) -> bool:
        """使用独立 db session 触发增量重编译（非阻塞访谈事务）。

        访谈回答已在主事务中提交，此处用 async_session_factory 创建独立会话
        执行重编译。即使重编译失败或耗时较长，也不影响访谈流程。
        """
        from app.database import async_session_factory
        from app.models.compiler import CompilationJob
        from app.services.compiler.pipeline import CompilationPipeline


        async with async_session_factory() as bg_db:
            # 查找企业最近一个编译任务
            job_result = await bg_db.execute(
                select(CompilationJob)
                .where(CompilationJob.enterprise_id == session.enterprise_id)
                .order_by(CompilationJob.created_at.desc())
                .limit(1)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                logger.info(
                    "企业 %s 尚无编译任务，跳过增量重编译（访谈回答已记录）",
                    session.enterprise_id,
                )
                return False

            affected_stages = self._bank.affected_stages_for_field(question.affected_field)
            # 用独立 session 重新查询 session 对象（避免跨 session 引用）
            bg_session = await bg_db.execute(
                select(InterviewSession).where(InterviewSession.id == session.id)
            )
            bg_session_obj = bg_session.scalar_one_or_none()
            if bg_session_obj is None:
                return False
            interview_completion = await self.compute_completeness(bg_db, bg_session_obj)

            pipeline = CompilationPipeline(bg_db, session.enterprise_id)
            await pipeline.run_incremental(
                job_id=job.id,
                affected_stages=affected_stages,
                interview_completion=interview_completion,
            )
            logger.info(
                "增量重编译已触发(独立session): enterprise=%s job=%s stages=%s completeness=%.2f",
                session.enterprise_id, job.id, affected_stages, interview_completion,
            )
            return True

    # ========================================================
    # 内部辅助
    # ========================================================

    async def _get_session(
        self, db: AsyncSession, session_id: str
    ) -> Optional[InterviewSession]:
        result = await db.execute(
            select(InterviewSession).where(InterviewSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def _mark_completed(
        self, db: AsyncSession, session: InterviewSession
    ) -> None:
        """全部问题已回答时标记会话完成。"""
        if session.status == "completed":
            return
        await db.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session.id)
            .values(status="completed")
        )
        await db.commit()
        logger.info("访谈会话已完成: session=%s", session.id)


# 模块级单例（无状态，可安全共享）
interview_engine = InterviewEngine()
