"""重新签名审计日志链（一次性数据修复）。

起因：在 .env 配置统一重构（合并为根目录单文件 .env）过程中，
AUDIT_SIGNING_KEY 发生变化，导致此前以旧密钥写入的审计日志在
verify_audit_chain 校验时出现「签名不匹配」。

本脚本用当前生效的密钥，按时间顺序重新计算整条 HMAC 链：
- 仅重签已有 signature 的日志（迁移前的无签名旧日志保持跳过，与校验逻辑一致）；
- 重新串联 prev_hash，保证链条自洽；
- 只更新签名相关字段，不改动任何业务内容。

用法（在 backend 目录下执行）：
    cd d:\\AIProjects\\AutoTeams\\backend
    python -m scripts.resign_audit_chain
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory
from app.models.audit_log import AuditLog
from app.utils.audit import GENESIS_HASH, _compute_signature, verify_audit_chain


async def resign() -> None:
    async with async_session_factory() as db:
        result = await db.execute(select(AuditLog).order_by(AuditLog.created_at.asc()))
        logs = result.scalars().all()

        expected_prev = GENESIS_HASH
        resigned = 0
        for log in logs:
            if log.signature is None:
                continue  # 迁移前的无签名旧日志，校验时跳过，不参与链
            log.prev_hash = expected_prev
            log.signature = _compute_signature(log)
            expected_prev = log.signature
            resigned += 1

        await db.commit()
        print(f"已重签 {resigned} 条审计日志（当前密钥 AUDIT_SIGNING_KEY）")

        # 重签后立即自检
        check = await verify_audit_chain(db, limit=0)
        print(f"自检结果: valid={check['valid']} checked={check['checked']} "
              f"broken_at={check['broken_at']}")
        print(f"message: {check['message']}")


if __name__ == "__main__":
    asyncio.run(resign())