"""触发一次全量五级编译（应用 O(n) 协作关系图优化 + 噪音岗位过滤），供端到端验证。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session_factory
from app.services.compiler.pipeline import run_compilation_background

ENTERPRISE_ID = "9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e"


async def main():
    folder = os.path.join(settings.SAMPLE_DATA_DIR, "example-enterprise")
    print("数据源:", folder)
    result = await run_compilation_background(ENTERPRISE_ID, folder)
    print("编译完成:")
    print("  completeness:", result.get("completeness"))
    runtime = result.get("runtime")
    if runtime is not None:
        cg = getattr(runtime, "collaboration_graph", None)
        print("  agents:", len(getattr(runtime, "agents", []) or []))
        print("  collab edges:", len(getattr(cg, "edges", []) or []) if cg else "N/A")


if __name__ == "__main__":
    asyncio.run(main())