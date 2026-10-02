"""记录关键操作到审计日志，并维护可验证的 HMAC 哈希链。

每次写入都会锁定 ``audit_chain_state`` 的全局游标行，在同一数据库事务内追加
AuditLog 并推进链尾。因此 API、编译 Worker、Agent Worker 或多进程 Web 服务共享
同一条顺序链，不依赖任何进程内缓存。
"""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_chain_state import AuditChainState
from app.models.audit_log import MAX_USER_AGENT_LENGTH, AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)

# 首条审计日志的前序签名。
GENESIS_HASH = "0" * 64
AUDIT_CHAIN_STATE_ID = "global"

# 审计签名密钥与 JWT 密钥分离，避免 JWT 轮换天然破坏审计链校验。
if settings.AUDIT_SIGNING_KEY:
    _AUDIT_SIGNING_SECRET = settings.AUDIT_SIGNING_KEY.encode("utf-8")
else:
    _AUDIT_SIGNING_SECRET = settings.JWT_SECRET_KEY.encode("utf-8")
    if not settings.DEBUG:
        logger.warning(
            "AUDIT_SIGNING_KEY 未配置，回退到 JWT_SECRET_KEY 签名审计链。"
            "生产环境建议设置独立密钥，避免 JWT 轮换导致历史审计链校验失败。"
        )


def _compute_signature(audit: AuditLog) -> str:
    """计算覆盖全部业务字段和前序签名的 HMAC-SHA256。"""
    details_json = (
        json.dumps(audit.details, sort_keys=True, ensure_ascii=False, default=str)
        if audit.details
        else ""
    )
    # 固定格式避免 SQLite 读取无时区时间后 isoformat 表示变化。
    ts_str = audit.created_at.strftime("%Y-%m-%dT%H:%M:%S.%f") if audit.created_at else ""
    message = "|".join(
        [
            str(audit.prev_hash or ""),
            str(audit.user_id or ""),
            str(audit.action or ""),
            str(audit.resource_type or ""),
            str(audit.resource_id or ""),
            str(audit.ip_address or ""),
            str(audit.user_agent or ""),
            details_json,
            ts_str,
        ]
    )
    return hmac.new(_AUDIT_SIGNING_SECRET, message.encode("utf-8"), hashlib.sha256).hexdigest()


