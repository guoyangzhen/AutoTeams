"""AutoTeams 5.0 · 战役 3：具身物理执行器 2.0 持久化模型（AUD-04 / AUD-18）。

历史实现把设备档案、任务账本、视窗帧、双因子挑战与审计留痕全部放在
``app/services/runner_v2_protocol.py`` 的进程内字典里，后果有二：

1. **AUD-04**：设备档案没有企业归属，任何拿到机器密钥的终端都能被任意租户
   下发任务；确认码也随心跳广播给所有在线设备。
2. **AUD-18**：进程重启即丢失全部任务与结果，端侧重试无法判断动作是否已经执行。

本模块把这些状态落成普通 SQLAlchemy 模型（async SQLAlchemy 2.x）：

- ``RunnerDevice``           设备档案，**注册时固化 enterprise_id / owner_user_id**
- ``RunnerDeviceCredential`` 设备长期凭据（只存哈希，可逐设备轮换与撤销）
- ``RunnerDeviceToken``      设备短期访问令牌（只存哈希，默认 15 分钟）
- ``RunnerTask``             物理任务账本（租户 + 设备双重绑定）
- ``RunnerTaskFrame``        视窗灰度帧（按任务环形上界保留）
- ``RunnerChallenge``        高危操作双因子挑战（确认码密文落库，仅投递给属主设备）
- ``RunnerAuditEntry``       操作审计留痕（凭据已脱敏）
- ``RunnerDeviceCommand``    云端 → 设备的一次性指令（端侧工具桥接的执行载体）

安全要点：
- 任何机器调用都必须「先解析设备令牌 → 取得 device → 校验 task.device_id == device.id」，
  跨租户访问统一按「不存在」处理，不泄露资源是否存在。
- 长期凭据与短期令牌都只保存 SHA-256 哈希，数据库泄露无法直接冒充设备。
- 确认码使用 Fernet 密文落库（``app.utils.credential_crypto``），只在投递给
  属主设备时解密；云端视图永不暴露。
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base
from app.models.base import TimestampMixin
from app.utils.time import utcnow


def _new_id() -> str:
    return str(uuid.uuid4())


class RunnerDevice(Base, TimestampMixin):
    """端侧物理执行设备档案。

    ``enterprise_id`` / ``owner_user_id`` 在注册时写入后**永不改变**：
    设备不通过请求体自称企业，租户边界由服务端档案决定。
    """

    __tablename__ = "runner_devices"

    id = Column(String(36), primary_key=True, default=_new_id)
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 端侧自报的稳定标识（同一企业内唯一）
    runner_id = Column(String(96), nullable=False)
    device_label = Column(String(128), nullable=True)
    #: **服务端授权**的作用域集合；心跳自报 scopes 只作回显，不参与授权
    scopes = Column(JSON, nullable=False, default=list)
    platform = Column(String(32), nullable=False, default="unknown")
    version = Column(String(32), nullable=False, default="0.0.0")
    capabilities = Column(JSON, nullable=False, default=dict)
    #: active / revoked
    status = Column(String(16), nullable=False, default="active")
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("enterprise_id", "runner_id", name="uq_runner_devices_enterprise_runner"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerDevice id={self.id} runner_id={self.runner_id} status={self.status}>"


class RunnerDeviceCredential(Base, TimestampMixin):
    """设备长期凭据（只存哈希）。

    ``secret_hash`` 是 SHA-256(secret)；撤销即置 ``revoked_at``，
    撤销单台设备不需要轮换任何全局密钥，也不影响其他设备。
    """

    __tablename__ = "runner_device_credentials"

    id = Column(String(36), primary_key=True, default=_new_id)
    device_id = Column(
        String(36), ForeignKey("runner_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secret_hash = Column(String(64), nullable=False, unique=True, index=True)
    label = Column(String(64), nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerDeviceCredential id={self.id} device_id={self.device_id}>"


class RunnerDeviceToken(Base, TimestampMixin):
    """设备短期访问令牌（只存哈希，默认 15 分钟）。

    端侧先用长期凭据换短期令牌，之后所有机器调用携带该令牌；
    令牌不可撤销以外的任何全局密钥，撤销设备即批量作废其令牌。
    """

    __tablename__ = "runner_device_tokens"

    id = Column(String(36), primary_key=True, default=_new_id)
    device_id = Column(
        String(36), ForeignKey("runner_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    issued_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerDeviceToken id={self.id} device_id={self.device_id}>"


class RunnerTask(Base):
    """物理任务账本（企业 + 设备双重绑定）。

    主键是业务键 ``task_id``，因此不继承带 ``id`` 的 TimestampMixin，
    只保留其时间戳语义。
    """

    __tablename__ = "runner_tasks"

    task_id = Column(String(40), primary_key=True)
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id = Column(
        String(36), ForeignKey("runner_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel = Column(String(32), nullable=False)
    #: blocked / awaiting_2fa / dispatched / executing / completed / failed / cancelled
    state = Column(String(24), nullable=False, default="dispatched", index=True)
    verdict = Column(JSON, nullable=False, default=dict)
    steps = Column(JSON, nullable=False, default=list)
    created_by = Column(String(64), nullable=False, default="")
    challenge_id = Column(String(40), nullable=True, index=True)
    result = Column(JSON, nullable=True)
    #: 最近一次被接受的端侧回执 ID（重连重发同一回执时不重复记账）
    last_receipt_id = Column(String(64), nullable=True)
    frame_seq = Column(Integer, nullable=False, default=0)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (Index("ix_runner_tasks_device_state", "device_id", "state"),)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerTask task_id={self.task_id} state={self.state}>"


class RunnerTaskFrame(Base):
    """视窗灰度帧（按任务环形上界保留，见协议层 FRAME_RETENTION）。"""

    __tablename__ = "runner_task_frames"

    id = Column(String(36), primary_key=True, default=_new_id)
    task_id = Column(
        String(40), ForeignKey("runner_tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    digest = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False)
    payload_b64 = Column(Text, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("task_id", "seq", name="uq_runner_task_frames_task_seq"),)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerTaskFrame task_id={self.task_id} seq={self.seq}>"


class RunnerChallenge(Base):
    """高危操作双因子确认挑战。

    ``device_code_encrypted`` 是 Fernet 密文：只有通过设备令牌鉴权、且
    ``device_id`` 与本行一致的端侧心跳才能解密并收到该码。

    主键是业务键 ``challenge_id``，因此不继承带 ``id`` 的 TimestampMixin。
    """

    __tablename__ = "runner_challenges"

    challenge_id = Column(String(40), primary_key=True)
    task_id = Column(
        String(40), ForeignKey("runner_tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id = Column(
        String(36), ForeignKey("runner_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: pending_endpoint_confirmation / pending_device_code / approved / rejected / expired
    state = Column(String(32), nullable=False, default="pending_endpoint_confirmation", index=True)
    reason = Column(Text, nullable=False, default="")
    device_code_encrypted = Column(Text, nullable=False)
    endpoint_confirmed_by = Column(String(64), nullable=True)
    endpoint_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    device_verified_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerChallenge id={self.challenge_id} state={self.state}>"


class RunnerAuditEntry(Base):
    """操作审计留痕（凭据已脱敏，保留期见协议层 AUDIT_RETENTION_DAYS）。"""

    __tablename__ = "runner_audit_entries"

    trace_id = Column(String(40), primary_key=True)
    enterprise_id = Column(String(36), nullable=True, index=True)
    task_id = Column(String(40), nullable=True, index=True)
    device_id = Column(String(36), nullable=True, index=True)
    runner_id = Column(String(96), nullable=True)
    actor = Column(String(64), nullable=False, default="")
    event = Column(String(64), nullable=False, index=True)
    decision = Column(String(64), nullable=False, default="")
    detail = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerAuditEntry trace_id={self.trace_id} event={self.event}>"


class RunnerDeviceCommand(Base, TimestampMixin):
    """云端 → 设备的一次性指令（端侧工具桥接的执行载体）。

    MCP 工具（``runner_read_file`` / ``runner_system_probe``）不再触碰后端
    文件系统：它们只负责校验设备归属并入队一条指令，端侧在下一次心跳时领取
    并回传结果。
    """

    __tablename__ = "runner_device_commands"

    id = Column(String(36), primary_key=True, default=_new_id)
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id = Column(
        String(36), ForeignKey("runner_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(String(36), nullable=False, default="")
    #: read_file / system_probe
    operation = Column(String(32), nullable=False)
    relative_path = Column(String(512), nullable=True)
    max_bytes = Column(Integer, nullable=True)
    #: pending / completed / failed / expired
    state = Column(String(16), nullable=False, default="pending", index=True)
    result = Column(JSON, nullable=True)
    error = Column(String(512), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_runner_device_commands_device_state", "device_id", "state"),)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RunnerDeviceCommand id={self.id} op={self.operation} state={self.state}>"


__all__ = [
    "RunnerDevice",
    "RunnerDeviceCredential",
    "RunnerDeviceToken",
    "RunnerTask",
    "RunnerTaskFrame",
    "RunnerChallenge",
    "RunnerAuditEntry",
    "RunnerDeviceCommand",
]
