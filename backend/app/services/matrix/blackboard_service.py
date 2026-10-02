"""AutoTeams 4.0 团队共享黑板（Blackboard）服务。

提供轻量入库流水线（清洗规范化、去重更新、结构化入库与引用回链）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team_matrix import SharedBlackboardEntry

logger = logging.getLogger(__name__)


async def post_blackboard_entry(
    db: AsyncSession,
    team_id: str,
    topic: str,
    content: str,
    source_profile_id: str,
    source_task_id: Optional[str] = None,
    citations: Optional[List[Dict[str, Any]]] = None,
    is_pinned: bool = False,
) -> SharedBlackboardEntry:
    """写入或更新共享黑板条目（活文档机制）。"""
    # 规范化清洗
    cleaned_topic = topic.strip()
    cleaned_content = content.strip()

    # 检查同 topic 是否已存在（活文档原地迭代更新）
    stmt = select(SharedBlackboardEntry).where(
        SharedBlackboardEntry.team_id == team_id,
        SharedBlackboardEntry.topic == cleaned_topic,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.content = cleaned_content
        existing.source_profile_id = source_profile_id
        existing.source_task_id = source_task_id
        if citations:
            existing.citations = citations
        existing.is_pinned = is_pinned or existing.is_pinned
        entry = existing
        logger.info(f"团队 {team_id} 更新共享黑板主题: {cleaned_topic}")
    else:
        entry = SharedBlackboardEntry(
            id=str(uuid.uuid4()),
            team_id=team_id,
            topic=cleaned_topic,
            content=cleaned_content,
            source_profile_id=source_profile_id,
            source_task_id=source_task_id,
            citations=citations or [],
            is_pinned=is_pinned,
        )
        db.add(entry)
        logger.info(f"团队 {team_id} 新增共享黑板主题: {cleaned_topic}")

    await db.commit()
    await db.refresh(entry)
    return entry


async def list_blackboard_entries(
    db: AsyncSession,
    team_id: str,
    topic: Optional[str] = None,
    pinned_only: bool = False,
) -> List[SharedBlackboardEntry]:
    """查询指定团队的共享黑板条目列表。"""
    stmt = select(SharedBlackboardEntry).where(SharedBlackboardEntry.team_id == team_id)
    if topic:
        stmt = stmt.where(SharedBlackboardEntry.topic == topic)
    if pinned_only:
        stmt = stmt.where(SharedBlackboardEntry.is_pinned.is_(True))

    stmt = stmt.order_by(SharedBlackboardEntry.is_pinned.desc(), SharedBlackboardEntry.updated_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
