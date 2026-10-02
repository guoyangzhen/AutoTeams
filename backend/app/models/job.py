"""统一后台任务模型（BackgroundJob）。

依据重构计划 §4.2.3「合并 ``CompilationJob``、``ProcessingTask`` 为统一的
``BackgroundJob`` 模型」。

两者的差异与统一方式：

======================  ==============================  ==============================
维度                    CompilationJob                  ProcessingTask
======================  ==============================  ==============================
状态机                  queued/running/completed/      pending/processing/vectorizing/
                        failed/cancelled               completed/failed
租约                    有（lease_owner/lease_until）  无
进度                    progress 0.0–1.0              progress 0.0–1.0
输入                    folder_path / interview_...    folder_path
阶段叙事                stage                          message
======================  ==============================  ==============================

统一后的约定：

- **status 是粗粒度状态机**（queued/running/completed/failed/cancelled），
  只表达「这个任务处在哪个生命周期节点」；
- **message 承载阶段叙事**（原来 ProcessingTask 用
  pending/scanning/processing/vectorizing 表达细粒度阶段）——把它塞进
  status 会让状态机与展示文案耦合，换个文案就要改状态机取值域；
- **lease 块整体保留**。它不是冗余：``services/compiler/job_queue.py`` 与
  ``pipeline.py`` 通过租约原子领取任务、租约超时接管、heartbeat 防误回收，
  ``api/agent_product.py`` 读 ``cancel_requested``。若按「最小核心字段」
  重列，这套机制会被静默丢弃，Worker 重启后长任务将无人接管。

⚠️ 本提交**只新增模型，不做数据迁移**。把既有两张表的数据并入新表属于
破坏性迁移，需单独排期并配套回滚方案；在此之前两张旧表继续被现有代码使用，
新模型尚未接入任何读写路径。
"""
from __future__ import annotations

from datetime import timezone
from typing import Dict

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base
from app.models.base import TimestampMixin

from app.utils.time import utcnow


class JobType:
    """任务类型（``job_type`` 列取值域）。

    用常量类而非 ``Enum`` 列，与本仓既有做法一致（``status`` 等列均为
    ``String`` + 注释约定取值，DB 层无 ``Enum`` / ``CHECK`` 约束）。
    """

    COMPILATION = "compilation"                  # 源：CompilationJob
    DOCUMENT_PROCESSING = "document_processing"  # 源：ProcessingTask


class JobStatus:
    """任务状态（``status`` 列取值域）。"""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: 允许的状态跃迁。用于自检与测试守护——非法跃迁是后台任务最常见的
#: 静默 bug（重复领取已完成任务、被取消的任务仍写结果）。
ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}),
    # 终态不再迁出：进程重启后误改终态是最难排查的一类数据损坏
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

#: 终态集合：任务到达这些状态后不应再被 worker 领取
TERMINAL_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class BackgroundJob(Base, TimestampMixin):
    """统一后台任务表。

    合并自 ``compilation_jobs``（五级编译器任务）与 ``processing_tasks``
    （文档处理任务），为独立 Celery Worker 提供统一的持久化任务面。
    """

    __tablename__ = "background_jobs"

    # -- 归属 --
    enterprise_id = Column(String(36), nullable=False, index=True)
    # 源：ProcessingTask.user_id。CompilationJob 无此列，故允许为空
    #（编译任务可由后台调度触发，无前台用户）。
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)

    # -- 类型与状态 --
    job_type = Column(String(32), nullable=False, index=True)
    # server_default 必需：存在绕过 ORM 的直接插入路径（如 seed 脚本），
    # 仅靠 Python 侧 default 会在 NOT NULL 约束上失败。
    status = Column(String(16), nullable=False, default=JobStatus.QUEUED, server_default="queued", index=True)

    # -- 进度与叙事 --
    # 真实进度 0.0–1.0，由 Worker 逐阶段写入，替代前端「阶段数/5」式估算。
    progress = Column(Float, nullable=False, default=0.0, server_default="0.0")
    # 当前阶段的人话提示（如「正在扫描文件夹…」）。细粒度阶段语义归这里，
    # 不进 status，避免状态机与展示文案耦合。
    message = Column(Text, nullable=True)

    # -- 输入与产出 --
    # 输入快照：Worker 重启或接管时不依赖原始 HTTP 请求的内存对象。
    payload = Column(JSON, nullable=False, default=dict)
    # 产出结果。结构随 job_type 而异（编译产物 / 知识库统计），故不强约束。
    result = Column(JSON, nullable=True)
    # 错误明细 [{file, error, timestamp}]，与 ProcessingTask 一致。
    error_log = Column(JSON, nullable=False, default=list)

    # -- 租约（源：CompilationJob，worker 领取/接管所依赖）--
    # 同一企业、同一输入在活跃窗口内复用同一 Job，避免重复执行。
    idempotency_key = Column(String(128), nullable=True, index=True)
    # Worker 领取租约；租约超时的 running Job 可被新 Worker 接管，
    # heartbeat 防止长任务被误回收。
    lease_owner = Column(String(128), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    # 已尝试次数，用于退避重试上限。
    attempt = Column(Integer, nullable=False, default=0, server_default="0")
    # 协作式取消：置位后由 worker 在下一个检查点响应，避免强杀进程。
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default="false")

    # -- 时间戳 --
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def can_transition_to(self, target: str) -> bool:
        """该任务当前状态是否允许迁移到 ``target``。"""
        return target in ALLOWED_TRANSITIONS.get(self.status, frozenset())

    def is_terminal(self) -> bool:
        """是否处于终态（不再被 worker 领取，也不应再变更结果）。"""
        return self.status in TERMINAL_STATUSES

    def is_lease_expired(self, now=None) -> bool:
        """租约是否已过期（可被其他 worker 接管）。

        无租约（``lease_until`` 为空）不算过期——那是尚未被领取的 queued 任务。

        ⚠️ 必须做时区归一：从 DB 读回的 ``lease_until`` 在 SQLite 下是 **naive**
        datetime，而 ``utcnow()`` 是 aware，直接比较会抛
        ``TypeError: can't compare offset-naive and offset-aware datetimes``。
        本仓已有同源修复先例：migrations/versions/
        2026_08_09_0900-b0c1d2e3f4a5_fix_naive_timestamps_to_timestamptz.py。
        """
        if self.lease_until is None:
            return False
        deadline = self.lease_until
        reference = now or utcnow()
        if deadline.tzinfo is None and reference.tzinfo is not None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        elif deadline.tzinfo is not None and reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return deadline < reference

    def __repr__(self) -> str:
        return f"<BackgroundJob id={self.id} type={self.job_type} status={self.status}>"
