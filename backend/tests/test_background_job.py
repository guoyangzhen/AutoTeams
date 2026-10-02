"""统一后台任务模型（BackgroundJob）测试。

覆盖两类：
1. **模型映射** —— 表名、列、默认值、ORM 注册；
2. **状态机转换** —— 允许/禁止的跃迁、终态判定、租约过期。

状态机用真实数据库会话验证，不只测纯函数——``can_transition_to`` 读的是实例上的
``status`` 列，脱离 DB 断言就测不出「改完代码忘了落库」这类问题。
"""
import pytest
from sqlalchemy import select

from app.models.job import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    BackgroundJob,
    JobStatus,
    JobType,
)
from app.utils.time import utcnow


# ---------------------------------------------------------------------------
# 1. 模型映射
# ---------------------------------------------------------------------------

class TestModelMapping:
    def test_table_name(self):
        assert BackgroundJob.__tablename__ == "background_jobs"

    def test_registered_in_orm_package(self):
        """未在 app.models 注册的模型不会进 Base.metadata，测试建表时直接缺表。"""
        import app.models as models_pkg

        assert hasattr(models_pkg, "BackgroundJob")
        assert models_pkg.BackgroundJob is BackgroundJob

    def test_table_in_metadata(self):
        from app.database import Base

        assert "background_jobs" in Base.metadata.tables

    def test_all_specified_columns_present(self):
        """任务书点名的 12 个字段一个都不能少。"""
        cols = {c.name for c in BackgroundJob.__table__.columns}
        required = {
            "id", "enterprise_id", "user_id", "job_type", "status",
            "progress", "message", "payload", "result", "error_log",
            "started_at", "completed_at", "created_at",
        }
        assert required <= cols, f"缺失: {required - cols}"

    def test_lease_block_preserved(self):
        """租约块必须保留：compiler/job_queue.py 与 agent_build_queue.py 依赖它领取任务。

        按「最小核心字段」重列会静默丢掉这套机制，Worker 重启后长任务将无人接管。
        """
        cols = {c.name for c in BackgroundJob.__table__.columns}
        assert {
            "idempotency_key", "lease_owner", "lease_until",
            "heartbeat_at", "attempt", "cancel_requested",
        } <= cols

    def test_job_type_column_is_indexed(self):
        """按类型捞任务是主要查询场景，缺索引会全表扫。"""
        col = BackgroundJob.__table__.columns["job_type"]
        assert col.index is True

    def test_status_column_is_indexed(self):
        assert BackgroundJob.__table__.columns["status"].index is True

    def test_progress_is_not_nullable_with_server_default(self):
        """server_default 必需：存在绕过 ORM 的直接插入路径，仅 Python 侧 default 会触发 NOT NULL 失败。"""
        col = BackgroundJob.__table__.columns["progress"]
        assert col.nullable is False
        assert col.server_default is not None

    def test_json_columns_have_container_defaults(self):
        """payload/error_log 必须默认成空容器。

        断言的是**工厂行为**（调用后得到什么类型），而非 callable 身份——
        SQLAlchemy 会把 ``default=dict`` 包一层，直接 ``is dict`` 并不成立。
        """
        for name, expected in (("payload", dict), ("error_log", list)):
            col = BackgroundJob.__table__.columns[name]
            assert col.nullable is False
            assert col.default is not None and col.default.arg is not None, name
            assert col.default.arg.__name__ == expected.__name__, name

    def test_user_id_optional(self):
        """编译任务可由后台调度触发、无前台用户，故 user_id 必须可空。"""
        assert BackgroundJob.__table__.columns["user_id"].nullable is True

    def test_job_type_and_status_values(self):
        assert JobType.COMPILATION == "compilation"
        assert JobType.DOCUMENT_PROCESSING == "document_processing"
        assert JobStatus.QUEUED == "queued"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.FAILED == "failed"
        assert JobStatus.CANCELLED == "cancelled"


