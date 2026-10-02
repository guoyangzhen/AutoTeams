"""导入 demo Runtime JSON 预设并绑定到 demo@autoteams.example（复现演示数据）。

配合 ``export_demo_runtime.py`` 生成的 ``backend/data/demo_runtime_preset.json``，
将完整重新编译的 Enterprise Runtime 一键导入到 demo 账号所在企业并设为激活版本，
保证全新部署时演示数据可复现且绑定到 demo@autoteams.example。

用法：
    cd backend
    python -m scripts.seed_demo_runtime

可选：``python -m scripts.seed_demo_runtime <enterprise_id>`` 覆盖目标企业。

幂等性：若目标企业已存在同名版本则跳过（不覆盖已有数据）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory
from app.models.enterprise import Enterprise
from app.models.user import User
from app.schemas.runtime import RuntimeCompileResult
from app.services.runtime import get_runtime_by_version, save_runtime

DEMO_EMAIL = "demo@autoteams.example"
DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "demo_runtime_preset.json",
)


async def _resolve_enterprise_id(db, override: str | None) -> str:
    """解析目标企业 ID：优先覆盖参数，否则取 demo 账号所属企业。"""
    if override:
        return override
    user = (await db.execute(
        select(User).where(User.email == DEMO_EMAIL)
    )).scalar_one_or_none()
    if user is None:
        raise SystemExit(f"未找到 demo 账号 {DEMO_EMAIL}，请先运行 scripts.seed_demo")
    if not user.enterprise_id:
        raise SystemExit(f"demo 账号 {DEMO_EMAIL} 未绑定企业，无法导入 Runtime")
    return user.enterprise_id


async def main() -> None:
    if not os.path.isfile(DATA_FILE):
        raise SystemExit(f"未找到预设文件: {DATA_FILE}（请先运行 scripts.export_demo_runtime）")

    with open(DATA_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    # 通过 schema 校验并重建编译结果对象
    result = RuntimeCompileResult.model_validate(raw)
    target_version = raw.get("version") or "v1.5.2"

    override = sys.argv[1] if len(sys.argv) > 1 else None
    async with async_session_factory() as db:
        enterprise_id = await _resolve_enterprise_id(db, override)
        ent = (await db.execute(
            select(Enterprise).where(Enterprise.id == enterprise_id)
        )).scalar_one_or_none()
        if ent is None:
            raise SystemExit(f"企业不存在: {enterprise_id}")

        # 幂等：版本已存在则跳过
        existing = await get_runtime_by_version(db, enterprise_id, target_version)
        if existing is not None:
            print(f"[跳过] 企业 {ent.name} 已存在版本 {target_version}（active={existing.is_active}），未覆盖。")
            return

        runtime = await save_runtime(
            db,
            enterprise_id=enterprise_id,
            compile_result=result,
            created_by=None,
            changelog=f"导入演示数据预设 {target_version}（完整重新编译产物）",
            change_type="minor",
            version=target_version,
        )
        print("=" * 60)
        print(f"已导入 demo Runtime 并设为激活版本:")
        print(f"  企业: {ent.name} ({enterprise_id})")
        print(f"  version: {runtime.version}")
        print(f"  completeness: {runtime.completeness:.4f}")
        print(f"  绑定账号: {DEMO_EMAIL}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
