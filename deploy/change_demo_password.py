"""修改 demo 账号密码并踢掉所有旧会话（方式A）。

在服务器后端容器内执行：
    sudo docker exec -i autoteams-deploy-backend-1 python /tmp/change_demo_password.py "MayU#Happy@2026n!~"

- 使用与后端一致的 bcrypt 哈希逻辑（get_password_hash）
- 改密后清空 refresh_token_hash / refresh_token_family_id，使所有旧 refresh 会话立即失效
- 幂等：重复执行无害
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.database import async_session_factory
from app.models.user import User
from app.utils.audit import log_audit
from app.utils.auth.password import get_password_hash

DEMO_EMAIL = "demo@autoteams.example"


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python change_demo_password.py <new_password>")
    new_password = sys.argv[1]

    async with async_session_factory() as db:
        user = (await db.execute(
            select(User).where(User.email == DEMO_EMAIL)
        )).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"未找到账号 {DEMO_EMAIL}")

        old_hash_prefix = (user.password_hash or "")[:8]
        user.password_hash = get_password_hash(new_password)
        # 踢掉所有旧会话：清空 refresh token 字段
        user.refresh_token_hash = None
        user.refresh_token_family_id = None
        await db.commit()

        print("=" * 60)
        print(f"已修改密码: {DEMO_EMAIL}")
        print(f"  旧哈希前缀: {old_hash_prefix} -> 新哈希前缀: {user.password_hash[:8]}")
        print(f"  已清空 refresh_token_hash / refresh_token_family_id（旧会话全部失效）")
        print("=" * 60)

        # 记录审计
        try:
            await log_audit(
                db,
                user,
                action="更新",
                resource_type="账号",
                resource_id=user.id,
                details={"note": "修改演示账号密码并重置会话（脚本操作）"},
            )
            await db.commit()
        except Exception as e:  # 审计失败不阻断主流程
            print(f"[warn] 审计记录失败（不影响改密）: {e}")


if __name__ == "__main__":
    asyncio.run(main())