"""基于 example-enterprise 为指定企业触发一次样例编译（#9 独立入口）。

用法：
    cd backend
    python -m scripts.seed_demo_compile <enterprise_id>

用于在 demo 账号已初始化后，单独触发一次真实五级编译，
将 24 岗位/7 产品/20 客户/20 订单/SOP/权限/KPI 灌入知识图谱与向量库，
并产出 Enterprise Runtime，使完成度提升、五级全通。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory
from app.services.compiler.pipeline import CompilationPipeline
from app.models.interview import InterviewSession
from app.services.interview.interview_engine import InterviewEngine


async def _compute_interview_completion(db, enterprise_id: str) -> float:
    """从数据库计算该企业实际访谈完成度（[0,1]，取所有会话完成度的最大值）。

    compute_completeness 返回 0-100，此处归一化到 [0,1] 供完成度公式使用。
    """
    engine = InterviewEngine()
    sessions = (await db.execute(
        select(InterviewSession).where(InterviewSession.enterprise_id == enterprise_id)
    )).scalars().all()
    if not sessions:
        return 0.0
    comps = [await engine.compute_completeness(db, s) for s in sessions]
    best = max(comps, default=0.0)
    return max(0.0, min(1.0, best / 100.0))


async def main(enterprise_id: str) -> None:
    """触发样例编译。"""
    from app.config import settings

    default_path = os.path.join(settings.SAMPLE_DATA_DIR, "example-enterprise")
    if not os.path.isdir(default_path):
        print(f"未找到示例企业数据目录：{default_path}")
        return

    async with async_session_factory() as db:
        # 从数据库计算实际访谈完成度并注入完成度公式（替代硬编码 0）
        interview_completion = await _compute_interview_completion(db, enterprise_id)
        print(f"访谈完成度（来自 DB，max over sessions）: {interview_completion:.4f}")

        pipeline = CompilationPipeline(db, enterprise_id)
        result = await pipeline.run_full(default_path, None, interview_completion)

    completeness = result.get("completeness")
    runtime = result.get("runtime")
    gaps = result.get("gaps")
    print("=" * 60)
    print(f"样例编译完成：enterprise={enterprise_id}")
    print(f"  data_source: {default_path}")
    print(f"完成度: {completeness.overall if completeness else 'N/A'}")
    print(f"等级: {completeness.level if completeness else 'N/A'}")
    if completeness is not None and completeness.dimensions:
        print("  维度得分:")
        for k, v in completeness.dimensions.items():
            print(f"    {k} = {v}")
    if runtime is not None:
        print(f"  岗位数(agents): {len(runtime.agents)}")
        print(f"  流程引擎数: {len(runtime.process_engines)}")
        print(f"  部门数: {len(runtime.organization.departments)}")
        print(f"  工具注册表: {len(runtime.tool_registry)}")
        print(f"  不同流程进程数: {len({pe.process_id for pe in runtime.process_engines})}")
        print(f"  Agent技能非空数: {sum(1 for a in runtime.agents if a.skills)}/{len(runtime.agents)}")
    if gaps is not None:
        print(f"  缺失项: {len(gaps.gaps)} 项")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python -m scripts.seed_demo_compile <enterprise_id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))