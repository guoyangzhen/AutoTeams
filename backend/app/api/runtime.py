"""Enterprise Runtime API 路由（WT2，spec §10.7）。

端点（前缀 ``/api/v1/runtime``，全部无尾斜杠）：
- GET    /{enterprise_id}                       当前激活 Runtime
- GET    /{enterprise_id}/versions              版本列表（分页）
- GET    /{enterprise_id}/versions/{version}    按版本获取 Runtime
- POST   /{enterprise_id}/rollback              回滚到指定版本（admin）
- GET    /{enterprise_id}/diff                  两版本 diff（query: a, b）
- GET    /{enterprise_id}/organization          组织运行时
- GET    /{enterprise_id}/agents                Agent 配置模板列表
- GET    /{enterprise_id}/processes             流程引擎列表

工程约束（spec §2.1 / §2.2）：
- 无尾斜杠（路径用 "/{...}" 而非 "/{...}/"）
- 列表端点含 limit/offset 分页（Query(ge=1, le=100) / Query(ge=0)）
- 鉴权：get_current_user；企业隔离 + 管理员校验
- 限流：rate_limit_api / rate_limit_admin
- 敏感字段过滤：非管理员响应中剔除 Agent 的 system_prompt / permissions 与工具 config
- 错误响应统一使用 ErrorCode 常量
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enterprise import Enterprise
from app.models.user import User
from app.schemas.runtime import (
    RuntimeDiffResponse,
    RuntimeRollbackRequest,
    RuntimeRollbackResponse,
    RuntimeVersionItem,
    RuntimeVersionListResponse,
)
from app.services.runtime import (
    diff_versions,
    get_active_runtime,
    get_agent_templates,
    get_organization,
    get_process_engines,
    get_runtime_version,
    list_versions,
    rollback_to_version,
)
from app.utils.error_codes import ErrorCode
from app.utils.rate_limit import rate_limit_admin, rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runtime", tags=["Enterprise Runtime"])


# ============================================================
# 权限与敏感字段处理
# ============================================================


def _verify_enterprise_access(current_user: User, enterprise_id: str) -> None:
    """校验当前用户属于目标企业（或为系统超管）。

    - 系统超管（enterprise_id is None 且 role == 'admin'）：放行
    - 企业成员（enterprise_id 匹配）：放行
    - 其他：403
    """
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)


def _verify_enterprise_admin(current_user: User, enterprise_id: str) -> None:
    """校验当前用户是目标企业的管理员（或系统超管）。"""
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if (
        current_user.enterprise_id != enterprise_id
        or current_user.role != "admin"
    ):
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


async def _ensure_enterprise_exists(db: AsyncSession, enterprise_id: str) -> Enterprise:
    """校验企业存在且未软删除，返回 Enterprise。"""
    result = await db.execute(select(Enterprise).where(Enterprise.id == enterprise_id))
    enterprise = result.scalar_one_or_none()
    if not enterprise:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)
    if not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_DELETED)
    return enterprise


def _is_admin(current_user: User) -> bool:
    return current_user.role == "admin"


def _redact_runtime_data(data: dict, is_admin: bool) -> dict:
    """对非管理员剔除 Runtime 中的敏感字段（system_prompt / permissions / 工具 config）。

    管理员返回原数据；非管理员返回脱敏副本。符合 spec §2.1「企业相关响应
    对非管理员排除敏感字段」。
    """
    if is_admin:
        return data
    # 浅拷贝后脱敏，避免修改缓存/原始对象
    redacted = dict(data)
    agents = redacted.get("agents")
    if isinstance(agents, list):
        redacted_agents = []
        for a in agents:
            if isinstance(a, dict):
                ra = dict(a)
                ra["system_prompt"] = ""
                ra["permissions"] = []
                # 工具绑定中的权限令牌也脱敏
                tools = ra.get("tools")
                if isinstance(tools, list):
                    ra["tools"] = [{**t, "permissions": []} for t in tools if isinstance(t, dict)]
                redacted_agents.append(ra)
            else:
                redacted_agents.append(a)
        redacted["agents"] = redacted_agents
    # 工具注册表中的调用配置可能含凭据，脱敏
    tool_registry = redacted.get("tool_registry")
    if isinstance(tool_registry, list):
        redacted["tool_registry"] = [
            ({**t, "config": {}} if isinstance(t, dict) else t) for t in tool_registry
        ]
    return redacted


# ============================================================
# 端点
# ============================================================


@router.get("/{enterprise_id}")
@rate_limit_api()
async def get_current_runtime(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取企业当前激活的 Runtime（RuntimeCompileResult）。

    管理员返回完整数据；非管理员返回脱敏数据（剔除 system_prompt / permissions / 工具 config）。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    result = await get_active_runtime(db, enterprise_id)
    if result is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)

    data = result.model_dump(mode="json")
    data = _redact_runtime_data(data, _is_admin(current_user))
    return success_response(data)


@router.get("/{enterprise_id}/versions")
@rate_limit_api()
async def list_runtime_versions(
    request: Request,
    enterprise_id: str,
    limit: int = Query(20, ge=1, le=100, description="每页数量（1-100）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出企业的所有 Runtime 版本（分页，最新在前）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    versions, total = await list_versions(db, enterprise_id, limit=limit, offset=offset)
    items = [
        RuntimeVersionItem(
            version=v.version,
            compiled_at=v.compiled_at,
            completeness=v.completeness,
            is_active=v.is_active,
            model_version=v.model_version,
            runtime_id=v.id,
        )
        for v in versions
    ]
    return success_response(
        RuntimeVersionListResponse(
            items=items, total=total, limit=limit, offset=offset
        ).model_dump(mode="json")
    )


@router.get("/{enterprise_id}/versions/{version}")
@rate_limit_api()
async def get_runtime_by_version(
    request: Request,
    enterprise_id: str,
    version: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按版本号获取 Runtime（RuntimeCompileResult）。

    管理员返回完整数据；非管理员返回脱敏数据。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    result = await get_runtime_version(db, enterprise_id, version)
    if result is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)

    data = result.model_dump(mode="json")
    data = _redact_runtime_data(data, _is_admin(current_user))
    return success_response(data)


@router.post("/{enterprise_id}/rollback")
@rate_limit_admin()
async def rollback_runtime(
    request: Request,
    enterprise_id: str,
    data: RuntimeRollbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """回滚到指定 Runtime 版本（管理员）。

    回滚前会为当前所有 Agent 创建配置快照（PRD §4.6.3：Agent 配置一并回滚）。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_admin(current_user, enterprise_id)

    try:
        runtime = await rollback_to_version(
            db,
            enterprise_id=enterprise_id,
            target_version=data.target_version,
            created_by=current_user.id,
        )
    except ValueError as e:
        # 版本不存在
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND) from e

    return success_response(
        RuntimeRollbackResponse(
            new_active_version=runtime.version,
            status="rolled_back",
            runtime_id=runtime.id,
        ).model_dump(mode="json")
    )


