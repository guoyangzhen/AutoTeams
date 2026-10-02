"""Enterprise Runtime 持久化层（WT2）。

实现 PRD §4.6 / §4.6.3 的存储与查询，消费 spec §10.2 的 ``RuntimeCompileResult``。

职责：
- ``save_runtime``：保存新版本（语义化版本递增），切换激活态，写审计事件
- ``get_active_runtime`` / ``get_runtime_by_version`` / ``list_versions``：查询

工程约束（spec §2.2）：
- 所有写操作显式 ``await db.commit()``
- 激活态唯一性由 service 层保证（每企业至多一个 is_active=True）
- 缓存（utils/cache.py）best-effort，Redis 不可用时透明降级
"""
import logging
import re
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise import Enterprise
from app.models.runtime import EnterpriseRuntime, RuntimeVersion
from app.schemas.runtime import RuntimeCompileResult
from app.utils.cache import cache_delete, cache_get_json, cache_set_json
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# Runtime 激活态缓存键前缀（与 cache.py 的 autoteams:cache: 前缀叠加）
_RUNTIME_CACHE_PREFIX = "runtime:active"
# 缓存 TTL（秒）
_RUNTIME_CACHE_TTL = 300

# 语义化版本变更类型（PRD §4.6.3）
ChangeType = str  # "patch" | "minor" | "major"

# 语义化版本正则：可选 "v" 前缀 + major.minor.patch
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def bump_semver(current: Optional[str], change_type: ChangeType = "minor") -> str:
    """按变更类型递增语义化版本号（PRD §4.6.3）。

    - patch（小修改）：v1.0.0 → v1.0.1
    - minor（能力变更）：v1.0.0 → v1.1.0
    - major（重大重构）：v1.0.0 → v2.0.0

    首次保存（current 为空）返回 "v1.0.0"。
    无法解析的版本号回退为 patch 递增或 "v1.0.0"。

    返回值带 "v" 前缀。
    """
    if not current:
        return "v1.0.0"
    match = _SEMVER_RE.match(current.strip())
    if not match:
        # 无法解析：在末尾追加 -next 兜底，保证唯一性
        return f"{current}-next"
    major, minor, patch = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if change_type == "major":
        return f"v{major + 1}.0.0"
    if change_type == "minor":
        return f"v{major}.{minor + 1}.0"
    # patch
    return f"v{major}.{minor}.{patch + 1}"


def _cache_key(enterprise_id: str) -> str:
    return f"{_RUNTIME_CACHE_PREFIX}:{enterprise_id}"


def _invalidate_runtime_cache(enterprise_id: str) -> None:
    """失效某企业激活 Runtime 缓存（best-effort）。"""
    cache_delete(_cache_key(enterprise_id))


def _runtime_data_from_result(result: RuntimeCompileResult) -> dict:
    """将 RuntimeCompileResult 序列化为可存入 JSONB 的 dict。"""
    return result.model_dump(mode="json")


async def _write_audit_log(
    db: AsyncSession,
    user_id: Optional[str],
    *,
    action: str,
    resource_id: str,
    details: dict,
) -> None:
    """写入 HMAC 链式签名审计日志（spec §2.6 安全规范）。

    将 Runtime 版本操作同步写入全局 ``audit_logs`` 表（HMAC 链式签名，由
    ``utils/audit.log_audit`` 完成），满足企业级安全合规要求。与
    ``RuntimeVersion`` 版本事件日志互补：前者是全局不可篡改审计链，后者是
    版本管理领域事件流。

    best-effort 语义：审计写入失败不阻断业务（记录 warning），由调用方在显式
    ``await db.commit()`` 时一并落盘；本函数不单独 commit。

    Args:
        db: 异步 session
        user_id: 操作者用户 ID（可为空；构造轻量 ``User(id=...)`` 传入，
            ``log_audit`` 仅读取 ``user.id``，无需 DB 查询）
        action: 审计动作（``runtime.save`` / ``runtime.rollback`` / ``runtime.snapshot``）
        resource_id: Runtime 版本 ID
        details: 审计详情（版本号、企业 ID 等）
    """
    try:
        from app.models.user import User
        from app.utils.audit import log_audit

        # 构造轻量 User 实例仅用于透传 user.id（log_audit 不查询/持久化该对象）
        audit_user = User(id=user_id) if user_id else None
        await log_audit(
            db,
            audit_user,
            action=action,
            resource_type="enterprise_runtime",
            resource_id=resource_id,
            details=details,
        )
    except Exception as e:  # noqa: BLE001 — 审计失败不应阻断业务流程
        logger.warning("Runtime 审计日志写入失败（非致命）: %s", e, exc_info=True)


