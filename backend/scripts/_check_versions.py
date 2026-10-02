"""查询 demo 企业的 EnterpriseRuntime 版本。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.database import async_session_factory

TARGET = "9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e"

async def main():
    async with async_session_factory() as db:
        # 查看 enterprise_runtime 表（版本存储）实际列
        res = await db.execute(text("PRAGMA table_info(enterprise_runtimes)"))
        print("enterprise_runtimes 列:", [r[1] for r in res.fetchall()])
        res = await db.execute(text(
            "SELECT id, version, completeness, is_active, compiled_at "
            "FROM enterprise_runtimes WHERE enterprise_id=:e ORDER BY compiled_at DESC"
        ), {"e": TARGET})
        rows = res.fetchall()
        print(f"demo 企业 enterprise_runtime 版本数: {len(rows)}")
        for r in rows:
            print(f"  id={r[0][:8]} version={r[1]} completeness={r[2]} active={r[3]} compiled_at={r[4]}")

if __name__ == "__main__":
    asyncio.run(main())