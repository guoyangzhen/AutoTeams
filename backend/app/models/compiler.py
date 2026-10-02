"""编译器数据模型。

对应 PRD §4.3 五级编译器 + 完成度评估框架。
- CompilationJob：编译任务记录（五级编译的每一级执行记录）
- CompilationArtifact：编译产物存储（JSONB 存储各级产出）
"""
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Float, Text, ForeignKey

from app.database import Base
from app.models.base import TimestampMixin
from app.models.cognition import JSON


class CompilationJob(Base, TimestampMixin):
    """编译任务（PRD §4.3 五级编译器 + §10.7 API 端点）。

    记录每次五级编译的执行状态、当前层级、置信度、完成度等。
    一个 enterprise 可有多个 compilation job（全量编译 + 增量重编译）。
    """
    __tablename__ = "compilation_jobs"

    enterprise_id = Column(String(36), nullable=False, index=True)
    trigger_source = Column(String(64), nullable=False, default="manual")
    # 当前编译层级：information/knowledge/process/capability/runtime/complete
    stage = Column(String(32), nullable=False, default="information")
        # 任务状态：queued/running/completed/failed/cancelled
    # queued 由 API 持久化提交，独立 Worker 通过租约原子领取后转换为 running。
    status = Column(String(16), nullable=False, default="queued", index=True)
    # 任务输入快照：Worker 重启或接管时无需依赖原始 HTTP 请求内存。
    folder_path = Column(Text, nullable=True)
    interview_completion = Column(Float, nullable=False, default=0.0, server_default="0.0")
    # 同一企业、同一输入在活跃窗口内复用同一 Job，避免重复编译和 Runtime 版本冲突。
    idempotency_key = Column(String(128), nullable=True, index=True)
    # Worker 领取租约。租约超时的 running Job 可被新 Worker 接管；heartbeat 防止长任务误回收。
    lease_owner = Column(String(128), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    attempt = Column(Integer, nullable=False, default=0, server_default="0")
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default="false")

    # 受影响的编译层级（增量重编译用，逗号分隔）
    affected_stages = Column(String(256), nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    completeness = Column(Float, nullable=False, default=0.0)
    # 真实编译进度 0.0-1.0（Pipeline 逐级写入，替代前端「阶段数/5」估算）
    # server_default 必需：测试用 create_all 建表且存在非 ORM 插入路径，
    # 仅靠 Python 侧 default 会触发 NOT NULL 约束失败
    progress = Column(Float, nullable=False, default=0.0, server_default="0.0")
    # 当前阶段开始时间（用于阶段耗时统计与剩余时间预估）
    stage_started_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class CompilationArtifact(Base, TimestampMixin):
    """编译产物（五级编译器每级的产出存储）。

    每个 artifact 对应某一级编译器的输出（JSONB），供后续层级消费。
    例如：information 级产出 → knowledge 级输入。
    """
    __tablename__ = "compilation_artifacts"

    enterprise_id = Column(String(36), nullable=False, index=True)
    job_id = Column(String(36), ForeignKey("compilation_jobs.id"), nullable=False, index=True)
    # 编译层级：information/knowledge/process/capability/runtime
    stage = Column(String(32), nullable=False)
    # 产出数据（JSONB）
    output = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, nullable=False, default=0.0)
    # 该层级发现的内容摘要（供编译动画展示）
    discovered_summary = Column(Text, nullable=True)
    # 该层级实际耗时（毫秒）—— 回放模式按真实时序播放的依据
    duration_ms = Column(Integer, nullable=True)
