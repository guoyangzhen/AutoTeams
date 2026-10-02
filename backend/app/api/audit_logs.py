"""审计日志 API。

提供审计日志的分页查询与 CSV 导出能力，满足合规审计需求。

3.1.5: LIKE 通配符转义，避免侧信道信息泄露
3.2.5: CSV 导出改用流式生成器，分批 yield 降低内存峰值
3.5.2: /verify 改用 GET，符合 RESTful（纯查询操作）
"""
import csv
import io
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit_log import AuditLogListResponse, AuditLogResponse
from app.utils.audit import log_audit, verify_audit_chain
from app.utils.rbac import require_admin
from app.utils.rate_limit import rate_limit_api, rate_limit_admin
from app.utils.response import success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit-logs", tags=["审计日志"])

MAX_LIMIT = 500
DEFAULT_LIMIT = 50

# 3.2.5: CSV 流式导出批次大小，平衡内存与查询往返开销
CSV_EXPORT_BATCH_SIZE = 500
CSV_EXPORT_MAX_ROWS = 10000

# 3.1.5: LIKE 转义字符（与 _escape_like_pattern 配合）
_LIKE_ESCAPE_CHAR = "\\"

CSV_HEADERS = [
    "id",
    "user_id",
    "action",
    "resource_type",
    "resource_id",
    "ip_address",
    "user_agent",
    "details",
    "created_at",
]


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """将 ISO 8601 字符串解析为 UTC datetime。"""
    if not value:
        return None
    try:
        # 兼容带 Z 与不带 Z 的格式
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _escape_like_pattern(value: str, escape_char: str = _LIKE_ESCAPE_CHAR) -> str:
    """3.1.5: 转义 SQL LIKE/ILIKE 通配符，避免侧信道信息泄露。

    用户输入的 `%` 可匹配所有记录、`_` 可枚举字段长度，构成侧信道泄露。
    通过反斜杠转义后，用户输入的通配符会被当作字面量匹配。
    顺序很重要：必须先转义反斜杠本身，再转义 % 和 _。
    """
    return (
        value.replace(escape_char, escape_char * 2)
        .replace("%", escape_char + "%")
        .replace("_", escape_char + "_")
    )


async def _build_audit_query(
    db: AsyncSession,
    current_user: User,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    keyword: Optional[str] = None,
):
    """构建带企业隔离与筛选条件的审计日志查询。

    - 超级管理员（enterprise_id is None）可查看全部日志
    - 企业管理员只能查看本企业用户的日志（通过 user.enterprise_id 关联）
    """
    query = select(AuditLog)

    # 企业隔离：非超级管理员只能看本企业用户的操作
    if current_user.enterprise_id:
        query = query.join(User, AuditLog.user_id == User.id).where(
            User.enterprise_id == current_user.enterprise_id
        )

    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)
    if keyword:
        # 3.1.5: 转义 LIKE 通配符，避免用户输入 %/_ 被当作通配符
        escaped_keyword = _escape_like_pattern(keyword)
        like_pattern = f"%{escaped_keyword}%"
        query = query.where(
            (AuditLog.resource_id.ilike(like_pattern, escape=_LIKE_ESCAPE_CHAR))
            | (AuditLog.action.ilike(like_pattern, escape=_LIKE_ESCAPE_CHAR))
            | (AuditLog.resource_type.ilike(like_pattern, escape=_LIKE_ESCAPE_CHAR))
        )

    return query


def _format_csv_row(log: AuditLog) -> list:
    """格式化单条审计日志为 CSV row。"""
    return [
        log.id,
        log.user_id or "",
        log.action,
        log.resource_type or "",
        log.resource_id or "",
        log.ip_address or "",
        log.user_agent or "",
        str(log.details or ""),
        log.created_at.isoformat() if log.created_at else "",
    ]


async def _stream_audit_logs_csv(
    db: AsyncSession,
    base_query,
    total: int,
    batch_size: int = CSV_EXPORT_BATCH_SIZE,
) -> AsyncIterator[bytes]:
    """3.2.5: 异步生成器，分批查询审计日志并流式输出 CSV bytes。

    替代一次性 `result.scalars().all()` 全量加载，降低内存峰值：
    - 单批最多 `batch_size` 行在内存中（默认 500）
    - 复用请求的 DB session（FastAPI 的 get_db 在 StreamingResponse 完成后才清理）
    - 首批输出 BOM（utf-8-sig）+ CSV header，后续批次仅输出数据行

    Args:
        db: 请求的数据库会话（在流式传输期间保持活跃）
        base_query: 已应用筛选条件但未排序/分页的基础查询
        total: 总行数（已在外部做过 EXPORT_MAX_ROWS 上限校验）
        batch_size: 单批拉取行数
    """
    # 首批：BOM + header（utf-8-sig 在编码时自动加 BOM）
    header_buf = io.StringIO()
    csv.writer(header_buf).writerow(CSV_HEADERS)
    yield header_buf.getvalue().encode("utf-8-sig")
    header_buf.close()

    # 分批拉取 rows
    for offset in range(0, total, batch_size):
        # 3.2.5: 复用请求 session，避免与生产 async_session_factory 解耦后无法被测试 override
        result = await db.execute(
            base_query.order_by(AuditLog.created_at.desc())
            .limit(batch_size)
            .offset(offset)
        )
        logs = result.scalars().all()

        if not logs:
            break

        buf = io.StringIO()
        writer = csv.writer(buf)
        for log in logs:
            writer.writerow(_format_csv_row(log))
        yield buf.getvalue().encode("utf-8")
        buf.close()


