"""导出 demo 企业激活 Enterprise Runtime 为 JSON 预设文件。

用于把「重新编译并持久化的完整演示数据」固化到仓库（backend/data/），
使全新部署时可通过 ``seed_demo_runtime.py`` 一键复现绑定到 demo@autoteams.example。

用法：
    cd backend
    python -m scripts.export_demo_runtime

输出：backend/data/demo_runtime_preset.json（含版本、完整度、agents/组织/流程/协作图/工具注册表）
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import async_session_factory
from app.services.runtime import store_get_active_runtime

# demo 企业（示例科技）固定 ID，与 seed_demo.py 保持一致
DEMO_ENTERPRISE_ID = "9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e"

# 输出目录：backend/data/
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT_FILE = os.path.join(OUT_DIR, "demo_runtime_preset.json")


async def main() -> None:
    async with async_session_factory() as db:
        runtime = await store_get_active_runtime(db, DEMO_ENTERPRISE_ID, use_cache=False)
        if runtime is None:
            print(f"未找到 demo 企业激活 Runtime（enterprise={DEMO_ENTERPRISE_ID}）")
            return

        data = runtime.runtime_data
        # 保证版本号一致，便于识别
        data["version"] = runtime.version

        os.makedirs(OUT_DIR, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        agents = data.get("agents") or []
        org = data.get("organization") or {}
        cg = data.get("collaboration_graph") or {}
        print("=" * 60)
        print(f"已导出 demo Runtime 预设: {OUT_FILE}")
        print(f"  version: {runtime.version}")
        print(f"  completeness: {runtime.completeness}")
        print(f"  agents: {len(agents)}")
        print(f"  departments: {len(org.get('departments') or [])}")
        print(f"  collab edges: {len(cg.get('edges') or [])}")
        print(f"  compiled_at: {runtime.compiled_at}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