async def save_runtime(
    db: AsyncSession,
    enterprise_id: str,
    compile_result: RuntimeCompileResult,
    created_by: Optional[str] = None,
    changelog: Optional[str] = None,
    change_type: ChangeType = "minor",
    version: Optional[str] = None,
) -> EnterpriseRuntime:
    """保存一个新的 Runtime 版本并设为激活。

    流程：
    1. 计算新版本号（``version`` 覆盖优先；否则按 ``change_type`` 递增当前激活版本）
    2. 将该企业现有激活版本置为 is_active=False
    3. 创建新 EnterpriseRuntime 行（is_active=True），runtime_data 存完整 compile_result
    4. 同步 Enterprise.current_runtime_version_id
    5. 写 RuntimeVersion 审计事件（event_type="save"）
    6. 失效缓存
    7. 显式 commit

    Args:
        db: 异步 session
        enterprise_id: 企业 ID
        compile_result: WT1 产出的 Runtime 编译结果（spec §10.2 契约）
        created_by: 操作者用户 ID
        changelog: 变更说明
        change_type: 版本递增类型（patch/minor/major），当 version 未指定时生效
        version: 显式版本号覆盖（须企业内唯一）

    Returns:
        新创建的 EnterpriseRuntime（已 refresh）

    Raises:
        ValueError("runtime_version_exists"): 版本号已被占用
        SQLAlchemyError: 持久化失败
    """
    # 1. 计算新版本号
    if version is None:
        current_active = await get_active_runtime(db, enterprise_id)
        current_version = current_active.version if current_active else None
        new_version = bump_semver(current_version, change_type)
    else:
        new_version = version if version.startswith("v") else f"v{version}"
        # 校验唯一性
        existing = await get_runtime_by_version(db, enterprise_id, new_version)
        if existing is not None:
            raise ValueError("runtime_version_exists")

    # 校验自增版本同样唯一（防御并发）
    existing_for_new = await get_runtime_by_version(db, enterprise_id, new_version)
    if existing_for_new is not None:
        raise ValueError("runtime_version_exists")

    try:
        # 2. 旧激活版本置为 inactive
        await db.execute(
            update(EnterpriseRuntime)
            .where(
                EnterpriseRuntime.enterprise_id == enterprise_id,
                EnterpriseRuntime.is_active.is_(True),
            )
            .values(is_active=False)
        )

        # 3. 创建新版本行
        runtime_data = _runtime_data_from_result(compile_result)
        # 用实际存储版本号覆盖 runtime_data 内嵌的 version 字段，保证
        # 行的 version 列与 runtime_data["version"] 一致（query 层从
        # runtime_data 反序列化 RuntimeCompileResult，二者必须一致）
        runtime_data["version"] = new_version
        now = utcnow()
        runtime = EnterpriseRuntime(
            enterprise_id=enterprise_id,
            version=new_version,
            model_version=compile_result.model_version,
            compiled_at=compile_result.compiled_at or now,
            completeness=float(compile_result.completeness or 0.0),
            runtime_data=runtime_data,
            is_active=True,
            created_by=created_by,
        )
        db.add(runtime)
        await db.flush()

        # 4. 同步 Enterprise.current_runtime_version_id
        await db.execute(
            update(Enterprise)
            .where(Enterprise.id == enterprise_id)
            .values(current_runtime_version_id=runtime.id)
        )

        # 5. 写审计事件
        event = RuntimeVersion(
            runtime_id=runtime.id,
            enterprise_id=enterprise_id,
            version=new_version,
            event_type="save",
            changelog=changelog or f"保存 Runtime 版本 {new_version}",
            is_active=True,
            created_by=created_by,
            metadata_json={"change_type": change_type, "completeness": runtime.completeness},
        )
        db.add(event)

        # 5.1 写 HMAC 链式签名审计日志（spec §2.6）
        await _write_audit_log(
            db,
            created_by,
            action="runtime.save",
            resource_id=runtime.id,
            details={
                "enterprise_id": enterprise_id,
                "version": new_version,
                "change_type": change_type,
                "completeness": float(runtime.completeness),
            },
        )

        # 6/7. 失效缓存 + 显式 commit
        _invalidate_runtime_cache(enterprise_id)
        await db.commit()
        await db.refresh(runtime)

        logger.info(
            "已保存 Runtime 版本（enterprise=%s, version=%s, completeness=%.2f）",
            enterprise_id,
            new_version,
            runtime.completeness,
        )
        return runtime
    except ValueError:
        raise
    except SQLAlchemyError as e:
        logger.error("保存 Runtime 失败: %s", e, exc_info=True)
        raise