@router.get("")
@rate_limit_api()
async def list_audit_logs(
    request: Request,
    action: Optional[str] = Query(None, description="操作类型过滤"),
    resource_type: Optional[str] = Query(None, description="资源类型过滤"),
    user_id: Optional[str] = Query(None, description="用户 ID 过滤"),
    start_date: Optional[str] = Query(None, description="开始时间（ISO 8601）"),
    end_date: Optional[str] = Query(None, description="结束时间（ISO 8601）"),
    keyword: Optional[str] = Query(None, description="关键词搜索"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """分页查询审计日志（管理员权限）。"""
    start_dt = _parse_iso_datetime(start_date)
    end_dt = _parse_iso_datetime(end_date)

    base_query = await _build_audit_query(
        db,
        current_user,
        action=action,
        resource_type=resource_type,
        user_id=user_id,
        start_date=start_dt,
        end_date=end_dt,
        keyword=keyword,
    )

    # 总数
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页查询
    result = await db.execute(
        base_query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    )
    logs = result.scalars().all()

    return success_response(
        AuditLogListResponse(
            total=total,
            logs=[AuditLogResponse.model_validate(log) for log in logs],
            limit=limit,
            offset=offset,
        ).model_dump()
    )


@router.get("/export")
@rate_limit_admin()
async def export_audit_logs(
    request: Request,
    action: Optional[str] = Query(None, description="操作类型过滤"),
    resource_type: Optional[str] = Query(None, description="资源类型过滤"),
    user_id: Optional[str] = Query(None, description="用户 ID 过滤"),
    start_date: Optional[str] = Query(None, description="开始时间（ISO 8601）"),
    end_date: Optional[str] = Query(None, description="结束时间（ISO 8601）"),
    keyword: Optional[str] = Query(None, description="关键词搜索"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """导出审计日志为 CSV（管理员权限）。

    限制单次导出最多 10000 条，避免内存与带宽爆炸。

    3.2.5: 改用流式生成器分批 yield，避免一次性全量加载到内存。
    内存峰值从 N×单行大小 降为 batch_size×单行大小（默认 500 行）。
    """
    start_dt = _parse_iso_datetime(start_date)
    end_dt = _parse_iso_datetime(end_date)

    base_query = await _build_audit_query(
        db,
        current_user,
        action=action,
        resource_type=resource_type,
        user_id=user_id,
        start_date=start_dt,
        end_date=end_dt,
        keyword=keyword,
    )

    # 先计数，超限则拒绝
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    if total > CSV_EXPORT_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"导出数据量超过 {CSV_EXPORT_MAX_ROWS} 条限制，请缩小筛选范围",
        )

    # 3.2.5: 捕获导出开始时间，用于在流式查询中排除本次导出本身产生的审计日志
    # （log_audit 写入后 created_at 会略晚于 pre_export_time，从而被过滤掉）
    pre_export_time = datetime.now(timezone.utc)

    # 记录导出审计日志这一敏感操作（在返回 StreamingResponse 前完成 commit）
    await log_audit(
        db,
        current_user,
        "export",
        "audit_log",
        "",
        request=request,
        details={
            "filters": {
                "action": action,
                "resource_type": resource_type,
                "user_id": user_id,
                "start_date": start_date,
                "end_date": end_date,
                "keyword": keyword,
            },
            "exported_rows": total,
        },
    )
    await db.commit()

    # 构造排除本次导出审计日志的查询（避免导出自身递归包含）
    export_query = base_query.where(AuditLog.created_at < pre_export_time)

    timestamp = pre_export_time.strftime("%Y%m%d_%H%M%S")
    filename = f"audit_logs_{timestamp}.csv"
    return StreamingResponse(
        _stream_audit_logs_csv(db, export_query, total),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/verify")
@rate_limit_admin()
async def verify_audit_log_chain(
    request: Request,
    limit: int = Query(0, ge=0, description="验证最近 N 条（0 表示全部）"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """验证审计日志链的完整性（5.3.7 HMAC 链式防篡改）。

    逐条重新计算 signature 并与存储值比对，检测任何篡改/删除/插入。
    激活原 verify_audit_chain 工具函数，使链式防篡改特性真正可用。

    3.5.2: 改用 GET 实现，符合 RESTful（验证本身为纯查询操作）。
    - GET 天然豁免 CSRF 校验，简化前端调用
    - 验证过程只读，不写日志（避免破坏链）
    - 验证完成后写一条 verify 审计日志（独立于被验证的链段之后），
      此 side-effect 仅为可观测性记录，不影响 RESTful 语义

    - 管理员权限
    - limit=0 表示验证全量；limit>0 仅验证最近 N 条（避免大表全表扫描）
    """
    result = await verify_audit_chain(db, limit=limit)

    # 记录验证操作本身（便于审计谁在何时做了完整性验证）
    await log_audit(
        db,
        current_user,
        "verify",
        "audit_log",
        "",
        request=request,
        details={
            "checked": result.get("checked", 0),
            "valid": result.get("valid", False),
            "limit": limit,
            "broken_at": result.get("broken_at"),
        },
    )
    await db.commit()

    if not result.get("valid", False):
        logger.warning(
            "审计链完整性验证失败: broken_at=%s, message=%s",
            result.get("broken_at"),
            result.get("message"),
        )

    return success_response(result)
