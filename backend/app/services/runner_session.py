"""本地路径授权服务 —— 协作工作台与本地工具桥接。

职责：
1. register：创建授权记录，签发一次性 setup token（仅存哈希），生成 setup 命令。
2. claim：校验一次性 setup token 并标记「已认领」（供 collaboration-service 经
   Runner 外连时调用）。
3. mark_connected：Runner 本地校验通过后回填 runner_id / resolved_path /
   tool_manifest 并置为 connected。
4. dispatch_to_runner：把「校验 / 执行任务」代理到 collaboration-service，
   由其把任务下发到已外连的本地守护进程（Runner）。

安全：
- setup token 只存 SHA-256 哈希；认领后立即失效。
- 企业隔离 + RBAC 由 API 层校验；此处专注授权记录与 token 语义。
"""
import hashlib
import httpx
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.local_path_grant import LocalPathGrant
from app.schemas.local_path import RunLocalTaskRequest

logger = logging.getLogger(__name__)


def hash_token(token: str) -> str:
    """对明文 setup token 做 SHA-256 哈希（只存哈希，不落明文）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_setup_token() -> str:
    """生成一次性 setup token（URL 安全随机串）。"""
    return secrets.token_urlsafe(32)


def build_setup_command(grant_id: str, token: str, local_path: str, scope: str) -> str:
    """生成用户复制到本机终端即可运行的连接命令（单行，极度易用）。

    命令格式：autoteams-runner connect --server <bridge> --token <token> --grant <grant> --path "<本地路径>" --scope <scope>
    其中 <bridge> 指向 collaboration-service 的 /bridge WebSocket 端点。

    云端部署时优先使用 RUNNER_PUBLIC_BRIDGE_URL（公网域名根，nginx 把 /bridge 反代到协作服务），
    保证用户本机 Runner 能通过公网连到云端；为空时回退到 COLLAB_SERVICE_URL（本地开发直连）。
    """
    bridge_base = (
        settings.RUNNER_PUBLIC_BRIDGE_URL
        or settings.COLLAB_SERVICE_URL
        or "http://127.0.0.1:3001"
    )
    bridge = bridge_base.replace("http://", "ws://").replace(
        "https://", "wss://"
    ).rstrip("/")
    # 双引号包裹本地路径，兼容含空格/中文的路径；转义可能与引号冲突的字符
    escaped_path = local_path.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'autoteams-runner connect --server {bridge}/bridge'
        f' --token {token}'
        f' --grant {grant_id}'
        f' --path "{escaped_path}"'
        f' --scope {scope}'
    )


async def register_grant(
    db: AsyncSession,
    enterprise_id: str,
    user_id: str,
    local_path: str,
    scope: str,
    label: Optional[str],
) -> tuple[LocalPathGrant, str, datetime]:
    """创建授权记录并签发一次性 setup token。

    Returns:
        (grant, setup_token, expires_at)
    """
    token = generate_setup_token()
    # P1-3: 将 30 天静态长期 Token 缩短为 10 分钟一次性配对令牌
    ttl_minutes = getattr(settings, "RUNNER_SETUP_TOKEN_TTL_MINUTES", 10)
    expires_at = _now() + timedelta(minutes=ttl_minutes)

    grant = LocalPathGrant(
        enterprise_id=enterprise_id,
        user_id=user_id,
        label=label,
        local_path=local_path,
        scope=scope,
        status="pending",
        runner_id=None,
        tool_manifest=None,
        resolved_path=None,
        setup_token_hash=hash_token(token),
        setup_token_expires_at=expires_at,
        claimed=False,
    )
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return grant, token, expires_at


async def claim_grant(db: AsyncSession, grant: LocalPathGrant, setup_token: str) -> bool:
    """校验 setup token 并标记已认领（一次性消费，防止凭证长期暴露与重放冒充 P1-3）。"""
    if grant.status == "revoked":
        return False
    if not grant.setup_token_hash:
        return False
    # 已经被认领过的一律拒绝重复认领（一次性消费，杜绝重放冒充）
    if grant.claimed:
        logger.warning(f"[RunnerSecurity] 授权 {grant.id} 尝试重复消费已失效的 setup_token，拒绝连接！")
        return False
    # SQLite 存储的 DateTime 无时区（naive），补上 UTC 时区后再与当前时间比较
    expires = grant.setup_token_expires_at
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < _now():
            return False
    if not secrets.compare_digest(grant.setup_token_hash, hash_token(setup_token)):
        return False
    # 用数据库条件更新原子消费令牌。两个请求可能各自持有尚未认领的 ORM 快照，
    # 只有第一个成功更新的请求才算认领成功。
    result = await db.execute(
        update(LocalPathGrant)
        .where(
            LocalPathGrant.id == grant.id,
            LocalPathGrant.claimed.is_(False),
            LocalPathGrant.status != "revoked",
            LocalPathGrant.setup_token_hash == hash_token(setup_token),
        )
        .values(claimed=True, setup_token_hash=None, setup_token_expires_at=None)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        await db.rollback()
        return False
    await db.commit()
    await db.refresh(grant)
    return True


async def mark_connected(
    db: AsyncSession,
    grant: LocalPathGrant,
    runner_id: str,
    resolved_path: Optional[str],
    tool_manifest: Optional[dict],
) -> bool:
    """Runner 本地校验通过后回填信息并置为 connected。"""
    # API 层读取授权与本次提交之间可能发生撤销；状态条件必须在写入时检查。
    result = await db.execute(
        update(LocalPathGrant)
        .where(LocalPathGrant.id == grant.id, LocalPathGrant.status != "revoked")
        .values(
            runner_id=runner_id,
            resolved_path=resolved_path,
            tool_manifest=tool_manifest,
            status="connected",
        )
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        await db.rollback()
        return False
    await db.commit()
    return True


async def mark_offline(db: AsyncSession, grant: LocalPathGrant) -> None:
    """Runner 断线时置为 offline（保留授权记录，等待重连）。"""
    result = await db.execute(
        update(LocalPathGrant)
        .where(LocalPathGrant.id == grant.id, LocalPathGrant.status == "connected")
        .values(status="offline")
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount == 1:
        await db.commit()
    else:
        await db.rollback()


async def revoke_grant(db: AsyncSession, grant: LocalPathGrant) -> None:
    """撤销授权：置为 revoked 并清空 token。"""
    grant.status = "revoked"
    grant.claimed = True
    grant.setup_token_hash = None
    grant.setup_token_expires_at = None
    await db.commit()


async def dispatch_to_runner(
    grant_id: str,
    task: RunLocalTaskRequest,
    timeout: float = 130.0,
) -> dict:
    """把本地任务代理到 collaboration-service，由其下发给已外连的 Runner。

    服务级鉴权：携带 X-Bridge-Secret（Backend 与协作服务共享的内部密钥），
    不透传用户 Cookie / CSRF（协作服务 /api/local/run 仅信任内部密钥）。
    """
    collab_base = (settings.COLLAB_SERVICE_URL or "http://127.0.0.1:3001").rstrip("/")
    url = f"{collab_base}/api/local/run"
    headers = {
        "Content-Type": "application/json",
        "X-Bridge-Secret": settings.BRIDGE_INTERNAL_SECRET,
    }

    # 仅转发受支持的受限文件操作参数；schema 已拒绝 cli/agentic，
    # 因此不会把命令、工作目录或 Agentic 提示词带入协作服务/Runner 链路。
    payload = {
        "grantId": grant_id,
        "tool": task.tool,
        "path": task.path,
        "content": task.content,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("error") or body.get("message") or resp.text
            except Exception:  # noqa: BLE001
                detail = resp.text
            raise RuntimeError(detail)
        body = resp.json()
        return body
    except httpx.HTTPError as e:
        logger.error("转发本地任务到协作服务失败: %s", e)
        raise RuntimeError("本地守护进程服务不可达，请确认协作服务已启动") from e
