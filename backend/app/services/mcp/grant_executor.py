"""端侧授权目录的只读文件执行器（AUD-01）。

为什么是"授权"而不是"设备"
--------------------------
历史上 MCP 工具用 ``device_id`` 指向端侧设备，这里**不再沿用**：

* 授权目录、授权范围、守护进程是否在线都记录在 ``local_path_grants``；
  ``RunnerDevice`` 只是"某企业登记过某台机器"的档案，既不在线也不带 grant 绑定。
* WebSocket 会话按 **grant** 绑定（``isRunnerReady(grantId)``），端侧进程一次只持有
  一个授权根目录。
* ``local_path_grants.runner_id`` 没有任何唯一约束，同一台机器可以有多个授权目录；
  而 ``RunnerDevice.runner_id`` 由 API 调用方传入。两者只是两个各自声明的字符串，
  **相等不等于同一台已授权机器**。

因此本执行器以 ``grant_id`` 为唯一入口，并**显式**按企业 + 用户 + 授权 ID 三者
过滤；任何一项不匹配都失败关闭，绝不"就近选一个授权"。

租户上下文约定
--------------
本模块自开会话，因此**必须**自己把租户与已认证主体绑定上去，否则受约束运行角色
下策略会以"无租户"拒绝所有行。这里用 ``tenant_scope`` / ``authenticated_user_scope``
（基于 ContextVar token 的作用域管理器）而不是手写 bind + 置空：**进入前的调用方
上下文会被完整还原**，不会因为执行了一次端侧读取就被清掉。

``apply_tenant_context`` 内部直接执行 ``SELECT set_config``，**只在 PostgreSQL 上
可用**；因此显式调用一律按方言守卫。PostgreSQL 上正常情况下引擎的 begin 事件已经
写入 GUC，这里的显式调用只用于"事务早于绑定就已开始"的兜底。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator

from sqlalchemy import select

from app.database import async_session_factory
from app.models.local_path_grant import LocalPathGrant
from app.models.user import User
from app.schemas.local_path import RunLocalTaskRequest
from app.services import runner_session
from app.services.mcp.runner_bridge import (
    ERR_GRANT_NOT_ALLOWED,
    ERR_PATH_NOT_ALLOWED,
    GrantFileRead,
    normalize_device_relative_path,
)
from app.utils.audit import log_audit
from app.utils.db_tenant_context import (
    apply_tenant_context,
    authenticated_user_scope,
    tenant_scope,
)

logger = logging.getLogger(__name__)

#: 允许通过本路径执行的操作。只有读取。
SUPPORTED_OPERATIONS = frozenset({"read_file"})

#: 端侧自身限制 5 MiB，这里再兜一层，避免异常响应把超大字符串带进内存与日志。
_HARD_CONTENT_LIMIT = 8 * 1024 * 1024

#: 远端失败对外呈现的错误文案。**不回传**远端原文，避免泄露内部细节与本地路径。
ERR_RUNNER_UNAVAILABLE = "端侧守护进程不可达或未连接，请确认本地工具桥接已启动"
ERR_RUNNER_REJECTED = "端侧守护进程拒绝执行该请求"
ERR_MALFORMED_RESPONSE = "端侧返回内容不符合预期"


class _PublicError(RuntimeError):
    """对外可见的错误：不携带远端原文。"""


@contextmanager
def _caller_scope(enterprise_id: str, user_id: str) -> Iterator[None]:
    """把租户与已认证主体绑定到当前上下文，退出时**完整还原**调用方的原值。"""
    with tenant_scope(enterprise_id):
        with authenticated_user_scope(user_id):
            yield


async def _apply_tenant_if_postgres(db, enterprise_id: str) -> None:
    """仅在 PostgreSQL 上显式写入租户 GUC。

    ``apply_tenant_context`` 内部执行 ``SELECT set_config``，SQLite 上会直接
    ``OperationalError``。PostgreSQL 上引擎的 begin 事件通常已经写好，这里是
    "事务早于绑定就已开始"时的兜底。
    """
    if db.get_bind().dialect.name == "postgresql":
        await apply_tenant_context(db, enterprise_id)


async def _record_audit(
    *,
    enterprise_id: str,
    user_id: str,
    grant_id: str,
    action: str,
    details: Dict[str, Any],
) -> None:
    """记录端侧读取的成功 / 失败 / 安全拒绝审计。

    **审计里不含任何文件内容**，只记授权、路径与结果状态。审计写入失败不会改变
    调用方已经得到的结果（读取本身已经发生），因此吞掉异常并记录日志；成功、失败
    与安全拒绝三条路径都走这里，处理方式一致。
    """
    try:
        with _caller_scope(enterprise_id, user_id):
            async with async_session_factory() as db:
                await _apply_tenant_if_postgres(db, enterprise_id)
                # 只按 id 取当前调用者本人；审计归属必须来自认证上下文而不是入参。
                user = await db.get(User, user_id)
                if user is None:
                    logger.warning("端侧读取审计跳过：调用者用户不存在")
                    return
                await log_audit(
                    db, user, action, "local_path", grant_id, details=details
                )
                await db.commit()
    except Exception:  # noqa: BLE001 - 审计故障不得影响读取结果
        logger.warning("端侧读取审计写入失败: %s", action, exc_info=True)


async def _load_grant(
    *,
    grant_id: str,
    enterprise_id: str,
    user_id: str,
) -> LocalPathGrant:
    """按「授权 ID + 企业 + 用户」加载**已连接**的授权。

    ``local_path_grants`` 是认证路径表（无 RLS），所以这里的显式
    ``enterprise_id`` / ``user_id`` 过滤就是租户边界本身，不能省略。
    """
    with _caller_scope(enterprise_id, user_id):
        async with async_session_factory() as db:
            await _apply_tenant_if_postgres(db, enterprise_id)
            result = await db.execute(
                select(LocalPathGrant).where(
                    LocalPathGrant.id == grant_id,
                    LocalPathGrant.enterprise_id == enterprise_id,
                    LocalPathGrant.user_id == user_id,
                    LocalPathGrant.status == "connected",
                )
            )
            grant = result.scalar_one_or_none()

    if grant is None:
        # 授权不存在 / 属于别人 / 已撤销 / 离线 —— 对外是同一条消息，
        # 避免用错误差异探测他人资源是否存在。
        raise PermissionError(ERR_GRANT_NOT_ALLOWED)
    if grant.scope not in ("read", "read_write"):
        # 只有这两种 scope 允许读；其余一律失败关闭。
        raise PermissionError(ERR_GRANT_NOT_ALLOWED)
    return grant


def _truncate_utf8(content: str, max_bytes: int) -> tuple[str, bool]:
    """按 UTF-8 **字节**截断。

    ``len(encoded) <= max_bytes`` 时不截断；**正好等于上限也不算截断**，只有超过
    上限才置 ``truncated``。多字节字符不会被切成半个码点。
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content, False
    clipped = encoded[:max_bytes]
    while clipped:
        try:
            return clipped.decode("utf-8"), True
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return "", True