async def get_active_runtime(
    db: AsyncSession, enterprise_id: str, use_cache: bool = True
) -> Optional[EnterpriseRuntime]:
    """获取企业当前激活的 Runtime（ORM 行）。

    优先读缓存（best-effort），未命中查 DB 并回填。
    返回的是 ORM ``EnterpriseRuntime`` 对象（runtime_data 为完整 JSONB）。
    """
    if use_cache:
        cached = cache_get_json(_cache_key(enterprise_id), expected_type=dict)
        if cached is not None:
            # 用缓存数据构造一个非托管实例（不 attached to session）
            return EnterpriseRuntime(**_orm_fields_from_cached(cached))
    result = await db.execute(
        select(EnterpriseRuntime).where(
            EnterpriseRuntime.enterprise_id == enterprise_id,
            EnterpriseRuntime.is_active.is_(True),
        )
    )
    runtime = result.scalar_one_or_none()
    if runtime is not None and use_cache:
        cache_set_json(
            _cache_key(enterprise_id),
            runtime.to_dict(),
            ttl=_RUNTIME_CACHE_TTL,
        )
    return runtime


def _orm_fields_from_cached(cached: dict) -> dict:
    """从缓存 dict 中提取 EnterpriseRuntime 构造参数。

    缓存存的是 to_dict() 结果，含 id/enterprise_id/version/.../runtime_data。
    """
    # 仅保留模型可接受的字段，过滤掉 to_dict 中的派生字段
    allowed = {
        "id",
        "enterprise_id",
        "version",
        "model_version",
        "compiled_at",
        "completeness",
        "runtime_data",
        "is_active",
        "created_by",
        "created_at",
        "updated_at",
    }
    return {k: v for k, v in cached.items() if k in allowed}


async def get_runtime_by_version(
    db: AsyncSession, enterprise_id: str, version: str
) -> Optional[EnterpriseRuntime]:
    """按版本号获取 Runtime（ORM 行），版本号不区分 "v" 前缀。"""
    normalized = version if version.startswith("v") else f"v{version}"
    result = await db.execute(
        select(EnterpriseRuntime).where(
            EnterpriseRuntime.enterprise_id == enterprise_id,
            EnterpriseRuntime.version == normalized,
        )
    )
    return result.scalar_one_or_none()


async def list_versions(
    db: AsyncSession,
    enterprise_id: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[EnterpriseRuntime], int]:
    """分页列出企业的所有 Runtime 版本（最新在前）。

    Returns:
        (versions, total)
    """
    base_query = select(EnterpriseRuntime).where(
        EnterpriseRuntime.enterprise_id == enterprise_id
    )
    count_query = select(func.count()).select_from(
        select(EnterpriseRuntime).where(
            EnterpriseRuntime.enterprise_id == enterprise_id
        ).subquery()
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        base_query.order_by(EnterpriseRuntime.compiled_at.desc()).limit(limit).offset(offset)
    )
    versions = list(result.scalars().all())
    return versions, total
