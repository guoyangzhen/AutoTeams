"""本地路径授权端点（prefix=/local-paths，无尾斜杠）。

端点：
- POST   /local-paths/register        注册本地路径授权（签发一次性 setup token + 命令）
- GET    /local-paths                 列出当前企业下可见的授权记录
- DELETE /local-paths/{id}            撤销授权
- POST   /local-paths/{id}/validate   请求 Runner 本地重新校验路径
- POST   /local-paths/{id}/run        驱动本地守护进程执行受限文件操作（list/read/write/delete）

- POST   /local-paths/{id}/claim      内部：Runner 认领（校验 setup token）
- POST   /local-paths/{id}/connected  内部：Runner 上报已连接（桥接密钥鉴权）
- POST   /local-paths/{id}/offline    内部：Runner 断线（桥接密钥鉴权）

安全：
- 公开端点经 get_current_user 鉴权 + 企业隔离 + RBAC + 限流。
- 内部端点（claim/connected/offline）由 collaboration-service 调用：
  claim 以「一次性 setup token」为凭证；connected/offline 以 X-Bridge-Secret 为凭证。
"""
import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.local_path_grant import LocalPathGrant
from app.models.user import User
from app.schemas.local_path import (
    ClaimGrantRequest,
    LocalPathGrantView,
    RegisterLocalPathRequest,
    RegisterLocalPathResponse,
    RunLocalTaskRequest,
    RunnerStatusUpdate,
)
from app.services import runner_session
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/local-paths", tags=["本地工具桥接"])


# ============================================================
# 内部鉴权辅助（service-to-service）
# ============================================================

def _verify_bridge_secret(x_bridge_secret: str | None) -> None:
    """校验 collaboration-service 携带的内部密钥（service-to-service 鉴权）。

    失败关闭：未配置密钥或密钥不匹配一律拒绝，不再有「仅开发跳过」逻辑。
    """
    expected = settings.BRIDGE_INTERNAL_SECRET
    if not expected:
        logger.error("BRIDGE_INTERNAL_SECRET 未配置，内部端点拒绝访问")
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    if not x_bridge_secret or not secrets.compare_digest(x_bridge_secret, expected):
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


async def _get_grant_for_user(
    db: AsyncSession, grant_id: str, current_user: User
) -> LocalPathGrant:
    result = await db.execute(
        select(LocalPathGrant).where(LocalPathGrant.id == grant_id)
    )
    grant = result.scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail=ErrorCode.LOCAL_PATH_NOT_FOUND)
    # 企业隔离：全局 admin 可跨企业，否则必须同企业
    if current_user.enterprise_id is None and current_user.role == "admin":
        return grant
    if grant.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.LOCAL_PATH_ACCESS_DENIED)
    return grant


async def _get_grant_owned(
    db: AsyncSession, grant_id: str, current_user: User
) -> LocalPathGrant:
    """获取授权并强制「仅所有者可操作」。

    用于 run/revoke/validate 等会驱动本地执行或影响授权的操作：
    同企业其他成员即使能读到授权，也不能越权操作他人授权。
    全局 admin 同样不允许代操作（授权与用户绑定）。
    """
    grant = await _get_grant_for_user(db, grant_id, current_user)
    if grant.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail=ErrorCode.LOCAL_PATH_ACCESS_DENIED)
    return grant


# ============================================================
# 公开端点
# ============================================================

