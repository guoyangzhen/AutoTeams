"""诊断脚本：列出所有企业的激活 Enterprise Runtime 状态。

用于排查「业务流程重复命名」「运行底座未安装」等问题。
在容器内执行：cd 后端工作目录 && python -m scripts.inspect_runtimes
"""
import asyncio
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory
from app.models.runtime import EnterpriseRuntime


async def main() -> None:
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(EnterpriseRuntime)
            .where(EnterpriseRuntime.is_active == True)  # noqa: E712
            .order_by(EnterpriseRuntime.enterprise_id, EnterpriseRuntime.created_at.desc())
        )).scalars().all()
        if not rows:
            print("无激活 Runtime")
            return

        for r in rows:
            d = r.runtime_data if isinstance(r.runtime_data, dict) else json.loads(r.runtime_data)
            pes = d.get("process_engines", []) or []
            pe_names = [p.get("name", "") or "" for p in pes]
            blank_names = sum(1 for n in pe_names if not n.strip())
            tr = d.get("tool_registry", []) or []
            installed = sum(1 for t in tr if t.get("installed"))
            agents = d.get("agents", []) or []
            agent_status = dict(Counter((a.get("status", "") or "unknown") for a in agents))
            print("=" * 70)
            print(f"enterprise_id : {r.enterprise_id}")
            print(f"runtime_id    : {r.id}")
            print(f"version       : {r.version}  completeness={r.completeness}")
            print(f"process_engines: {len(pes)} 个，空名 {blank_names} 个")
            print(f"  names: {pe_names}")
            print(f"tool_registry  : {len(tr)} 个，installed {installed} 个")
            print(f"  tools installed: {[t.get('name') for t in tr if not t.get('installed')]}")
            print(f"agents         : {len(agents)} 个，status={agent_status}")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())