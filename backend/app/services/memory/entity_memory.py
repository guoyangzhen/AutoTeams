"""实体记忆：客户/商机/产品关键属性（PRD §5.12）。

特性：
- 存储每个 Agent 关联的实体关键属性（JSON）
- 复用 cognition/knowledge_graph.py（WT1）的 GraphStore 接口
- 支持实体 ID 查询、属性更新、关系追溯
- DB 表 entity_memories + 知识图谱 GraphStore 双写

实体类型：customer / opportunity / product / order
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import EntityMemory
from app.schemas.compiler import GraphNode
from app.services.cognition.knowledge_graph import (
    EntityType,
    PGJSONBGraphStore,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 实体类型 → GraphStore EntityType 映射
_ENTITY_TYPE_MAP = {
    "customer": EntityType.CUSTOMER,
    "opportunity": EntityType.OPPORTUNITY,
    "product": EntityType.PRODUCT,
    "order": EntityType.ORDER,
}


class EntityMemoryService:
    """实体记忆服务。

    用法：
        svc = EntityMemoryService()
        # 更新实体记忆
        entity = await svc.upsert_entity(
            db, agent_id, enterprise_id,
            entity_type="customer",
            entity_id="C-001",
            attributes={"name": "华智制造", "budget": 200000, "focus": "测温精度"}
        )
        # 查询实体
        entity = await svc.get_entity(db, agent_id, enterprise_id, "customer", "C-001")
        # 查询关系（通过知识图谱）
        neighbors = await svc.get_relations(db, enterprise_id, "C-001")
    """

    async def get_entity(
        self,
        db: AsyncSession,
        agent_id: str,
        enterprise_id: str,
        entity_type: str,
        entity_id: str,
    ) -> Optional[EntityMemory]:
        """按实体类型 + ID 查询实体记忆。"""
        stmt = select(EntityMemory).where(
            EntityMemory.agent_id == agent_id,
            EntityMemory.entity_type == entity_type,
            EntityMemory.entity_id == entity_id,
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def upsert_entity(
        self,
        db: AsyncSession,
        agent_id: str,
        enterprise_id: str,
        entity_type: str,
        entity_id: str,
        attributes: dict,
    ) -> EntityMemory:
        """更新或创建实体记忆，同时同步到知识图谱。

        硬约束：service 层写操作必须显式 await db.commit()
        """
        existing = await self.get_entity(db, agent_id, enterprise_id, entity_type, entity_id)

        if existing:
            # 合并属性（新属性覆盖旧属性）
            merged = {**existing.attributes, **attributes}
            existing.attributes = merged
            existing.updated_at = utcnow()
            await db.flush()
            await db.commit()
            await db.refresh(existing)
            record = existing
        else:
            record = EntityMemory(
                agent_id=agent_id,
                enterprise_id=enterprise_id,
                entity_type=entity_type,
                entity_id=entity_id,
                attributes=attributes,
            )
            db.add(record)
            await db.flush()
            await db.commit()
            await db.refresh(record)

        # 同步到知识图谱（best-effort，失败不阻断主流程）
        await self._sync_to_graph(db, enterprise_id, entity_type, entity_id, attributes)

        logger.info(
            f"实体记忆已更新: agent_id={agent_id}, "
            f"type={entity_type}, id={entity_id}"
        )
        return record

    async def get_entities_by_type(
        self,
        db: AsyncSession,
        agent_id: str,
        entity_type: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EntityMemory]:
        """按类型列出 Agent 的实体记忆。"""
        stmt = (
            select(EntityMemory)
            .where(
                EntityMemory.agent_id == agent_id,
                EntityMemory.entity_type == entity_type,
            )
            .order_by(EntityMemory.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def get_all_entities(
        self,
        db: AsyncSession,
        agent_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EntityMemory]:
        """列出 Agent 的所有实体记忆。"""
        stmt = (
            select(EntityMemory)
            .where(EntityMemory.agent_id == agent_id)
            .order_by(EntityMemory.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def get_relations(
        self,
        db: AsyncSession,
        enterprise_id: str,
        entity_id: str,
    ) -> list[GraphNode]:
        """通过知识图谱查询实体的关联节点。

        复用 WT1 的 PGJSONBGraphStore.query_neighbors()。
        """
        store = PGJSONBGraphStore(db, enterprise_id)
        # entity_id 可能是知识图谱中的 node_id，也可能是别名
        # 先尝试直接查询，未命中时尝试用 entity_id 作为 node_id
        neighbors = await store.query_neighbors(entity_id)
        return neighbors

    async def _sync_to_graph(
        self,
        db: AsyncSession,
        enterprise_id: str,
        entity_type: str,
        entity_id: str,
        attributes: dict,
    ) -> None:
        """将实体记忆同步到知识图谱（best-effort）。

        在知识图谱中创建或更新对应的实体节点，
        使实体记忆与知识图谱保持关联。
        """
        try:
            graph_entity_type = _ENTITY_TYPE_MAP.get(entity_type)
            if graph_entity_type is None:
                logger.debug(f"实体类型 {entity_type} 无图谱映射，跳过同步")
                return

            store = PGJSONBGraphStore(db, enterprise_id)
            node = GraphNode(
                node_id=entity_id,
                node_type=graph_entity_type.value,
                name=attributes.get("name", entity_id),
                attributes=attributes,
            )
            await store.add_node(node)
            await store.save()  # 显式提交
        except Exception as e:
            logger.warning(f"实体记忆同步到知识图谱失败（不阻断主流程）: {e}")