@router.post("/register", status_code=201, response_model=None)
@rate_limit_admin()
async def register_local_path(
    request: Request,
    body: RegisterLocalPathRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    # 限流：单用户可授权路径数上限
    count_res = await db.execute(
        select(func.count(LocalPathGrant.id)).where(
            LocalPathGrant.user_id == current_user.id,
            LocalPathGrant.status.in_(["pending", "connected", "offline"]),
        )
    )
    active_count = count_res.scalar_one()
    if active_count >= settings.LOCAL_PATH_MAX_PER_USER:
        raise HTTPException(status_code=400, detail=ErrorCode.LOCAL_PATH_LIMIT_EXCEEDED)

    try:
        grant, token, expires_at = await runner_session.register_grant(
            db,
            enterprise_id=current_user.enterprise_id,
            user_id=str(current_user.id),
            local_path=body.local_path,
            scope=body.scope,
            label=body.label,
        )
    except SQLAlchemyError as e:
        logger.error("注册本地路径失败: %s", type(e).__name__, exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e

    await log_audit(
        db, current_user, "register", "local_path", grant.id,
        request=request, details={"local_path": body.local_path, "scope": body.scope},
    )
    await db.commit()

    resp = RegisterLocalPathResponse(
        grant=LocalPathGrantView.model_validate(grant),
        setup_token=token,
        setup_command=runner_session.build_setup_command(
            grant.id, token, body.local_path, body.scope
        ),
        setup_token_expires_at=expires_at,
    )
    return success_response(
        data=resp.model_dump(mode="json"), message="本地路径授权已创建"
    )


@router.get("", response_model=None)
@rate_limit_api()
async def list_local_paths(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    if current_user.enterprise_id is None and current_user.role == "admin":
        result = await db.execute(
            select(LocalPathGrant).order_by(LocalPathGrant.created_at.desc())
        )
    else:
        result = await db.execute(
            select(LocalPathGrant)
            .where(LocalPathGrant.enterprise_id == current_user.enterprise_id)
            .order_by(LocalPathGrant.created_at.desc())
        )
    grants = result.scalars().all()
    return success_response(
        data=[LocalPathGrantView.model_validate(g).model_dump(mode="json") for g in grants]
    )


@router.get("/{grant_id}", response_model=None)
@rate_limit_api()
async def get_local_path(
    grant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    grant = await _get_grant_for_user(db, grant_id, current_user)
    return success_response(
        data=LocalPathGrantView.model_validate(grant).model_dump(mode="json")
    )


@router.delete("/{grant_id}", response_model=None)
@rate_limit_admin()
async def revoke_local_path(
    grant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    grant = await _get_grant_owned(db, grant_id, current_user)
    try:
        await runner_session.revoke_grant(db, grant)
    except SQLAlchemyError as e:
        logger.error("撤销本地路径失败: %s", type(e).__name__, exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
    await log_audit(
        db, current_user, "revoke", "local_path", grant.id, request=request
    )
    await db.commit()
    return success_response(message="本地路径授权已撤销")


@router.post("/{grant_id}/validate", response_model=None)
@rate_limit_api()
async def validate_local_path(
    grant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    grant = await _get_grant_owned(db, grant_id, current_user)
    if grant.status not in ("connected", "offline"):
        raise HTTPException(status_code=400, detail=ErrorCode.LOCAL_PATH_RUNNER_OFFLINE)

    task = RunLocalTaskRequest(tool="list", path=".")
    try:
        result = await runner_session.dispatch_to_runner(grant.id, task)
    except RuntimeError as e:
        logger.warning("本地路径校验失败（Runner 不可达）: %s", e)
        raise HTTPException(status_code=400, detail=ErrorCode.LOCAL_PATH_RUNNER_OFFLINE) from e

    await log_audit(
        db, current_user, "validate", "local_path", grant.id,
        request=request, details={"result": result},
    )
    await db.commit()
    return success_response(data=result, message="本地路径校验完成")


@router.post("/{grant_id}/run", response_model=None)
@rate_limit_api()
async def run_local_task(
    grant_id: str,
    body: RunLocalTaskRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    grant = await _get_grant_owned(db, grant_id, current_user)
    if grant.status != "connected":
        raise HTTPException(status_code=400, detail=ErrorCode.LOCAL_PATH_RUNNER_OFFLINE)

        # scope 校验（写入与删除需 read_write；通用命令/Agentic 已由 schema 拒绝）
    if body.tool in ("write", "delete") and grant.scope != "read_write":

        raise HTTPException(status_code=403, detail=ErrorCode.LOCAL_PATH_SCOPE_FORBIDDEN)

    try:
        result = await runner_session.dispatch_to_runner(grant.id, body)
    except RuntimeError as e:
        logger.warning("本地任务执行失败: %s", e)
        raise HTTPException(status_code=400, detail=ErrorCode.LOCAL_PATH_TASK_FAILED) from e

    await log_audit(
        db, current_user, "run", "local_path", grant.id,
        request=request,
                details={"tool": body.tool, "path": body.path},

    )
    await db.commit()
    return success_response(data=result, message="本地任务已执行")


# ============================================================
# 内部端点（collaboration-service 调用）
# ============================================================

@router.post("/{grant_id}/claim", response_model=None)
@rate_limit_api()
async def claim_local_path(
    grant_id: str,
    body: ClaimGrantRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LocalPathGrant).where(LocalPathGrant.id == grant_id)
    )
    grant = result.scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail=ErrorCode.LOCAL_PATH_NOT_FOUND)
    if grant.status == "revoked":
        raise HTTPException(status_code=403, detail=ErrorCode.LOCAL_PATH_ACCESS_DENIED)

    ok = await runner_session.claim_grant(db, grant, body.setup_token)
    if not ok:
        raise HTTPException(status_code=401, detail=ErrorCode.LOCAL_PATH_TOKEN_INVALID)

    return success_response(data={
        "grant_id": grant.id,
        "scope": grant.scope,
        "local_path": grant.local_path,
        "enterprise_id": grant.enterprise_id,
        "user_id": grant.user_id,
    })


@router.post("/{grant_id}/connected", response_model=None)
@rate_limit_api()
async def mark_connected(
    grant_id: str,
    body: RunnerStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_bridge_secret: str | None = Header(default=None),
):
    _verify_bridge_secret(x_bridge_secret)
    result = await db.execute(
        select(LocalPathGrant).where(LocalPathGrant.id == grant_id)
    )
    grant = result.scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail=ErrorCode.LOCAL_PATH_NOT_FOUND)
    if grant.status == "revoked":
        raise HTTPException(status_code=403, detail=ErrorCode.LOCAL_PATH_ACCESS_DENIED)

    connected = await runner_session.mark_connected(
        db, grant, body.runner_id, body.resolved_path, body.tool_manifest
    )
    if not connected:
        raise HTTPException(status_code=403, detail=ErrorCode.LOCAL_PATH_ACCESS_DENIED)
    return success_response(message="本地守护进程已连接")


@router.post("/{grant_id}/offline", response_model=None)
@rate_limit_api()
async def mark_offline(
    grant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_bridge_secret: str | None = Header(default=None),
):
    _verify_bridge_secret(x_bridge_secret)
    result = await db.execute(
        select(LocalPathGrant).where(LocalPathGrant.id == grant_id)
    )
    grant = result.scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail=ErrorCode.LOCAL_PATH_NOT_FOUND)

    await runner_session.mark_offline(db, grant)
    return success_response(message="本地守护进程已离线")