def _classify_failure(detail: str) -> str:
    """把远端错误归类为可安全展示的文案。

    ``dispatch_to_runner`` 会把远端 ``error`` 字段放进异常文本（可能含设备本地路径
    等细节），因此这里只做分类，不回显原文。
    """
    lowered = detail.lower()
    if "不在线" in lowered or "不可达" in lowered or "offline" in lowered:
        return ERR_RUNNER_UNAVAILABLE
    return ERR_RUNNER_REJECTED


async def grant_executor(
    *,
    operation: str,
    grant_id: str,
    enterprise_id: str,
    user_id: str,
    relative_path: str,
    max_bytes: int,
) -> Any:
    """``runner_bridge.GrantExecutor`` 的生产实现（只读）。

    ``PermissionError`` 表示授权不可用（桥接层映射为结构化的"授权不允许"）；
    其余异常一律转成不含远端细节的错误消息。
    """
    if operation not in SUPPORTED_OPERATIONS:
        raise PermissionError(ERR_GRANT_NOT_ALLOWED)

    normalized = normalize_device_relative_path(relative_path)
    if normalized is None:
        # 路径非法与授权不可用同样属于"安全拒绝"，同样留审计。
        await _record_audit(
            enterprise_id=enterprise_id,
            user_id=user_id,
            grant_id=grant_id,
            action="runner_read_file_denied",
            details={"grant_id": grant_id, "reason": "path_not_allowed"},
        )
        raise PermissionError(ERR_PATH_NOT_ALLOWED)

    try:
        grant = await _load_grant(
            grant_id=grant_id, enterprise_id=enterprise_id, user_id=user_id
        )
    except PermissionError:
        # 安全拒绝同样审计，但 reason 不区分"不存在/属于他人/已撤销"，
        # 以免审计本身成为探测他人资源存在性的旁路。
        await _record_audit(
            enterprise_id=enterprise_id,
            user_id=user_id,
            grant_id=grant_id,
            action="runner_read_file_denied",
            details={"grant_id": grant_id, "relative_path": normalized,
                     "reason": "grant_not_allowed"},
        )
        raise

    audit_details: Dict[str, Any] = {
        "grant_id": grant_id,
        "relative_path": normalized,
        "operation": operation,
    }

    try:
        body = await runner_session.dispatch_to_runner(
            grant.id,
            RunLocalTaskRequest(tool="read", path=normalized, content=None),
        )
    except RuntimeError as exc:
        reason = _classify_failure(str(exc))
        await _record_audit(
            enterprise_id=enterprise_id,
            user_id=user_id,
            grant_id=grant_id,
            action="runner_read_file_failed",
            details={**audit_details, "reason": reason},
        )
        raise _PublicError(reason) from None

    # 远端信封必须明确成功，且 content 必须是字符串；否则视为格式异常。
    content = None
    if isinstance(body, dict) and body.get("success") is True:
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            content = data["content"]
    if content is None:
        await _record_audit(
            enterprise_id=enterprise_id,
            user_id=user_id,
            grant_id=grant_id,
            action="runner_read_file_failed",
            details={**audit_details, "reason": "malformed_response"},
        )
        raise _PublicError(ERR_MALFORMED_RESPONSE)

    if len(content) > _HARD_CONTENT_LIMIT:
        content = content[:_HARD_CONTENT_LIMIT]

    clipped, truncated = _truncate_utf8(content, max_bytes)
    await _record_audit(
        enterprise_id=enterprise_id,
        user_id=user_id,
        grant_id=grant_id,
        action="runner_read_file",
        details={**audit_details, "truncated": truncated},
    )
    return GrantFileRead(content=clipped, truncated=truncated)
