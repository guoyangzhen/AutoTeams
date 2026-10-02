"""访谈引擎测试（PRD §5.7，渐进式对话 + 完成度更新 + 增量重编译）。

测试覆盖：
- start_session：创建会话 + 问题记录
- get_next_question：按优先级返回问题
- submit_answer：记录回答 → 完成度变化 → 增量重编译触发
- get_session_status：会话状态 + 完成度
- 7 大类问题全部纳入会话
- 完成度从 0 到 100 的渐进变化
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.interview.interview_engine import InterviewEngine
from app.services.interview.question_bank import question_bank
from app.models.interview import InterviewSession, InterviewQuestion

# 共享 fixtures（conftest_extensions 未被 pytest 自动发现，需显式导入）


# ============================================================
# 测试辅助
# ============================================================


async def _seed_enterprise_and_user(db):
    """创建企业 + 用户。"""
    from sqlalchemy import text
    import uuid
    from datetime import datetime, timezone
    from app.utils.security import get_password_hash

    eid = f"ent-{uuid.uuid4().hex[:8]}"
    uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    await db.execute(
        text(
            "INSERT INTO enterprises (id, name, is_active, invite_max_uses, invite_used_count, created_at, updated_at) "
            "VALUES (:id, :name, 1, 10, 0, :now, :now)"
        ),
        {"id": eid, "name": "测试企业", "now": now},
    )
    await db.execute(
        text(
            "INSERT INTO users "
            "(id, email, password_hash, name, role, enterprise_id, is_active, "
            "created_at, updated_at) "
            "VALUES (:id, :email, :ph, :name, 'admin', :eid, 1, :now, :now)"
        ),
        {
            "id": uid,
            "email": f"test-{uuid.uuid4().hex[:6]}@test.com",
            "ph": get_password_hash("Test1234!"),
            "name": "测试管理员",
            "eid": eid,
            "now": now,
        },
    )
    await db.commit()
    return eid, uid


# ============================================================
# start_session 测试
# ============================================================


class TestStartSession:
    async def test_start_session_creates_session_and_questions(
        self, db_session, v3_tables
    ):
        """启动会话：创建 1 个 session + N 个问题记录（N = 问题库总数）。"""
        eid, uid = await _seed_enterprise_and_user(db_session)

        engine = InterviewEngine()
        result = await engine.start_session(db_session, eid, uid)

        assert result["status"] == "active"
        assert result["total_count"] == question_bank.total
        assert "session_id" in result

        # 验证问题记录数
        from sqlalchemy import select
        q_result = await db_session.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.session_id == result["session_id"]
            )
        )
        questions = q_result.scalars().all()
        assert len(questions) == question_bank.total

    async def test_start_session_questions_cover_7_categories(
        self, db_session, v3_tables
    ):
        """启动会话后问题覆盖 7 大类。"""
        eid, uid = await _seed_enterprise_and_user(db_session)

        engine = InterviewEngine()
        result = await engine.start_session(db_session, eid, uid)

        from sqlalchemy import select
        q_result = await db_session.execute(
            select(InterviewQuestion.category).where(
                InterviewQuestion.session_id == result["session_id"]
            )
        )
        categories = {row[0] for row in q_result.fetchall()}
        assert len(categories) == 7
        for cat in question_bank.categories:
            assert cat in categories


# ============================================================
# get_next_question 测试
# ============================================================


class TestGetNextQuestion:
    async def test_get_next_question_returns_p0_first(
        self, db_session, v3_tables
    ):
        """get_next_question 优先返回 P0 问题。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        next_q = await engine.get_next_question(db_session, session["session_id"])

        assert next_q["question_id"] is not None
        assert next_q["priority"] == "P0"

    async def test_get_next_question_returns_none_when_completed(
        self, db_session, v3_tables
    ):
        """全部问题已回答后返回 question_id=None。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        # 答完所有问题
        from sqlalchemy import update
        await db_session.execute(
            update(InterviewQuestion)
            .where(InterviewQuestion.session_id == session["session_id"])
            .values(answer="测试答案")
        )
        # 更新会话状态
        from sqlalchemy import select
        sess_result = await db_session.execute(
            select(InterviewSession).where(InterviewSession.id == session["session_id"])
        )
        sess = sess_result.scalar_one()
        sess.answered_count = sess.total_count
        await db_session.commit()

        next_q = await engine.get_next_question(db_session, session["session_id"])
        assert next_q["question_id"] is None

    async def test_get_next_question_invalid_session_raises(
        self, db_session, v3_tables
    ):
        """不存在的 session_id 抛出 ValueError。"""
        engine = InterviewEngine()
        with pytest.raises(ValueError, match="不存在"):
            await engine.get_next_question(db_session, "invalid-session-id")


# ============================================================
# submit_answer 测试（含完成度变化）
# ============================================================


class TestSubmitAnswer:
    async def test_submit_answer_updates_completeness(
        self, db_session, v3_tables
    ):
        """提交回答后完成度变化（从 0 到 >0）。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        # 初始完成度 = 0
        status = await engine.get_session_status(db_session, session["session_id"])
        assert status["completeness"] == 0.0

        # 获取下一问
        next_q = await engine.get_next_question(db_session, session["session_id"])
        question_id = next_q["question_id"]

        # 提交回答（mock 增量重编译，避免依赖 WT1）
        with patch.object(
            engine, "_trigger_incremental_recompile", new=AsyncMock(return_value=False)
        ):
            result = await engine.submit_answer(
                db_session, session["session_id"], question_id, "这是测试回答"
            )

        assert result["updated_completeness"] > 0
        assert result["recompile_triggered"] is False
        assert result["next_question"] is not None

    async def test_submit_answer_increases_completeness_progressively(
        self, db_session, v3_tables
    ):
        """回答越多完成度越高（渐进式）。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        with patch.object(
            engine, "_trigger_incremental_recompile", new=AsyncMock(return_value=False)
        ):
            # 回答第一个问题
            q1 = await engine.get_next_question(db_session, session["session_id"])
            r1 = await engine.submit_answer(
                db_session, session["session_id"], q1["question_id"], "回答1"
            )
            c1 = r1["updated_completeness"]

            # 回答第二个问题
            q2 = r1["next_question"]
            r2 = await engine.submit_answer(
                db_session, session["session_id"], q2["question_id"], "回答2"
            )
            c2 = r2["updated_completeness"]

            assert c2 > c1

    async def test_submit_answer_rejects_duplicate(
        self, db_session, v3_tables
    ):
        """重复回答同一问题抛出 ValueError。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        q = await engine.get_next_question(db_session, session["session_id"])

        with patch.object(
            engine, "_trigger_incremental_recompile", new=AsyncMock(return_value=False)
        ):
            await engine.submit_answer(
                db_session, session["session_id"], q["question_id"], "第一次回答"
            )

        with pytest.raises(ValueError, match="已回答"):
            await engine.submit_answer(
                db_session, session["session_id"], q["question_id"], "第二次回答"
            )

    async def test_submit_answer_invalid_question_raises(
        self, db_session, v3_tables
    ):
        """提交不存在的问题 ID 抛出 ValueError。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        with pytest.raises(ValueError, match="不存在"):
            await engine.submit_answer(
                db_session, session["session_id"], "invalid-qid", "回答"
            )

    async def test_submit_answer_records_answer_text(
        self, db_session, v3_tables
    ):
        """提交回答后 answer 字段被记录。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        q = await engine.get_next_question(db_session, session["session_id"])

        with patch.object(
            engine, "_trigger_incremental_recompile", new=AsyncMock(return_value=False)
        ):
            await engine.submit_answer(
                db_session, session["session_id"], q["question_id"], "这是精确的回答内容"
            )

        from sqlalchemy import select
        q_result = await db_session.execute(
            select(InterviewQuestion).where(InterviewQuestion.id == q["question_id"])
        )
        question = q_result.scalar_one()
        assert question.answer == "这是精确的回答内容"
        assert question.answered_at is not None

    async def test_submit_answer_to_completed_session_raises(
        self, db_session, v3_tables
    ):
        """向已完成的会话提交回答抛出 ValueError。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        # 手动将会话标记为完成
        from sqlalchemy import update
        await db_session.execute(
            update(InterviewSession)
            .where(InterviewSession.id == session["session_id"])
            .values(status="completed")
        )
        await db_session.commit()

        with pytest.raises(ValueError, match="已完成"):
            await engine.submit_answer(
                db_session, session["session_id"], "any-qid", "回答"
            )


# ============================================================
# get_session_status 测试
# ============================================================


class TestGetSessionStatus:
    async def test_initial_session_status(self, db_session, v3_tables):
        """新会话初始状态：answered_count=0，completeness=0。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        status = await engine.get_session_status(db_session, session["session_id"])

        assert status["status"] == "active"
        assert status["answered_count"] == 0
        assert status["total_count"] == question_bank.total
        assert status["completeness"] == 0.0

    async def test_status_after_one_answer(self, db_session, v3_tables):
        """回答 1 个问题后状态更新。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        q = await engine.get_next_question(db_session, session["session_id"])
        with patch.object(
            engine, "_trigger_incremental_recompile", new=AsyncMock(return_value=False)
        ):
            await engine.submit_answer(
                db_session, session["session_id"], q["question_id"], "回答"
            )

        status = await engine.get_session_status(db_session, session["session_id"])
        assert status["answered_count"] == 1
        assert status["completeness"] > 0

    async def test_status_invalid_session_raises(self, db_session, v3_tables):
        """不存在的会话抛出 ValueError。"""
        engine = InterviewEngine()
        with pytest.raises(ValueError, match="不存在"):
            await engine.get_session_status(db_session, "invalid-id")


# ============================================================
# compute_completeness 测试
# ============================================================


class TestComputeCompleteness:
    async def test_completeness_0_when_no_answers(self, db_session, v3_tables):
        """无回答时完成度为 0。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        from sqlalchemy import select
        sess_result = await db_session.execute(
            select(InterviewSession).where(InterviewSession.id == session["session_id"])
        )
        sess = sess_result.scalar_one()
        completeness = await engine.compute_completeness(db_session, sess)
        assert completeness == 0.0

    async def test_completeness_100_when_all_answered(self, db_session, v3_tables):
        """全部回答后完成度为 100。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        # 全部回答
        from sqlalchemy import update
        await db_session.execute(
            update(InterviewQuestion)
            .where(InterviewQuestion.session_id == session["session_id"])
            .values(answer="已回答")
        )
        from sqlalchemy import select
        sess_result = await db_session.execute(
            select(InterviewSession).where(InterviewSession.id == session["session_id"])
        )
        sess = sess_result.scalar_one()
        sess.answered_count = sess.total_count
        await db_session.commit()

        completeness = await engine.compute_completeness(db_session, sess)
        assert completeness == 100.0

    async def test_p0_contributes_more_than_p2(self, db_session, v3_tables):
        """P0 问题对完成度贡献大于 P2。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        # 仅回答 1 个 P0
        from sqlalchemy import select, update
        sess_result = await db_session.execute(
            select(InterviewSession).where(InterviewSession.id == session["session_id"])
        )
        sess = sess_result.scalar_one()

        # 找一个 P0 和一个 P2
        q_result = await db_session.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.session_id == session["session_id"],
                InterviewQuestion.priority == "P0",
            ).limit(1)
        )
        p0_q = q_result.scalar_one()
        p0_q.answer = "回答"
        await db_session.commit()
        c_p0 = await engine.compute_completeness(db_session, sess)

        # 重置
        p0_q.answer = None
        await db_session.commit()

        # 仅回答 1 个 P2
        q2_result = await db_session.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.session_id == session["session_id"],
                InterviewQuestion.priority == "P2",
            ).limit(1)
        )
        p2_q = q2_result.scalar_one()
        p2_q.answer = "回答"
        await db_session.commit()
        c_p2 = await engine.compute_completeness(db_session, sess)

        assert c_p0 > c_p2


