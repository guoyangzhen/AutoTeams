import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.enterprise import Enterprise
from app.models.invitation import Invitation
from app.models.user import User
from app.schemas.enterprise import (
    EnterpriseCreate,
    EnterpriseUpdate,
    EnterpriseResponse,
    MemberResponse,
    UpdateRoleRequest,
    InvitationResponse,
)
from app.utils.security import get_current_user
from app.utils.auth.deps import bind_authenticated_tenant
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api, rate_limit_admin
from app.utils.audit import log_audit
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enterprises", tags=["企业"])


def _verify_enterprise_admin(current_user: User, enterprise_id: str) -> None:
    """校验当前用户是对定企业的管理员。

    P1-9 修复：统一超级管理员（enterprise_id is None）放行逻辑，
    与 rbac.require_admin 行为一致。

    - 超级管理员（enterprise_id is None）：放行
    - 企业管理员（enterprise_id 匹配且 role == 'admin'）：放行
    - 其他：403
    """
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id or current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


@router.post("", status_code=status.HTTP_201_CREATED)
@rate_limit_admin()
async def create_enterprise(
    request: Request,
    data: EnterpriseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enterprise = Enterprise(name=data.name)
    db.add(enterprise)
    await db.flush()
    await db.refresh(enterprise)

    current_user.enterprise_id = enterprise.id
    current_user.role = "admin"
    # AUD-19：企业是刚创建的，当前事务里的租户 GUC 还是"无租户"；写审计前重新
    # 绑定到新企业，否则账本策略会以 42501 拒绝这条本该成功的记录。
    await bind_authenticated_tenant(db, current_user)
    # 显式 commit：确保数据在返回 response 前持久化（见 auth_service.register 注释）
    await db.commit()

    await log_audit(
        db,
        current_user,
        "create",
        "enterprise",
        str(enterprise.id),
        request=request,
        details={"name": data.name},
    )
    await db.commit()

    return success_response(EnterpriseResponse.model_validate(enterprise).model_dump())


@router.get("/current")
@rate_limit_api()
async def get_current_enterprise(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前登录用户所属的企业信息（用于前端全局上下文）。"""
    if not current_user.enterprise_id:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    result = await db.execute(
        select(Enterprise).where(Enterprise.id == current_user.enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    if not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_DELETED)

    data = EnterpriseResponse.model_validate(enterprise).model_dump()
    if current_user.role != "admin":
        data["invite_token"] = None
        data["invite_expires_at"] = None
        data["invite_max_uses"] = None
        data["invite_used_count"] = None
    return success_response(data)


@router.get("/{enterprise_id}")
@rate_limit_api()
async def get_enterprise(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P2-2: 软删除的企业视为不存在
    if not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_DELETED)

    if current_user.enterprise_id != enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    data = EnterpriseResponse.model_validate(enterprise).model_dump()
    # 安全：邀请 token 仅管理员可见，防止普通成员越权获取后邀请任意用户加入企业
    # （POST /{enterprise_id}/invite 为 admin-only，GET 不应泄露同等敏感信息）
    if current_user.role != "admin":
        data["invite_token"] = None
        data["invite_expires_at"] = None
        data["invite_max_uses"] = None
        data["invite_used_count"] = None
    return success_response(data)


@router.put("/{enterprise_id}")
@rate_limit_admin()
async def update_enterprise(
    request: Request,
    enterprise_id: str,
    data: EnterpriseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P1-9: 统一使用辅助函数，超级管理员（enterprise_id is None）放行
    _verify_enterprise_admin(current_user, enterprise_id)

    if data.name is not None:
        enterprise.name = data.name
    await db.flush()
    await db.refresh(enterprise)
    # P1-16: 审计日志
    await log_audit(db, current_user, "update", "enterprise", enterprise_id,
                    request=request, details={"name": data.name})
    # 显式 commit：确保企业信息修改在返回 response 前已持久化
    await db.commit()

    return success_response(EnterpriseResponse.model_validate(enterprise).model_dump())


@router.post("/{enterprise_id}/invite")
@rate_limit_admin()
async def generate_invite(
    request: Request,
    enterprise_id: str,
    max_uses: int = 10,
    email: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成企业邀请链接。

    P2-2 改进：每次生成都创建一条 Invitation 记录（token 全局唯一），
    同时更新 Enterprise.invite_token 以保持向后兼容。
    使用 query 参数（max_uses、email）以保持原端点无 body 的签名兼容性。
    """
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P1-9: 统一权限校验
    _verify_enterprise_admin(current_user, enterprise_id)

    # P2-2: 校验 max_uses 合法性
    if max_uses < 1:
        raise HTTPException(status_code=400, detail=ErrorCode.INVITE_MAX_USES_INVALID)

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    # 创建 Invitation 记录
    invitation = Invitation(
        enterprise_id=enterprise_id,
        invited_by_user_id=current_user.id,
        token=token,
        email=email,
        status="pending",
        expires_at=expires_at,
    )
    db.add(invitation)

    # 同时更新 Enterprise.invite_token 保持向后兼容（原 register-with-invite 仍可用）
    enterprise.invite_token = token
    enterprise.invite_expires_at = expires_at
    enterprise.invite_max_uses = max_uses
    enterprise.invite_used_count = 0

    await db.flush()
    await db.refresh(invitation)
    # P1-16: 审计日志
    await log_audit(db, current_user, "create_invite", "invitation", invitation.id,
                    request=request, details={"enterprise_id": enterprise_id, "max_uses": max_uses})
    # 显式 commit：确保邀请 token 在返回 response 前已持久化，否则客户端立即使用邀请链接会失效
    await db.commit()

    # FE-SEC-03: 优先使用配置的前端地址生成完整邀请链接，避免前端依赖 window.location.origin
    invite_path = f"/invite/{token}"
    if settings.FRONTEND_URL:
        base = settings.FRONTEND_URL.rstrip("/")
        invite_url = f"{base}{invite_path}"
    else:
        invite_url = invite_path

    return success_response({
        "invite_token": token,
        "invite_url": invite_url,
        "expires_at": expires_at.isoformat(),
        "max_uses": max_uses,
        "used_count": 0,
        "invitation_id": invitation.id,
    })


# ==================== P2-2: 成员管理 ====================


@router.get("/{enterprise_id}/members")
@rate_limit_api()
async def list_members(
    request: Request,
    enterprise_id: str,
    limit: int = Query(100, ge=1, le=500, description="每页数量（1-500）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取企业成员列表（admin 或 member 都可查看）。"""
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    if current_user.enterprise_id != enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    result = await db.execute(
        select(User).where(User.enterprise_id == enterprise_id).limit(limit).offset(offset)
    )
    members = result.scalars().all()
    return success_response([
        MemberResponse.model_validate(m).model_dump() for m in members
    ])


@router.delete("/{enterprise_id}/members/{user_id}")
@rate_limit_admin()
async def remove_member(
    request: Request,
    enterprise_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """移除企业成员（需要 admin，不能移除自己）。"""
    # 校验企业存在
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P1-9: 统一权限校验
    _verify_enterprise_admin(current_user, enterprise_id)

    # 不能移除自己
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail=ErrorCode.CANNOT_REMOVE_SELF)

    # 查找目标成员
    result = await db.execute(
        select(User).where(User.id == user_id, User.enterprise_id == enterprise_id)
    )
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail=ErrorCode.MEMBER_NOT_FOUND)

    # 移除：断开 enterprise 关联并禁用账号
    target_user.enterprise_id = None
    target_user.is_active = False
    await db.flush()
    # P1-16: 审计日志
    await log_audit(db, current_user, "remove_member", "user", user_id,
                    request=request, details={"enterprise_id": enterprise_id})
    # 显式 commit：确保成员移除操作在返回 response 前已持久化
    await db.commit()

    return success_response({"message": "成员已移除"})


@router.put("/{enterprise_id}/members/{user_id}/role")
@rate_limit_admin()
async def change_member_role(
    request: Request,
    enterprise_id: str,
    user_id: str,
    data: UpdateRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """变更成员角色（需要 admin，不能改自己）。"""
    # 校验企业存在
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P1-9: 统一权限校验
    _verify_enterprise_admin(current_user, enterprise_id)

    # 不能改自己
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail=ErrorCode.CANNOT_CHANGE_SELF_ROLE)

    # 查找目标成员
    result = await db.execute(
        select(User).where(User.id == user_id, User.enterprise_id == enterprise_id)
    )
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail=ErrorCode.MEMBER_NOT_FOUND)

    target_user.role = data.role
    await db.flush()
    await db.refresh(target_user)
    # P1-16: 审计日志
    await log_audit(db, current_user, "change_role", "user", user_id,
                    request=request, details={"enterprise_id": enterprise_id, "new_role": data.role})
    # 显式 commit：确保角色变更在返回 response 前已持久化
    await db.commit()

    return success_response(MemberResponse.model_validate(target_user).model_dump())


# ==================== P2-2: 企业软删除 ====================


@router.delete("/{enterprise_id}")
@rate_limit_admin()
async def delete_enterprise(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """软删除企业（需要 admin，设置 is_active=False，同时禁用所有成员）。"""
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P1-9: 统一权限校验
    _verify_enterprise_admin(current_user, enterprise_id)

    # 软删除企业
    enterprise.is_active = False

    # 禁用所有成员
    result = await db.execute(
        select(User).where(User.enterprise_id == enterprise_id)
    )
    members = result.scalars().all()
    for m in members:
        m.is_active = False

    await db.flush()
    # P1-16: 审计日志
    await log_audit(db, current_user, "delete", "enterprise", enterprise_id,
                    request=request, details={"disabled_members": len(members)})
    # 显式 commit：确保软删除和成员禁用在返回 response 前已持久化
    await db.commit()

    return success_response({"message": "企业已删除"})


# ==================== P2-2: 邀请记录管理 ====================


@router.get("/{enterprise_id}/invitations")
@rate_limit_admin()
async def list_invitations(
    request: Request,
    enterprise_id: str,
    limit: int = Query(100, ge=1, le=500, description="每页数量（1-500）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取企业邀请记录列表（需要 admin）。"""
    # 校验企业存在
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P1-9: 统一权限校验
    _verify_enterprise_admin(current_user, enterprise_id)

    result = await db.execute(
        select(Invitation)
        .where(Invitation.enterprise_id == enterprise_id)
        .order_by(Invitation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    invitations = result.scalars().all()
    return success_response([
        InvitationResponse.model_validate(inv).model_dump() for inv in invitations
    ])


@router.post("/{enterprise_id}/invitations/{invitation_id}/cancel")
@rate_limit_admin()
async def cancel_invitation(
    request: Request,
    enterprise_id: str,
    invitation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消邀请（需要 admin，将 status 置为 cancelled）。"""
    # 校验企业存在
    result = await db.execute(
        select(Enterprise).where(Enterprise.id == enterprise_id)
    )
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)

    # P1-9: 统一权限校验
    _verify_enterprise_admin(current_user, enterprise_id)

    # 查找邀请
    result = await db.execute(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.enterprise_id == enterprise_id,
        )
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        raise HTTPException(status_code=404, detail=ErrorCode.INVITATION_NOT_FOUND)

    if invitation.status != "pending":
        # BE-SEC-02: 不暴露内部状态值，使用统一错误码
        logger.warning(f"邀请状态不允许取消: invitation_id={invitation.id}, status={invitation.status}")
        raise HTTPException(status_code=400, detail=ErrorCode.INVITATION_STATUS_INVALID)

    invitation.status = "cancelled"
    await db.flush()
    await db.refresh(invitation)
    # P1-16: 审计日志
    await log_audit(db, current_user, "cancel_invite", "invitation", invitation_id,
                    request=request, details={"enterprise_id": enterprise_id})
    # 显式 commit：确保取消操作在返回 response 前已持久化
    await db.commit()

    return success_response(InvitationResponse.model_validate(invitation).model_dump())