# ---------------------------------------------------------------------------
# 2. 状态机
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_terminal_states_have_no_outgoing_edges(self):
        """终态迁出是后台任务最隐蔽的数据损坏：进程重启后误改终态极难排查。"""
        for status in TERMINAL_STATUSES:
            assert ALLOWED_TRANSITIONS[status] == frozenset(), status

    def test_queued_can_start_or_cancel(self):
        assert BackgroundJob(status=JobStatus.QUEUED).can_transition_to(JobStatus.RUNNING)
        assert BackgroundJob(status=JobStatus.QUEUED).can_transition_to(JobStatus.CANCELLED)
        assert not BackgroundJob(status=JobStatus.QUEUED).can_transition_to(JobStatus.COMPLETED)

    def test_running_can_finish_fail_or_cancel(self):
        job = BackgroundJob(status=JobStatus.RUNNING)
        assert job.can_transition_to(JobStatus.COMPLETED)
        assert job.can_transition_to(JobStatus.FAILED)
        assert job.can_transition_to(JobStatus.CANCELLED)
        assert not job.can_transition_to(JobStatus.QUEUED)

    def test_terminal_rejects_everything(self):
        for status in TERMINAL_STATUSES:
            job = BackgroundJob(status=status)
            for target in JobStatus.__dict__.values():
                if isinstance(target, str) and target.startswith(("queued", "running", "completed", "failed", "cancelled")):
                    assert not job.can_transition_to(target), (status, target)

    def test_is_terminal(self):
        assert BackgroundJob(status=JobStatus.COMPLETED).is_terminal()
        assert BackgroundJob(status=JobStatus.FAILED).is_terminal()
        assert BackgroundJob(status=JobStatus.CANCELLED).is_terminal()
        assert not BackgroundJob(status=JobStatus.QUEUED).is_terminal()
        assert not BackgroundJob(status=JobStatus.RUNNING).is_terminal()

    def test_unknown_status_is_not_terminal_and_blocks_transitions(self):
        """脏状态不应被误判为终态（那会让任务永远卡住）。"""
        job = BackgroundJob(status="weird_value")
        assert not job.is_terminal()
        assert not job.can_transition_to(JobStatus.RUNNING)


# ---------------------------------------------------------------------------
# 3. 租约
# ---------------------------------------------------------------------------

class TestLease:
    def test_no_lease_is_not_expired(self):
        """无租约 = 尚未被领取的 queued 任务，不能判为过期。"""
        assert BackgroundJob(lease_until=None).is_lease_expired() is False

    def test_future_lease_not_expired(self):
        from datetime import timedelta

        job = BackgroundJob(lease_until=utcnow() + timedelta(minutes=5))
        assert job.is_lease_expired() is False

    def test_past_lease_expired(self):
        from datetime import timedelta

        job = BackgroundJob(lease_until=utcnow() - timedelta(minutes=1))
        assert job.is_lease_expired() is True


# ---------------------------------------------------------------------------
# 4. 真实落库往返
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPersistence:
    async def test_round_trip_defaults(self, db_session):
        job = BackgroundJob(enterprise_id="ent-1", job_type=JobType.COMPILATION)
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        assert job.id  # TimestampMixin 生成
        assert job.status == JobStatus.QUEUED
        assert job.progress == 0.0
        assert job.payload == {}
        assert job.error_log == []
        assert job.result is None
        assert job.attempt == 0
        assert job.cancel_requested is False
        assert job.created_at is not None
        assert job.lease_until is None

    async def test_state_change_persists(self, db_session):
        """状态跃迁必须真的落库——``can_transition_to`` 读的是列值。"""
        job = BackgroundJob(enterprise_id="ent-2", job_type=JobType.DOCUMENT_PROCESSING)
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        assert job.can_transition_to(JobStatus.RUNNING)
        job.status = JobStatus.RUNNING
        await db_session.commit()
        await db_session.refresh(job)

        assert job.status == JobStatus.RUNNING
        assert job.can_transition_to(JobStatus.COMPLETED)

        job.status = JobStatus.COMPLETED
        await db_session.commit()
        await db_session.refresh(job)
        assert job.is_terminal()

    async def test_payload_and_error_log_round_trip(self, db_session):
        job = BackgroundJob(
            enterprise_id="ent-3",
            job_type=JobType.DOCUMENT_PROCESSING,
            payload={"folder_path": "uploads/x"},
            error_log=[{"file": "a.pdf", "error": "解析失败"}],
            result={"knowledge_count": 12},
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        assert job.payload["folder_path"] == "uploads/x"
        assert job.error_log[0]["file"] == "a.pdf"
        assert job.result["knowledge_count"] == 12

    async def test_query_by_type_and_status(self, db_session):
        """按类型 + 状态捞任务是主要查询路径。"""
        db_session.add_all([
            BackgroundJob(enterprise_id="ent-4", job_type=JobType.COMPILATION, status=JobStatus.QUEUED),
            BackgroundJob(enterprise_id="ent-4", job_type=JobType.DOCUMENT_PROCESSING, status=JobStatus.RUNNING),
        ])
        await db_session.commit()

        stmt = select(BackgroundJob).where(
            BackgroundJob.enterprise_id == "ent-4",
            BackgroundJob.status == JobStatus.QUEUED,
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].job_type == JobType.COMPILATION

    async def test_lease_persists(self, db_session):
        from datetime import timedelta

        until = utcnow() - timedelta(seconds=1)
        job = BackgroundJob(
            enterprise_id="ent-5", job_type=JobType.COMPILATION,
            lease_owner="worker-1", lease_until=until, attempt=2,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        assert job.lease_owner == "worker-1"
        assert job.attempt == 2
        assert job.is_lease_expired() is True