# ============================================================
# 增量重编译触发测试
# ============================================================


class TestIncrementalRecompile:
    async def test_recompile_skipped_when_no_compilation_job(
        self, db_session, v3_tables
    ):
        """无编译任务时跳过重编译（返回 False）。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        from sqlalchemy import select
        sess_result = await db_session.execute(
            select(InterviewSession).where(InterviewSession.id == session["session_id"])
        )
        sess = sess_result.scalar_one()

        q_result = await db_session.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.session_id == session["session_id"]
            ).limit(1)
        )
        question = q_result.scalar_one()

        triggered = await engine._trigger_incremental_recompile(
            db_session, sess, question
        )
        assert triggered is False

    async def test_submit_answer_does_not_fail_when_recompile_errors(
        self, db_session, v3_tables
    ):
        """重编译失败不阻断访谈主流程。"""
        eid, uid = await _seed_enterprise_and_user(db_session)
        engine = InterviewEngine()
        session = await engine.start_session(db_session, eid, uid)

        q = await engine.get_next_question(db_session, session["session_id"])

        # mock 重编译抛出异常
        with patch.object(
            engine, "_trigger_incremental_recompile",
            new=AsyncMock(side_effect=RuntimeError("重编译失败")),
        ):
            result = await engine.submit_answer(
                db_session, session["session_id"], q["question_id"], "回答"
            )

        # 重编译失败被捕获，recompile_triggered=False，回答仍记录
        assert result["recompile_triggered"] is False
        assert result["updated_completeness"] > 0
