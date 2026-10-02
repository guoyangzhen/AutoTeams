"""一次性首管理员 bootstrap（AUD-12）。

背景
----
生产环境 `REGISTRATION_ENABLED` 默认关闭（公开注册会允许任意互联网用户建企提权），
而演示用的 `seed_demo` 在生产编排里是禁用的。结果是：一台全新机器按文档启动后，
**没有任何可用方式创建第一个企业管理员**。

本脚本提供唯一且可审计的初始化路径：

* **一次性**：执行后写入 `bootstrap_state` 审计记录，同一企业不能重复 bootstrap；
* **无固定口令**：密码从环境变量 `BOOTSTRAP_ADMIN_PASSWORD` 读取，或由
  `--generate` 现场生成随机强口令并**只打印一次**；脚本内没有任何默认口令；
* **可审计**：创建企业 + 管理员 + `bootstrap_state` 审计记录，行为可回溯；
* **失败关闭**：口令强度不足、账号已存在、企业已有管理员时直接拒绝，不静默跳过。

用法::

    cd backend
    BOOTSTRAP_ADMIN_PASSWORD='<强口令>' python -m scripts.bootstrap_admin \
        --email admin@example.com --enterprise "示例企业" --name "管理员"

    # 或者让脚本生成随机口令（只打印一次，务必立即保存）
    python -m scripts.bootstrap_admin --email admin@example.com \
        --enterprise "示例企业" --name "管理员" --generate
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import secrets
import sys
import uuid

from sqlalchemy import func, select

from app.database import async_session_factory
from app.models.enterprise import Enterprise
from app.models.user import User
from app.utils.audit import log_audit
from app.utils.auth.password import get_password_hash

MIN_PASSWORD_LENGTH = 12
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BootstrapError(RuntimeError):
    """bootstrap 前置条件不满足。"""


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BootstrapError(
            f"口令强度不足：至少 {MIN_PASSWORD_LENGTH} 个字符（建议同时包含大小写与数字）"
        )
    if password.lower() == password or password.upper() == password:
        raise BootstrapError("口令强度不足：不要使用全大写或全小写")


def _resolve_password(generate: bool) -> str:
    """从环境变量读取口令，或现场生成随机强口令。

    刻意**不**经过 `Settings`：一次性引导口令不应该落进任何可能被打印、
    缓存或序列化的地方。
    """
    if generate:
        return secrets.token_urlsafe(18)
    value = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    if not value:
        value = getpass.getpass("请输入首管理员口令（不会回显）: ")
    return value.strip()


async def bootstrap(
    *,
    email: str,
    enterprise_name: str,
    admin_name: str,
    password: str,
) -> dict:
    """创建企业 + 首个管理员 + 审计记录。"""
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise BootstrapError(f"邮箱格式非法: {email}")
    _validate_password(password)
    if not enterprise_name.strip():
        raise BootstrapError("企业名称不能为空")
    if not admin_name.strip():
        raise BootstrapError("管理员姓名不能为空")

    async with async_session_factory() as db:
        existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing is not None:
            raise BootstrapError(f"该邮箱已存在用户: {email}")

        # 一次性：系统里已有任何企业管理员时拒绝二次 bootstrap。
        enterprises = (await db.execute(select(Enterprise))).scalars().all()
        for enterprise in enterprises:
            members = (
                await db.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.enterprise_id == enterprise.id, User.role == "admin")
                )
            ).scalar_one()
            if members:
                raise BootstrapError(
                    f"企业「{enterprise.name}」已存在管理员账号，拒绝重复 bootstrap。"
                    "如需新增管理员，请走邀请流程。"
                )

        enterprise = Enterprise(id=str(uuid.uuid4()), name=enterprise_name.strip())
        db.add(enterprise)
        await db.flush()

        admin = User(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=get_password_hash(password),
            name=admin_name.strip(),
            role="admin",
            enterprise_id=enterprise.id,
            is_active=True,
        )
        db.add(admin)
        await db.flush()

        # 可审计：把"这是一次 bootstrap"写进审计链，而不是只留在 shell 历史里。
        await log_audit(
            db,
            admin,
            "bootstrap",
            "enterprise",
            enterprise.id,
            details={
                "enterprise_name": enterprise.name,
                "admin_email": email,
                "channel": "one_time_cli_bootstrap",
            },
        )
        await db.commit()

    return {
        "enterprise_id": enterprise.id,
        "enterprise_name": enterprise.name,
        "admin_email": email,
        "admin_id": admin.id,
    }


async def _main() -> int:
    parser = argparse.ArgumentParser(description="创建首个企业管理员（一次性）")
    parser.add_argument("--email", required=True, help="管理员邮箱")
    parser.add_argument("--enterprise", required=True, help="企业名称")
    parser.add_argument("--name", default="管理员", help="管理员姓名")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="生成随机强口令并只打印一次（不要在共享 shell 使用）",
    )
    args = parser.parse_args()

    try:
        password = _resolve_password(args.generate)
        result = await bootstrap(
            email=args.email,
            enterprise_name=args.enterprise,
            admin_name=args.name,
            password=password,
        )
    except BootstrapError as exc:
        print(f"bootstrap 失败: {exc}", file=sys.stderr)
        return 1

    print("bootstrap 成功：")
    for key, value in result.items():
        print(f"  {key}: {value}")
    if args.generate:
        print(f"\n请立即保存该口令（只显示这一次）:\n  {password}")
    print("\n下一步：登录前端，创建 Agent 并下发第一个任务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
