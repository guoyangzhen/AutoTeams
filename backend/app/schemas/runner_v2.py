"""AutoTeams 5.0 · 战役 3：具身物理执行器 2.0 请求/响应模型（Local Runner 2.0）。

字段命名约定（AUD-10）
---------------------
**线上契约统一使用 snake_case**。端侧 ``local-runner/src/physical-bridge.ts``
与 ``physical.ts`` 的 ViewportFrame 字段已同步改为 ``payload_b64``；
本模块**不设 Pydantic alias**，也不同时接受两种拼写——同时接受会让契约错误
在生产里静默通过。两侧一致性由以下两侧测试共同保证：

- ``backend/tests/test_runner_v2_contract.py``：把端侧实际发出的 JSON fixture
  喂给生产 Pydantic 模型，并反向断言模型字段集合与端侧发送字段集合完全一致。
- ``local-runner/test/contract.test.ts``：断言端侧序列化出的键名与
  ``local-runner/contract/runner_v2_payloads.json`` 中的共享 fixture 一致。

``capabilities`` 是结构化对象（``browser`` / ``desktop`` / ``frames``），
其中 ``frames`` 为 ``{width, height, format}``，不再是 ``dict[str, bool]``。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: 单任务允许的最大 step 数（与端侧契约一致，避免无界任务体）。
MAX_STEPS_PER_TASK = 60


class PhysicalStepModel(BaseModel):
    """一条具身物理指令。

    字段语义与端侧 ``local-runner/src/physical.ts`` 的 ``PhysicalStep`` 一一对应：
    - ``op``：操作类型（按通道白名单校验，见 protocol.CHANNEL_OPS）
    - ``target``：CSS 选择器 / 无障碍节点路径
    - ``text``：录入正文（**严禁**携带凭据，护栏规则一）
    - ``credential_ref``：Fernet 凭据机代填引用（如 ``vault://erp/prod/password``）
    - ``rows``：表格批量录入行
    - ``keys``：受控输入模拟按键序列
    - ``url``：导航目标（仅 http/https）
    - ``risk``：显式风险标记（``high`` 触发双因子确认）
    - ``step_id``：端侧幂等回执使用的稳定 step 标识（由云端生成）
    """

    model_config = ConfigDict(extra="forbid")

    op: str = Field(..., min_length=1, max_length=32)
    target: Optional[str] = Field(default=None, max_length=512)
    text: Optional[str] = Field(default=None, max_length=4096)
    credential_ref: Optional[str] = Field(default=None, max_length=256)
    rows: Optional[list[dict[str, str]]] = Field(default=None)
    keys: Optional[list[str]] = Field(default=None)
    url: Optional[str] = Field(default=None, max_length=2048)
    label: Optional[str] = Field(default=None, max_length=256)
    risk: Literal["normal", "high"] = "normal"
    step_id: Optional[str] = Field(default=None, max_length=64)


class FrameCapabilities(BaseModel):
    """端侧视窗流能力声明（降采样无损 8bit 灰度帧）。"""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(..., gt=0, le=512)
    height: int = Field(..., gt=0, le=512)
    format: Literal["gray8"] = "gray8"


class RunnerCapabilities(BaseModel):
    """端侧心跳上报的能力集合。"""

    model_config = ConfigDict(extra="forbid")

    browser: bool = False
    desktop: bool = False
    frames: Optional[FrameCapabilities] = None


class HeartbeatRequest(BaseModel):
    """端侧心跳 / 能力注册载荷。

    注意：``runner_id`` 只用于与设备档案做一致性校验（防止一卡多身份），
    **租户归属一律取自注册时固化的 ``RunnerDevice.enterprise_id``**。
    """

    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(..., min_length=4, max_length=96)
    scopes: list[str] = Field(default_factory=list)
    platform: str = Field(default="unknown", max_length=32)
    version: str = Field(default="0.0.0", max_length=32)
    capabilities: RunnerCapabilities = Field(default_factory=RunnerCapabilities)


class DispatchPhysicalTaskRequest(BaseModel):
    """下发一条具身物理任务。"""

    model_config = ConfigDict(extra="forbid")

    channel: Literal["browser_action", "desktop_accessibility"]
    steps: list[PhysicalStepModel] = Field(..., min_length=1, max_length=MAX_STEPS_PER_TASK)
    runner_id: str = Field(..., min_length=4, max_length=96)


class PushFrameRequest(BaseModel):
    """端侧上报一条视窗灰度帧。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["keyframe", "delta"]
    width: int = Field(..., gt=0, le=512)
    height: int = Field(..., gt=0, le=512)
    payload_b64: str = Field(..., min_length=8)


class ReportResultRequest(BaseModel):
    """端侧回传物理执行结果。

    ``receipt_id`` 是端侧按 (task_id, step_id) 生成的幂等回执 ID：
    动作执行完成即先落本地账本再上报；重连时重发同一 ``receipt_id``，
    云端据此去重，绝不重复记账或重复触发副作用。

    ``step_id`` 必须与该任务真实存在的 step 身份一致（服务端只认
    ``task.steps[].step_id``）；不接受无法核对步骤身份的自造回执。
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = Field(default=None, max_length=2048)
    receipt_id: Optional[str] = Field(default=None, max_length=64)
    step_id: Optional[str] = Field(default=None, max_length=64)


class ConfirmChallengeRequest(BaseModel):
    """高危操作双因子确认：云端意图确认 / 人工否决。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"] = "approve"
    note: Optional[str] = Field(default=None, max_length=512)


class VerifyDeviceCodeRequest(BaseModel):
    """高危操作双因子确认：端侧物理确认码。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(..., min_length=6, max_length=6)


class RegisterDeviceRequest(BaseModel):
    """云端注册一台具身物理执行设备（登录用户通道）。"""

    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(..., min_length=4, max_length=96)
    device_label: Optional[str] = Field(default=None, max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["read", "physical"])
    credential_label: Optional[str] = Field(default=None, max_length=64)


class ExchangeDeviceTokenRequest(BaseModel):
    """端侧用长期凭据换取短期访问令牌。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=8, max_length=64)
    device_secret: str = Field(..., min_length=32, max_length=256)


class RevokeDeviceRequest(BaseModel):
    """撤销单台设备的全部凭据（登录用户通道）。"""

    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(default=None, max_length=256)


__all__ = [
    "MAX_STEPS_PER_TASK",
    "PhysicalStepModel",
    "FrameCapabilities",
    "RunnerCapabilities",
    "HeartbeatRequest",
    "DispatchPhysicalTaskRequest",
    "PushFrameRequest",
    "ReportResultRequest",
    "ConfirmChallengeRequest",
    "VerifyDeviceCodeRequest",
    "RegisterDeviceRequest",
    "ExchangeDeviceTokenRequest",
    "RevokeDeviceRequest",
]
