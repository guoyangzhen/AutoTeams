"""AutoTeams 5.0 · 战役 3：具身物理执行器 2.0 API（prefix=/api/v1/runner/v2）。

端点（全部无尾斜杠）
--------------------
云端通道（``get_current_user`` + 企业隔离）：

- POST /devices                         注册端侧设备（固化企业/所有者，签发长期凭据）
- POST /devices/{device_id}/rotate      轮换单台设备的长期凭据
- POST /devices/{device_id}/revoke      撤销单台设备（不影响其他设备）
- GET  /devices                         本企业设备列表
- POST /tasks                           下发物理任务（护栏实时审查）
- GET  /tasks                           本企业物理任务列表
- GET  /tasks/{task_id}                 单任务详情
- GET  /tasks/{task_id}/stream          视窗帧实时推送（text/event-stream）
- GET  /challenges                      高危操作双因子确认列表
- POST /challenges/{id}/confirm         双因子确认（云端意图确认 / 人工否决）
- GET  /runners                         端侧档案与在线状态（= 本企业设备的兼容视图）
- GET  /audit                           操作审计留痕
- POST /maintenance/purge               手动触发有界保留清理（企业管理员）

端侧通道（**逐设备访问令牌**，``Authorization: Bearer <device_token>``）：

- POST /runners/heartbeat               心跳与能力注册（回带本设备任务/通知）
- POST /tasks/{task_id}/claim            领取任务（仅限本设备任务）
- POST /tasks/{task_id}/frames           上报视窗灰度帧
- POST /tasks/{task_id}/result           回传执行结果（按 receipt_id 幂等）
- POST /challenges/{id}/verify           第二因子：端侧物理确认码
- POST /devices/token                   长期凭据 → 短期访问令牌

服务间通道（**仅** collaboration-service，``X-Bridge-Secret``）：

- POST /internal/service/presence        仅登记设备在线状态，不返回任何租户数据

鉴权边界（AUD-04）
-----------------
- 端侧**只**认逐设备令牌。历史的全局共享 ``X-Bridge-Secret`` 在本模块
  已彻底移除（clean cutover）：拿到该共享密钥不再能冒充任何设备。
- 每个端侧调用都强制「令牌 → 设备 → 企业」与「任务.device_id == 设备.id」。
- 服务间共享密钥只保留在 ``/internal/service/*`` 这一条明确命名空间里，
  该端点不接受设备令牌，也不会返回任务、确认码或跨租户设备信息。
"""
import json
import logging
import secrets
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.runner_v2 import RunnerDevice
from app.models.user import User
from app.schemas.runner_v2 import (
    ConfirmChallengeRequest,
    DispatchPhysicalTaskRequest,
    ExchangeDeviceTokenRequest,
    HeartbeatRequest,
    PhysicalStepModel,
    PushFrameRequest,
    RegisterDeviceRequest,
    ReportResultRequest,
    RevokeDeviceRequest,
    VerifyDeviceCodeRequest,
)
from app.services.runner_v2_protocol import (
    PhysicalProtocolError,
    runner_v2_protocol,
)
from app.utils.rate_limit import rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user
from app.utils.tenant_scope import ROLE_ADMIN, assert_role, require_enterprise_bound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runner/v2", tags=["Local Runner 2.0 物理执行"])


# ============================================================
# 鉴权与错误收敛
# ============================================================


def _bearer_token(authorization: Optional[str]) -> str:
    """从 ``Authorization: Bearer <token>`` 中取出设备访问令牌。"""
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


