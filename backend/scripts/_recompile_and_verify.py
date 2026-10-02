"""同步全量编译 + 验证协作关系图边数（端到端验证 O(n) 修复）。

清理后台残留的 running 任务后，同步执行五级编译，打印每级进度与耗时，
结束后输出 agents / departments / collab edges，验证边数是否从 O(n^2) 降为 O(n)。
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(root)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.models.compiler import CompilationJob
from app.services.compiler.pipeline import CompilationPipeline, run_compilation_background

ENTERPRISE_ID = "9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e"


async def cleanup_stale_jobs():
    """把残留的 running 任务标记为 failed，避免状态混乱。"""
    async with async_session_factory() as db:
        res = await db.execute(select(CompilationJob).where(CompilationJob.status == "running"))
        for j in res.scalars().all():
            j.status = "failed"
            j.error_message = "cleaned up: stale running job from interrupted background task"
            j.completed_at = __import__("app.utils.time", fromlist=["utcnow"]).utcnow()
            print(f"已清理残留任务 {j.id[:8]} (stage={j.stage})")
        await db.commit()


def _get_collab_edge_count(d: dict) -> int:
    cg = (d or {}).get("collaboration_graph") or {}
    return len(cg.get("edges") or [])


async def main():
    await cleanup_stale_jobs()

    folder = os.path.join(settings.SAMPLE_DATA_DIR, "example-enterprise")
    print(f"数据源: {folder}")

    start = time.time()
    # 用独立 session 同步执行完整编译（非后台，便于监控进度）
    async with async_session_factory() as db:
        pipeline = CompilationPipeline(db, ENTERPRISE_ID)
        result = await pipeline.run_full(folder)
    elapsed = time.time() - start

    runtime = result.get("runtime")
    agents = getattr(runtime, "agents", None) or []
    org = getattr(runtime, "organization", None)
    depts = getattr(org, "departments", None) or []
    cg = getattr(runtime, "collaboration_graph", None)
    edges = getattr(cg, "edges", None) or []
    comp = result.get("completeness")

    print("\n===== 编译完成 =====")
    print(f"耗时: {elapsed:.1f}s")
    print(f"job_id: {result.get('job_id')}")
    print(f"agents: {len(agents)}")
    print(f"departments: {len(depts)}")
    print(f"collab edges: {len(edges)}")
    print(f"completeness: {getattr(comp, 'overall', None)}")
    print("\n各阶段摘要:")
    for k, v in (result.get("results") or {}).items():
        print(f"  {k}: {v}")

    # 验证：从 enterprise_runtimes 表读取持久化结果
    from app.models.runtime import EnterpriseRuntime
    async with async_session_factory() as db2:
        r = await db2.execute(
            select(EnterpriseRuntime.runtime_data, EnterpriseRuntime.compiled_at)
            .order_by(EnterpriseRuntime.compiled_at.desc()).limit(1)
        )
        row = r.first()
        if row:
            d, ts = row
            print(f"\n[持久化验证] compiled_at={ts} edges={_get_collab_edge_count(d)}")


if __name__ == "__main__":
    asyncio.run(main())