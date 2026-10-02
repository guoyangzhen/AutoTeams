"""AutoTeams 4.0 数字员工档案与名牌服务层。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workforce import WorkforceProfile
from app.schemas.workforce_profile import (
    WorkforceProfileCreate,
    WorkforceProfileUpdate,
    DutyBoundaryCheckResponse,
)

logger = logging.getLogger(__name__)


async def generate_next_badge(db: AsyncSession, enterprise_id: str, department: str = "GEN") -> str:
    """自动生成 AutoTeams 规范工号，格式：AT-{YEAR}-{DEPT_CODE}-{001}"""
    year = datetime.utcnow().year
    dept_code = department[:4].upper() if department else "GEN"

    # 统计当前企业现有员工数作为序号基准
    stmt = select(func.count(WorkforceProfile.id)).where(WorkforceProfile.enterprise_id == enterprise_id)
    result = await db.execute(stmt)
    count = result.scalar() or 0

    seq = count + 1
    badge = f"ATE-{year}-{dept_code}-{seq:03d}"

    # 避免冲突碰撞检查
    existing = await db.execute(select(WorkforceProfile.id).where(WorkforceProfile.employee_badge == badge))
    if existing.scalar():
        suffix = uuid.uuid4().hex[:4].upper()
        badge = f"ATE-{year}-{dept_code}-{seq:03d}-{suffix}"

    return badge


async def create_workforce_profile(
    db: AsyncSession,
    enterprise_id: str,
    payload: WorkforceProfileCreate,
) -> WorkforceProfile:
    """创建并保存数字员工名牌档案。"""
    badge = await generate_next_badge(db, enterprise_id, payload.department)

    profile = WorkforceProfile(
        id=str(uuid.uuid4()),
        enterprise_id=enterprise_id,
        agent_id=payload.agent_id,
        employee_badge=badge,
        display_name=payload.display_name,
        job_title=payload.job_title,
        department=payload.department,
        duty_boundaries=payload.duty_boundaries.model_dump(),
        tone_style=payload.tone_style,
        authorized_flows=payload.authorized_flows,
        accessible_knowledge_buckets=payload.accessible_knowledge_buckets,
        authorized_tools=payload.authorized_tools,
        employment_status=payload.employment_status,
        performance_score=payload.performance_score,
        avatar_url=payload.avatar_url,
    )

    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    logger.info(f"成功创建数字员工档案: {profile.employee_badge} ({profile.display_name})")
    return profile


async def get_workforce_profile(
    db: AsyncSession,
    profile_id: str,
) -> Optional[WorkforceProfile]:
    """按 ID 查询员工档案。"""
    stmt = select(WorkforceProfile).where(WorkforceProfile.id == profile_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_workforce_profiles(
    db: AsyncSession,
    enterprise_id: str,
    status: Optional[str] = None,
    department: Optional[str] = None,
) -> List[WorkforceProfile]:
    """查询企业的数字员工花名册列表。"""
    stmt = select(WorkforceProfile).where(WorkforceProfile.enterprise_id == enterprise_id)
    if status:
        stmt = stmt.where(WorkforceProfile.employment_status == status)
    if department:
        stmt = stmt.where(WorkforceProfile.department == department)

    stmt = stmt.order_by(WorkforceProfile.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_workforce_profile(
    db: AsyncSession,
    profile_id: str,
    payload: WorkforceProfileUpdate,
) -> Optional[WorkforceProfile]:
    """更新数字员工名牌档案。"""
    profile = await get_workforce_profile(db, profile_id)
    if not profile:
        return None

    update_data = payload.model_dump(exclude_unset=True)
    if "duty_boundaries" in update_data and update_data["duty_boundaries"] is not None:
        if hasattr(update_data["duty_boundaries"], "model_dump"):
            update_data["duty_boundaries"] = update_data["duty_boundaries"].model_dump()

    for key, value in update_data.items():
        setattr(profile, key, value)

    await db.commit()
    await db.refresh(profile)
    return profile


async def delete_workforce_profile(
    db: AsyncSession,
    profile_id: str,
) -> bool:
    """删除数字员工档案。"""
    profile = await get_workforce_profile(db, profile_id)
    if not profile:
        return False

    await db.delete(profile)
    await db.commit()
    return True


def check_duty_boundary(
    profile: WorkforceProfile,
    action_intent: str,
) -> DutyBoundaryCheckResponse:
    """校验某意图或操作是否符合该员工的岗位边界守则。"""
    intent_lower = action_intent.lower().strip()
    boundaries = profile.duty_boundaries or {}
    forbidden_list = boundaries.get("forbidden", [])
    allowed_list = boundaries.get("allowed", [])

    # 1. 优先禁止项检查（高风险越权行为）
    for forbidden in forbidden_list:
        if forbidden.lower() in intent_lower or intent_lower in forbidden.lower():
            return DutyBoundaryCheckResponse(
                allowed=False,
                reason=f"触发岗位禁止越权边界项: '{forbidden}'，已阻断或需人工复核",
                matched_boundary=forbidden,
            )

    # 2. 若配置了白名单且不为空，则需匹配允许项
    if allowed_list:
        for allowed in allowed_list:
            if allowed.lower() in intent_lower or intent_lower in allowed.lower():
                return DutyBoundaryCheckResponse(
                    allowed=True,
                    reason=f"匹配岗位职责授权项: '{allowed}'",
                    matched_boundary=allowed,
                )
        return DutyBoundaryCheckResponse(
            allowed=False,
            reason="未在岗位明确授权的允许清单中，默认最小权限拦截",
            matched_boundary=None,
        )

    # 默认放行
    return DutyBoundaryCheckResponse(
        allowed=True,
        reason="未触发任何禁止边界项，允许继续履约",
        matched_boundary=None,
    )