async def _get_chain_state_for_update(db: AsyncSession) -> AuditChainState:
    """取得并锁定审计链游标。

    迁移会预置全局状态行。为保持隔离测试和旧数据库的兼容性，这里仍以 savepoint
    安全初始化一次：若其他进程抢先插入，唯一键冲突仅回滚 savepoint，随后重新锁行。
    """
    query = (
        select(AuditChainState)
        .where(AuditChainState.id == AUDIT_CHAIN_STATE_ID)
        .with_for_update()
    )
    state = (await db.execute(query)).scalar_one_or_none()
    if state:
        return state

    # 不依赖 created_at 选择链尾：并发请求可能拥有相同时间戳。真正的链尾是
    # "自身签名未被任何日志用作 prev_hash" 的签名；正常单链中它唯一存在。
    referenced_signatures = select(AuditLog.prev_hash).where(AuditLog.prev_hash.isnot(None))
    latest_signature = (
        await db.execute(
            select(AuditLog.signature)
            .where(
                AuditLog.signature.isnot(None),
                AuditLog.signature.not_in(referenced_signatures),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    try:
        async with db.begin_nested():
            db.add(
                AuditChainState(
                    id=AUDIT_CHAIN_STATE_ID,
                    # 兼容已有签名日志的测试库或异常漏迁移库；正常生产升级由迁移预置。
                    last_signature=latest_signature or GENESIS_HASH,
                )
            )
            await db.flush()
    except IntegrityError:
        # 并发首次写入时，另一事务已创建该唯一单例；继续读取并锁定它。
        pass

    state = (await db.execute(query)).scalar_one_or_none()
    if state is None:
        raise RuntimeError("无法初始化审计链状态")
    return state


def reset_audit_cache() -> None:
    """向后兼容的测试钩子。

    审计链不再使用进程内缓存；保留此无副作用函数避免旧测试/调用方失效。
    """
    return None


async def log_audit(
    db: AsyncSession,
    user: User | None,
    action: str,
    resource_type: str,
    resource_id: str,
    request: Optional[Request] = None,
    details: Optional[dict] = None,
) -> None:
    """在调用方事务中安全追加一条审计日志。

    ``FOR UPDATE`` 锁覆盖“读取前序签名—写入本条日志—推进链尾”的整个事务区间。
    调用方仍负责 ``commit``，从而保证业务变更与其审计事件原子提交。

    受约束运行角色下，账本 INSERT 策略要求租户上下文且 ``user_id`` 属于本租户。
    因此"没有用户"或"用户还没有企业"的事件改走数据库受控入口；**这里不会**
    根据传入的 user 去绑定租户 —— 绑定租户必须发生在认证成功的入口。
    """
    state = await _get_chain_state_for_update(db)

    if _needs_bootstrap_audit_channel(db, user):
        try:
            await _append_bootstrap_audit(
                db,
                state,
                user_id=user.id if user else None,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request=request,
                details=details,
            )
        except ProgrammingError as exc:
            # 不做"迁移缺失就退回 ORM 写入"的兼容：那条路径在受约束角色下必然
            # 42501，而且此时事务已 abort，回退只会把明确的部署问题变成模糊的
            # 权限错误。缺迁移就明确失败。
            raise RuntimeError(
                "受控审计入口不可用：请确认数据库已迁移到 d5e6f7a8b9c0"
                f"（原始错误：{exc}）"
            ) from exc
        return

    ua = request.headers.get("user-agent") if request else None
    if ua and len(ua) > MAX_USER_AGENT_LENGTH:
        ua = ua[:MAX_USER_AGENT_LENGTH]

    now = datetime.now(timezone.utc)
    audit = AuditLog(
        id=str(uuid.uuid4()),
        user_id=user.id if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=request.client.host if request and request.client else None,
        user_agent=ua,
        details=details,
        prev_hash=state.last_signature,
        created_at=now,
        updated_at=now,
    )
    audit.signature = _compute_signature(audit)
    db.add(audit)
    # 同一事务内推进链尾；事务失败时两项会一并回滚。
    state.last_signature = audit.signature



#: 受控的"尚无租户主体"审计入口（迁移 d5e6f7a8b9c0）。
#: 主体（user_id）为空 → 匿名安全事件；主体存在 → 必须是还没有企业的账号。
#: 动作白名单、链尾校验和主体判定都在数据库里，应用无法绕过。
BOOTSTRAP_AUDIT_SQL = text(
    "SELECT public.app_append_bootstrap_audit("
    ":id, :user_id, :action, :resource_type, :resource_id, :ip_address, "
    ":user_agent, :details, :created_at, :signature, :prev_hash)"
)


async def _append_bootstrap_audit(
    db: AsyncSession,
    state: AuditChainState,
    *,
    user_id: Optional[str],
    action: str,
    resource_type: Optional[str],
    resource_id: Optional[str],
    request: Optional[Request],
    details: Optional[dict],
) -> None:
    """通过数据库受控入口追加一条审计记录。

    调用方**不能**在这里绑定租户：绑定等于授予该租户的全部可见性，只能由
    认证成功的入口做（``bind_authenticated_tenant``）。这里只处理本来就
    "没有租户主体"的事件。
    """
    ua = request.headers.get("user-agent") if request else None
    if ua and len(ua) > MAX_USER_AGENT_LENGTH:
        ua = ua[:MAX_USER_AGENT_LENGTH]
    now = datetime.now(timezone.utc)
    audit = AuditLog(
        id=str(uuid.uuid4()),
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=request.client.host if request and request.client else None,
        user_agent=ua,
        details=details,
        prev_hash=state.last_signature,
        created_at=now,
        updated_at=now,
    )
    audit.signature = _compute_signature(audit)
    await db.execute(
        BOOTSTRAP_AUDIT_SQL,
        {
            "id": audit.id,
            "user_id": user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip_address": audit.ip_address,
            "user_agent": audit.user_agent,
            "details": json.dumps(details, default=str) if details else None,
            "created_at": now,
            "signature": audit.signature,
            "prev_hash": state.last_signature,
        },
    )
    # 链尾由数据库函数推进；同步本地对象，避免会话后续 flush 把它改回旧值。
    state.last_signature = audit.signature


def _needs_bootstrap_audit_channel(db: AsyncSession, user: Optional[User]) -> bool:
    """是否必须走受控入口。

    只有两种情况：事件本来就没有用户（登录失败/账户锁定），或者主体是尚未
    加入任何企业的账号（公开注册）。这两类记录在任何租户的账本策略下都写不进去，
    必须由数据库判定主体后放行。
    """
    if db.get_bind().dialect.name != "postgresql":
        return False
    return user is None or user.enterprise_id is None

async def verify_audit_chain(db: AsyncSession, limit: int = 0) -> dict:
    """按 ``prev_hash -> signature`` 拓扑校验审计链，检测篡改、删除、插入和分叉。

    ``limit`` 保留以兼容既有 API 查询参数，但完整性判断永远遍历整条签名链；部分
    验证不能证明审计链整体有效，因此不会把它误报为“验证通过”。
    """
    del limit
    logs = (await db.execute(select(AuditLog))).scalars().all()
    signed_logs = [log for log in logs if log.signature is not None]
    by_previous_signature: dict[str, AuditLog] = {}

    for log in signed_logs:
        if _compute_signature(log) != log.signature:
            return {
                "valid": False,
                "checked": 0,
                "broken_at": log.id,
                "message": f"签名不匹配：日志 {log.id} 可能被篡改",
            }
        previous_signature = log.prev_hash or ""
        if previous_signature in by_previous_signature:
            return {
                "valid": False,
                "checked": 0,
                "broken_at": log.id,
                "message": f"链路分叉：多个日志引用同一前序签名 {previous_signature}",
            }
        by_previous_signature[previous_signature] = log

    expected_prev = GENESIS_HASH
    visited_ids: set[str] = set()
    checked = 0
    while expected_prev in by_previous_signature:
        log = by_previous_signature[expected_prev]
        if log.id in visited_ids:
            return {
                "valid": False,
                "checked": checked,
                "broken_at": log.id,
                "message": f"链路循环：日志 {log.id} 被重复引用",
            }
        visited_ids.add(log.id)
        expected_prev = log.signature
        checked += 1

    if len(visited_ids) != len(signed_logs):
        broken_log = next(log for log in signed_logs if log.id not in visited_ids)
        return {
            "valid": False,
            "checked": checked,
            "broken_at": broken_log.id,
            "message": f"链路断裂：日志 {broken_log.id} 无法从创世签名连通",
        }

    state = (await db.execute(
        select(AuditChainState).where(AuditChainState.id == AUDIT_CHAIN_STATE_ID)
    )).scalar_one_or_none()
    if state is not None and state.last_signature != expected_prev:
        return {
            "valid": False,
            "checked": checked,
            "broken_at": None,
            "message": "审计链游标与最后一条有效签名不一致",
        }

    return {
        "valid": True,
        "checked": checked,
        "broken_at": None,
        "message": "审计链完整性验证通过",
    }
