import asyncio, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import async_session_factory
from sqlalchemy import text
from collections import Counter

enterprise = "9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e"

async def main():
    async with async_session_factory() as db:
        r = await db.execute(text(
            "SELECT id, enterprise_id, version, completeness, runtime_data, is_active, created_at "
            "FROM enterprise_runtimes WHERE enterprise_id=:e ORDER BY created_at DESC LIMIT 10"
        ), {"e": enterprise})
        print("=== enterprise_runtimes ===")
        for row in r:
            rd = row.runtime_data
            if isinstance(rd, str):
                try:
                    rd = json.loads(rd)
                except Exception:
                    rd = None
            org = rd.get("organization") if isinstance(rd, dict) else None
            n = 0
            if isinstance(org, dict):
                n = len(org.get("departments", []))
            print(f"  ver={row.version} comp={row.completeness} active={row.is_active} depts={n} created={row.created_at}")

        # agent table columns
        r2 = await db.execute(text("PRAGMA table_info(agents)"))
        cols = [row[1] for row in r2]
        print("\n=== agents columns ===")
        print("  ", cols)

        # department grouping: try config JSON if present
        r3 = await db.execute(text(
            "SELECT name, config FROM agents WHERE enterprise_id=:e"
        ), {"e": enterprise})
        dept_counter = Counter()
        for row in r3:
            cfg = row.config
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = None
            dept = None
            if isinstance(cfg, dict):
                dept = cfg.get("department")
            dept_counter[dept or "(none/db)"] += 1
        print("\n=== agent department (from config) ===")
        for k, v in dept_counter.items():
            print(f"  {k}: {v}")

asyncio.run(main())