"""实体记忆测试（PRD §5.12）。

覆盖：
- upsert_entity：创建 + 更新（属性合并）
- get_entity：按类型 + ID 查询
- get_entities_by_type / get_all_entities
- 知识图谱同步（mock GraphStore）
- 唯一约束（agent_id + entity_type + entity_id）
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.memory.entity_memory import EntityMemoryService
from app.models.memory import EntityMemory


class TestEntityMemoryService:
    """实体记忆服务测试。"""

    async def test_upsert_entity_create(self, db_session):
        """创建实体记忆。"""
        svc = EntityMemoryService()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            entity = await svc.upsert_entity(
                db_session,
                agent_id="agent-em-1",
                enterprise_id="ent-em-1",
                entity_type="customer",
                entity_id="C-001",
                attributes={"name": "华智制造", "budget": 200000},
            )

        assert entity.id is not None
        assert entity.agent_id == "agent-em-1"
        assert entity.entity_type == "customer"
        assert entity.entity_id == "C-001"
        assert entity.attributes["name"] == "华智制造"
        assert entity.attributes["budget"] == 200000

    async def test_upsert_entity_update_merges_attributes(self, db_session):
        """更新实体记忆时合并属性（新属性覆盖旧属性）。"""
        svc = EntityMemoryService()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            # 初始创建
            await svc.upsert_entity(
                db_session, "agent-em-2", "ent-em-2",
                entity_type="customer", entity_id="C-002",
                attributes={"name": "客户A", "budget": 100000},
            )
            # 更新（部分属性）
            updated = await svc.upsert_entity(
                db_session, "agent-em-2", "ent-em-2",
                entity_type="customer", entity_id="C-002",
                attributes={"budget": 150000, "focus": "测温精度"},
            )

        # 旧属性保留，新属性覆盖/新增
        assert updated.attributes["name"] == "客户A"  # 保留
        assert updated.attributes["budget"] == 150000  # 覆盖
        assert updated.attributes["focus"] == "测温精度"  # 新增

    async def test_get_entity(self, db_session):
        """按类型 + ID 查询实体记忆。"""
        svc = EntityMemoryService()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            await svc.upsert_entity(
                db_session, "agent-em-3", "ent-em-3",
                entity_type="product", entity_id="SL-T100",
                attributes={"name": "温度传感器", "range": "-40~600℃"},
            )

            entity = await svc.get_entity(
                db_session, "agent-em-3", "ent-em-3",
                entity_type="product", entity_id="SL-T100",
            )

        assert entity is not None
        assert entity.attributes["range"] == "-40~600℃"

    async def test_get_entity_not_found(self, db_session):
        """查询不存在的实体返回 None。"""
        svc = EntityMemoryService()
        result = await svc.get_entity(
            db_session, "agent-x", "ent-x",
            entity_type="customer", entity_id="nonexistent",
        )
        assert result is None

    async def test_get_entities_by_type(self, db_session):
        """按类型列出实体记忆。"""
        svc = EntityMemoryService()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            await svc.upsert_entity(
                db_session, "agent-em-4", "ent-em-4",
                entity_type="customer", entity_id="C-010",
                attributes={"name": "客户1"},
            )
            await svc.upsert_entity(
                db_session, "agent-em-4", "ent-em-4",
                entity_type="customer", entity_id="C-011",
                attributes={"name": "客户2"},
            )
            await svc.upsert_entity(
                db_session, "agent-em-4", "ent-em-4",
                entity_type="product", entity_id="P-001",
                attributes={"name": "产品1"},
            )

            customers = await svc.get_entities_by_type(
                db_session, "agent-em-4", entity_type="customer",
            )
            products = await svc.get_entities_by_type(
                db_session, "agent-em-4", entity_type="product",
            )

        assert len(customers) == 2
        assert len(products) == 1

    async def test_get_all_entities(self, db_session):
        """列出 Agent 的所有实体记忆。"""
        svc = EntityMemoryService()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            await svc.upsert_entity(
                db_session, "agent-em-5", "ent-em-5",
                entity_type="customer", entity_id="C-020",
                attributes={"name": "客户"},
            )
            await svc.upsert_entity(
                db_session, "agent-em-5", "ent-em-5",
                entity_type="opportunity", entity_id="OPP-001",
                attributes={"name": "商机"},
            )

            all_entities = await svc.get_all_entities(db_session, "agent-em-5")

        assert len(all_entities) == 2

    async def test_entity_types_isolated_per_agent(self, db_session):
        """不同 Agent 的实体记忆相互隔离。"""
        svc = EntityMemoryService()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            await svc.upsert_entity(
                db_session, "agent-em-6a", "ent-em-6",
                entity_type="customer", entity_id="C-030",
                attributes={"name": "AgentA客户"},
            )
            await svc.upsert_entity(
                db_session, "agent-em-6b", "ent-em-6",
                entity_type="customer", entity_id="C-030",
                attributes={"name": "AgentB客户"},
            )

            entity_a = await svc.get_entity(
                db_session, "agent-em-6a", "ent-em-6",
                "customer", "C-030",
            )
            entity_b = await svc.get_entity(
                db_session, "agent-em-6b", "ent-em-6",
                "customer", "C-030",
            )

        assert entity_a.attributes["name"] == "AgentA客户"
        assert entity_b.attributes["name"] == "AgentB客户"

    async def test_unique_constraint(self, db_session):
        """同一 agent + type + id 的 upsert 是更新而非创建。"""
        svc = EntityMemoryService()
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            await svc.upsert_entity(
                db_session, "agent-em-7", "ent-em-7",
                entity_type="customer", entity_id="C-040",
                attributes={"name": "原始"},
            )
            await svc.upsert_entity(
                db_session, "agent-em-7", "ent-em-7",
                entity_type="customer", entity_id="C-040",
                attributes={"name": "更新"},
            )

            all_entities = await svc.get_all_entities(db_session, "agent-em-7")

        assert len(all_entities) == 1  # 只有一条记录
        assert all_entities[0].attributes["name"] == "更新"

    async def test_supports_multiple_entity_types(self, db_session):
        """支持 customer / opportunity / product / order 多种实体类型。"""
        svc = EntityMemoryService()
        types = ["customer", "opportunity", "product", "order"]
        with patch.object(
            EntityMemoryService, "_sync_to_graph", new=AsyncMock()
        ):
            for i, etype in enumerate(types):
                entity = await svc.upsert_entity(
                    db_session, "agent-em-8", "ent-em-8",
                    entity_type=etype, entity_id=f"{etype.upper()}-{i}",
                    attributes={"name": f"{etype}实体"},
                )
                assert entity.entity_type == etype