@router.get("/{enterprise_id}/diff")
@rate_limit_api()
async def diff_runtime_versions(
    request: Request,
    enterprise_id: str,
    a: str = Query(..., description="起始版本号"),
    b: str = Query(..., description="目标版本号"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对比两个 Runtime 版本（结构化差异 + 人类可读摘要）。

    仅返回变更项的 key 与类型，不包含原始敏感配置，企业成员可查看。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    try:
        diff: RuntimeDiffResponse = await diff_versions(db, enterprise_id, a, b)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND) from e

    return success_response(diff.model_dump(mode="json"))


@router.get("/{enterprise_id}/organization")
@rate_limit_api()
async def get_runtime_organization(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取组织运行时（部门 + 汇报关系树）。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    org = await get_organization(db, enterprise_id)
    if org is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    return success_response(org.model_dump(mode="json"))


@router.get("/{enterprise_id}/agents")
@rate_limit_api()
async def get_runtime_agents(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取 Agent 配置模板列表。

    管理员返回完整模板；非管理员返回脱敏模板（剔除 system_prompt / permissions）。
    """
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    agents = await get_agent_templates(db, enterprise_id)
    agent_dicts = [a.model_dump(mode="json") for a in agents]
    if not _is_admin(current_user):
        redacted = []
        for a in agent_dicts:
            a["system_prompt"] = ""
            a["permissions"] = []
            tools = a.get("tools")
            if isinstance(tools, list):
                a["tools"] = [{**t, "permissions": []} for t in tools if isinstance(t, dict)]
            redacted.append(a)
        agent_dicts = redacted
    return success_response({"agents": agent_dicts})


@router.get("/{enterprise_id}/processes")
@rate_limit_api()
async def get_runtime_processes(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取流程引擎实例列表。"""
    await _ensure_enterprise_exists(db, enterprise_id)
    _verify_enterprise_access(current_user, enterprise_id)

    processes = await get_process_engines(db, enterprise_id)
    return success_response(
        {"processes": [p.model_dump(mode="json") for p in processes]}
    )