async def authenticate_device(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> RunnerDevice:
    """端侧机器通道鉴权：只认逐设备短期令牌（AUD-04 clean cutover）。"""
    try:
        return await runner_v2_protocol.authenticate_device_token(
            db, token=_bearer_token(authorization)
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc


def verify_service_secret(x_bridge_secret: Optional[str]) -> None:
    """服务间共享密钥（仅 collaboration-service ↔ backend，绝不用于设备鉴权）。"""
    expected = settings.BRIDGE_INTERNAL_SECRET
    if not expected:
        logger.error("BRIDGE_INTERNAL_SECRET 未配置，服务间通道拒绝访问")
        raise HTTPException(status_code=403, detail="FORBIDDEN")
    if not x_bridge_secret or not secrets.compare_digest(x_bridge_secret, expected):
        raise HTTPException(status_code=403, detail="FORBIDDEN")


def _require_enterprise_id(current_user: User) -> str:
    """物理操作始终绑定企业归属；无企业账号不得下发具身任务。"""
    return require_enterprise_bound(current_user)


def _actor(current_user: User) -> str:
    return current_user.id or current_user.email


def _handle(error: PhysicalProtocolError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def _steps_to_payload(steps: list[PhysicalStepModel]) -> list[dict[str, Any]]:
    """把 pydantic step 收敛为端侧契约的纯 dict（剔除未设置字段）。"""
    return [step.model_dump(exclude_none=True) for step in steps]


# ============================================================
# 云端通道：设备注册与凭据管理
# ============================================================


@router.post("/devices", response_model=None)
@rate_limit_api()
async def register_runner_device(
    request: Request,
    payload: RegisterDeviceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """注册端侧设备：企业归属与所有者由服务端固化，返回一次性长期凭据。"""
    enterprise_id = _require_enterprise_id(current_user)
    # AUD-04：注册设备会签发**长期凭据**并让该设备承接本企业的物理任务，
    # 与轮换/撤销一样属于企业管理员决策，普通成员不得自行接入新终端。
    assert_role(current_user, ROLE_ADMIN)
    try:
        device = await runner_v2_protocol.register_device(
            db,
            enterprise_id=enterprise_id,
            owner_user_id=_actor(current_user),
            runner_id=payload.runner_id,
            device_label=payload.device_label,
            scopes=payload.scopes,
            credential_label=payload.credential_label,
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=device, message="端侧设备已注册，请妥善保存设备凭据")


@router.get("/devices", response_model=None)
@rate_limit_api()
async def list_runner_devices(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """列出**本企业**的端侧设备。"""
    enterprise_id = _require_enterprise_id(current_user)
    return success_response(data=await runner_v2_protocol.list_devices(db, enterprise_id))


@router.post("/devices/{device_id}/rotate", response_model=None)
@rate_limit_api()
async def rotate_runner_device_credential(
    request: Request,
    device_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """轮换单台设备的长期凭据（旧凭据立即失效，其他设备不受影响）。"""
    enterprise_id = _require_enterprise_id(current_user)
    assert_role(current_user, ROLE_ADMIN)
    try:
        device = await runner_v2_protocol.rotate_device_credential(
            db,
            device_id=device_id,
            enterprise_id=enterprise_id,
            actor=_actor(current_user),
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=device, message="设备凭据已轮换")


@router.post("/devices/{device_id}/revoke", response_model=None)
@rate_limit_api()
async def revoke_runner_device(
    request: Request,
    device_id: str,
    payload: RevokeDeviceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """撤销单台设备：其全部凭据与令牌立即作废，其他设备不受影响。"""
    enterprise_id = _require_enterprise_id(current_user)
    assert_role(current_user, ROLE_ADMIN)
    try:
        device = await runner_v2_protocol.revoke_device(
            db,
            device_id=device_id,
            enterprise_id=enterprise_id,
            actor=_actor(current_user),
            reason=payload.reason,
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=device, message="端侧设备已撤销")


# ============================================================
# 端侧通道：逐设备访问令牌
# ============================================================


@router.post("/devices/token", response_model=None)
@rate_limit_api()
async def exchange_device_token(
    request: Request,
    payload: ExchangeDeviceTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """端侧用长期设备凭据换取短期访问令牌（默认 15 分钟）。"""
    try:
        return success_response(
            data=await runner_v2_protocol.exchange_device_token(
                db,
                device_id=payload.device_id,
                device_secret=payload.device_secret,
            ),
            message="设备令牌已签发",
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc


@router.post("/runners/heartbeat", response_model=None)
@rate_limit_api()
async def runner_heartbeat(
    request: Request,
    payload: HeartbeatRequest,
    device: RunnerDevice = Depends(authenticate_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """端侧心跳：续期本设备档案，回带**本设备**的待执行任务与待确认通知。"""
    try:
        result = await runner_v2_protocol.heartbeat(
            db,
            device=device,
            runner_id=payload.runner_id,
            scopes=payload.scopes,
            platform=payload.platform,
            version=payload.version,
            capabilities=payload.capabilities.model_dump(exclude_none=True),
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=result, message="心跳已登记")


@router.post("/tasks/{task_id}/claim", response_model=None)
@rate_limit_api()
async def claim_physical_task(
    request: Request,
    task_id: str,
    device: RunnerDevice = Depends(authenticate_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """端侧领取任务：非本设备任务按「不存在」处理（AUD-04）。"""
    try:
        task = await runner_v2_protocol.claim_task(db, device=device, task_id=task_id)
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=task, message="任务已领取")


@router.post("/tasks/{task_id}/frames", response_model=None)
@rate_limit_api()
async def push_viewport_frame(
    request: Request,
    task_id: str,
    payload: PushFrameRequest,
    device: RunnerDevice = Depends(authenticate_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """端侧上报一条视窗灰度帧（无损 8bit 灰度降采样）。"""
    try:
        frame = await runner_v2_protocol.push_frame(
            db,
            device=device,
            task_id=task_id,
            kind=payload.kind,
            width=payload.width,
            height=payload.height,
            payload_b64=payload.payload_b64,
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=frame, message="视窗帧已归档")


@router.post("/tasks/{task_id}/result", response_model=None)
@rate_limit_api()
async def report_physical_result(
    request: Request,
    task_id: str,
    payload: ReportResultRequest,
    device: RunnerDevice = Depends(authenticate_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """端侧回传物理执行结果（同一 step 的重发不会重复记账）。"""
    try:
        task = await runner_v2_protocol.report_result(
            db,
            device=device,
            task_id=task_id,
            ok=payload.ok,
            data=payload.data,
            error=payload.error,
            receipt_id=payload.receipt_id,
            step_id=payload.step_id,
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=task, message="执行结果已记录")


@router.post("/challenges/{challenge_id}/verify", response_model=None)
@rate_limit_api()
async def verify_device_factor(
    request: Request,
    challenge_id: str,
    payload: VerifyDeviceCodeRequest,
    device: RunnerDevice = Depends(authenticate_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """高危操作双因子确认之第二因子：端侧物理确认码（仅任务属主设备可提交）。"""
    try:
        challenge = await runner_v2_protocol.verify_device_factor(
            db, device=device, challenge_id=challenge_id, device_code=payload.device_code
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=challenge, message="双因子确认通过，任务已放行")


# ============================================================
# 服务间通道（collaboration-service，共享密钥，与设备鉴权完全分离）
# ============================================================


@router.post("/internal/service/presence", response_model=None)
@rate_limit_api()
async def service_device_presence(
    request: Request,
    payload: dict[str, Any],
    x_bridge_secret: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """服务间通道：仅登记「某企业某设备」的在线状态。

    该端点**不接受**设备令牌，也**不返回**任务、确认码、凭据或任何其他企业的
    数据；它存在的唯一目的是让 collaboration-service 复用既有的服务级鉴权语义。
    """
    verify_service_secret(x_bridge_secret)
    enterprise_id = str(payload.get("enterprise_id") or "")
    device_id = str(payload.get("device_id") or "")
    if not enterprise_id or not device_id:
        raise HTTPException(status_code=422, detail="enterprise_id 与 device_id 必填")
    try:
        presence = await runner_v2_protocol.service_touch_device_presence(
            db, enterprise_id=enterprise_id, device_id=device_id
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=presence)


# ============================================================
# 云端通道：任务 / 挑战 / 档案 / 审计
# ============================================================


@router.post("/tasks", response_model=None)
@rate_limit_api()
async def dispatch_physical_task(
    request: Request,
    payload: DispatchPhysicalTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """下发物理任务：护栏裁决后放行 / 阻断 / 挂起双因子确认。

    ``runner_id`` 只在**调用者企业**内解析：企业 A 无法向企业 B 的设备下发任务。
    """
    enterprise_id = _require_enterprise_id(current_user)
    try:
        task = await runner_v2_protocol.dispatch_task(
            db,
            enterprise_id=enterprise_id,
            channel_raw=payload.channel,
            steps=_steps_to_payload(payload.steps),
            runner_id=payload.runner_id,
            actor=_actor(current_user),
        )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=task, message="物理任务已受理")


@router.get("/tasks", response_model=None)
@rate_limit_api()
async def list_physical_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """列出本企业物理任务（审计与工作台展示）。"""
    enterprise_id = _require_enterprise_id(current_user)
    return success_response(data=await runner_v2_protocol.list_tasks(db, enterprise_id))


@router.get("/tasks/{task_id}", response_model=None)
@rate_limit_api()
async def get_physical_task(
    request: Request,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """获取单条物理任务详情。"""
    enterprise_id = _require_enterprise_id(current_user)
    try:
        task, runner_id = await runner_v2_protocol.get_task(db, task_id, enterprise_id)
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=runner_v2_protocol.task_to_dict(task, runner_id))


@router.get("/tasks/{task_id}/stream", response_model=None)
@rate_limit_api()
async def stream_viewport_frames(
    request: Request,
    task_id: str,
    since_seq: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """视窗帧实时推送：按 ``since_seq`` 游标增量下发灰度帧后结束本次连接。

    采用「游标 + 短连接」而非长连接，避免端侧离线时前端悬挂等待；
    前端以最后一次 seq 作为游标循环拉取即可获得准实时视窗流。
    """
    enterprise_id = _require_enterprise_id(current_user)
    try:
        await runner_v2_protocol.get_task(db, task_id, enterprise_id)
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc

    async def event_source() -> AsyncIterator[bytes]:
        cursor = since_seq
        frames = await runner_v2_protocol.read_frames(db, task_id, since_seq=cursor)
        for frame in frames:
            cursor = frame["seq"]
            payload = json.dumps(frame, ensure_ascii=False)
            yield f"event: frame\ndata: {payload}\n\n".encode("utf-8")
        yield b"event: eof\ndata: {\"cursor\": %d}\n\n" % cursor

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/challenges", response_model=None)
@rate_limit_api()
async def list_two_factor_challenges(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """列出本企业高危操作双因子确认挑战。"""
    enterprise_id = _require_enterprise_id(current_user)
    return success_response(data=await runner_v2_protocol.list_challenges(db, enterprise_id))


@router.post("/challenges/{challenge_id}/confirm", response_model=None)
@rate_limit_api()
async def confirm_two_factor(
    request: Request,
    challenge_id: str,
    payload: ConfirmChallengeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """高危操作双因子确认之第一因子：云端操作意图确认 / 人工否决。"""
    enterprise_id = _require_enterprise_id(current_user)
    # AUD-04：第一道因子是"放行高危物理操作"的审批位。否决可以让任何成员做
    # （只能收紧不能放宽），但放行必须是企业管理员，否则双因子退化成"发起人自批"。
    if payload.decision != "reject":
        assert_role(current_user, ROLE_ADMIN)
    try:
        if payload.decision == "reject":
            challenge = await runner_v2_protocol.reject_challenge(
                db, challenge_id, enterprise_id, _actor(current_user)
            )
        else:
            challenge = await runner_v2_protocol.confirm_endpoint_factor(
                db, challenge_id, enterprise_id, _actor(current_user)
            )
    except PhysicalProtocolError as exc:
        raise _handle(exc) from exc
    return success_response(data=challenge, message="双因子状态已更新")


@router.get("/runners", response_model=None)
@rate_limit_api()
async def list_physical_runners(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """端侧档案与在线状态（``GET /devices`` 的兼容视图，同样按企业隔离）。"""
    enterprise_id = _require_enterprise_id(current_user)
    devices = await runner_v2_protocol.list_devices(db, enterprise_id)
    return success_response(
        data=[
            {
                "runner_id": item["runner_id"],
                "platform": item["platform"],
                "version": item["version"],
                "scopes": item["scopes"],
                "capabilities": item["capabilities"],
                "online": item["online"],
                "last_seen": item["last_seen"],
                "device_id": item["device_id"],
                "status": item["status"],
            }
            for item in devices
        ]
    )


@router.get("/audit", response_model=None)
@rate_limit_api()
async def read_physical_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """操作审计留痕（凭据已脱敏，永不落明文）。"""
    enterprise_id = _require_enterprise_id(current_user)
    return success_response(data=await runner_v2_protocol.read_audit(db, enterprise_id, limit=limit))


@router.post("/maintenance/purge", response_model=None)
@rate_limit_api()
async def purge_runner_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """手动触发有界保留清理（策略见 runner_v2_protocol 模块 docstring）。"""
    _require_enterprise_id(current_user)
    assert_role(current_user, ROLE_ADMIN)
    return success_response(
        data=await runner_v2_protocol.purge_expired(db), message="保留策略清理完成"
    )
