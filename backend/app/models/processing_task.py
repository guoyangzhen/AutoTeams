"""处理任务模型。

之前 process.py 的 _tasks 字典存在内存里，且 processed_files 直接赋值为
agent.file_count，失败文件也算已处理，knowledge_count 硬编码为 0。
本模型持久化处理任务并记录真实的处理进度。
"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base import TimestampMixin


class ProcessingTask(Base, TimestampMixin):
    __tablename__ = "processing_tasks"

    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    enterprise_id = Column(String(36), ForeignKey("enterprises.id"), nullable=False, index=True)
    # BE-REL-02: Agent 删除后保留任务历史，但解除关联
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    folder_path = Column(Text, nullable=False)
    agent_name = Column(String(255), nullable=True)
    agent_description = Column(Text, nullable=True)
    # pending / scanning / processing / vectorizing / completed / failed
    status = Column(String(32), default="pending", nullable=False, index=True)
    # 进度 0.0 ~ 1.0
    progress = Column(Float, default=0.0, nullable=False)
    # 当前阶段提示信息（如 "正在扫描文件夹..."）
    message = Column(Text, nullable=True)

    # 真实的文件计数
    total_files = Column(Integer, default=0, nullable=False)
    processed_files = Column(Integer, default=0, nullable=False)
    failed_files = Column(Integer, default=0, nullable=False)
    # 真实的知识片段数
    knowledge_count = Column(Integer, default=0, nullable=False)
    # 处理耗时（秒）
    processing_time_seconds = Column(Float, default=0.0, nullable=False)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    # 错误日志：[{file, error, timestamp}]
    error_log = Column(JSON, nullable=True, default=list)

    # ------------------------------------------------------------
    # 耐久队列租约（AUD-17）
    # ------------------------------------------------------------
    # 文档处理此前靠进程内 `asyncio.create_task` 执行：请求进不同进程就取消不到
    # 原任务，滚动更新启动任一实例又会把所有 `processing` 任务标成 failed，
    # 误伤仍由其他实例持有的任务。改为数据库租约后：
    #   - 任意 worker 都能领取 pending 任务；
    #   - 崩溃的任务在租约过期后可被重新投递；
    #   - 租约仍然有效的任务不会被其他实例动。
    lease_owner = Column(String(128), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    #: 投递次数：崩溃后重投时递增，幂等执行据此避免重复副作用
    attempt = Column(Integer, default=0, nullable=False, server_default="0")
    #: 跨进程取消信号：API 只写库，worker 观察它并协作式停止
    cancel_requested = Column(Boolean, default=False, nullable=False, server_default="false")
    #: 发起任务时使用的配置（model / skills / index_strategy），重投时按原配置执行
    worker_id = Column(String(128), nullable=True)
    #: 幂等键：同一 (enterprise, folder, agent_name) 的重复提交只执行一次
    idempotency_key = Column(String(128), nullable=True, index=True)

    #: 由外部执行器写入的稳定标识，用于判定"这次重投是否已经产生过副作用"
    external_run_id = Column(String(128), nullable=True)

    # 任务配置 JSON（重投时需要与首次完全一致）
    run_config = Column(JSON, nullable=True, default=dict)

    user = relationship("User")
    enterprise = relationship("Enterprise")
    agent = relationship("Agent", back_populates="processing_tasks")

    def to_dict(self) -> dict:
        return {
            "task_id": self.id,
            "status": self.status,
            "progress": self.progress or 0.0,
            "message": self.message or "",
            "folder_path": self.folder_path,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_description": self.agent_description,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "failed_files": self.failed_files,
            "knowledge_count": self.knowledge_count,
            "processing_time_seconds": self.processing_time_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_log": self.error_log or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
