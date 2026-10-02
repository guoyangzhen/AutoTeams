"""AutoTeams 4.0 数字员工花名册与名牌档案 API 路由。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.workforce_profile import (
    WorkforceProfileCreate,
    WorkforceProfileUpdate,
    WorkforceProfileResponse,
    DutyBoundaryCheckRequest,
)
from app.services.workforce.profile_service import (
    create_workforce_profile,
    get_workforce_profile,
    list_workforce_profiles,
    update_workforce_profile,
    delete_workforce_profile,
    check_duty_boundary,
)
from app.utils.security import get_current_user
from app.utils.response import success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workforce-profiles", tags=["AutoTeams Workforce Profiles"])


@router.get("", response_model=None)
async def list_profiles(
    status: Optional[str] = Query(None, description="聘用状态过滤: shadow/active/suspended/retired"),
    department: Optional[str] = Query(None, description="部门过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取企业当前所有数字员工档案花名册。"""
    profiles = await list_workforce_profiles(
        db,
        enterprise_id=current_user.enterprise_id,
        status=status,
        department=department,
    )
    data = [WorkforceProfileResponse.model_validate(p).model_dump() for p in profiles]
    return success_response(data=data)


@router.get("/gallery", response_model=None)
async def get_gallery(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取数字员工大厅（Gallery）聚合视图。"""
    profiles = await list_workforce_profiles(db, enterprise_id=current_user.enterprise_id)
    items = [WorkforceProfileResponse.model_validate(p).model_dump() for p in profiles]
    stats = {
        "total": len(items),
        "active": sum(1 for p in items if p["employment_status"] == "active"),
        "shadow": sum(1 for p in items if p["employment_status"] == "shadow"),
        "departments": list(set(p["department"] for p in items)),
    }
    return success_response(data={"profiles": items, "stats": stats})


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: WorkforceProfileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建并授予新数字员工工号与岗位档案卡。"""
    profile = await create_workforce_profile(db, enterprise_id=current_user.enterprise_id, payload=payload)
    return success_response(
        data=WorkforceProfileResponse.model_validate(profile).model_dump(),
        message="成功创建数字员工档案",
    )


@router.get("/{profile_id}", response_model=None)
async def get_profile(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询单个数字员工名牌详情。"""
    profile = await get_workforce_profile(db, profile_id)
    if not profile or profile.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数字员工档案不存在")
    return success_response(data=WorkforceProfileResponse.model_validate(profile).model_dump())


@router.put("/{profile_id}", response_model=None)
async def update_profile(
    profile_id: str,
    payload: WorkforceProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新数字员工名牌或权限清单。"""
    profile = await get_workforce_profile(db, profile_id)
    if not profile or profile.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数字员工档案不存在")

    updated = await update_workforce_profile(db, profile_id, payload)
    return success_response(
        data=WorkforceProfileResponse.model_validate(updated).model_dump(),
        message="数字员工档案更新成功",
    )


@router.delete("/{profile_id}", response_model=None)
async def delete_profile(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """注销/删除数字员工档案。"""
    profile = await get_workforce_profile(db, profile_id)
    if not profile or profile.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数字员工档案不存在")

    await delete_workforce_profile(db, profile_id)
    return success_response(message="数字员工档案已成功删除")


@router.post("/{profile_id}/verify-action", response_model=None)
async def verify_duty_action(
    profile_id: str,
    payload: DutyBoundaryCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """岗位职责边界防线检查（越权校验）。"""
    profile = await get_workforce_profile(db, profile_id)
    if not profile or profile.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数字员工档案不存在")

    check_result = check_duty_boundary(profile, payload.action_intent)
    return success_response(data=check_result.model_dump())

@router.post("/{profile_id}/check-boundary", response_model=None)
async def check_boundary_alias(
    profile_id: str,
    payload: DutyBoundaryCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """岗位职责边界防线检查别名（兼容 check-boundary 与 verify-action）。"""
    return await verify_duty_action(profile_id, payload, db, current_user)
