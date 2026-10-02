"""将历史 agent_{id} 向量索引回填到 ent_{enterprise}_agent_{id} 命名空间。

执行前应完成数据库迁移并备份 Chroma 数据；脚本只写新集合、不删除旧集合。聊天链路在
迁移期双读，待回填指标和抽样验证完成后再手动清理旧集合。

用法：python scripts/backfill_vector_namespaces.py [--enterprise-id <uuid>]
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.database import async_session_factory
from app.models.agent import Agent
from app.services.incremental_updater import incremental_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


async def run(enterprise_id: str | None) -> None:
    async with async_session_factory() as db:
        query = select(Agent).where(Agent.folder_path.is_not(None))
        if enterprise_id:
            query = query.where(Agent.enterprise_id == enterprise_id)
        agents = (await db.execute(query)).scalars().all()

        for agent in agents:
            if not agent.folder_path:
                continue
            logger.info("开始回填 Agent 向量集合: enterprise=%s agent=%s", agent.enterprise_id, agent.id)
            try:
                stats = await incremental_update(
                    db,
                    str(agent.id),
                    agent.folder_path,
                    force_reindex=True,
                )
                logger.info("回填完成: agent=%s stats=%s", agent.id, stats)
            except Exception:  # noqa: BLE001
                logger.exception("回填失败: agent=%s", agent.id)


def main() -> None:
    parser = argparse.ArgumentParser(description="回填企业前缀 Chroma 集合")
    parser.add_argument("--enterprise-id", help="可选：仅处理一个企业")
    args = parser.parse_args()
    asyncio.run(run(args.enterprise_id))


if __name__ == "__main__":
    main()
