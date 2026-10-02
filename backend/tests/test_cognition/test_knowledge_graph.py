"""知识图谱服务测试（PRD §4.2）。

测试 GraphStore 接口的 PGJSONBGraphStore 实现和 NetworkXInMemoryGraph。
覆盖：CRUD + 邻居查询 + 子图提取 + 13 类实体 + 8 类关系。
"""
import pytest
from unittest.mock import AsyncMock

from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    NetworkXInMemoryGraph,
    EntityType,
    RelationType,
)
from app.schemas.compiler import GraphNode, GraphEdge


@pytest.mark.asyncio
async def test_add_and_get_node(db_session):
    """测试添加和查询节点。"""
    store = PGJSONBGraphStore(db_session, "ent_test_1")
    node = GraphNode(
        node_id="dept_1",
        node_type=EntityType.DEPARTMENT.value,
        name="销售部",
        attributes={"industry": "科技"},
        confidence=0.9,
    )
    await store.add_node(node)
    await store.save()

    # 重新创建 store 实例以模拟新请求
    store2 = PGJSONBGraphStore(db_session, "ent_test_1")
    result = await store2.get_node("dept_1")
    assert result is not None
    assert result.name == "销售部"
    assert result.node_type == EntityType.DEPARTMENT.value


@pytest.mark.asyncio
async def test_add_edge_and_query_neighbors(db_session):
    """测试添加边和邻居查询。"""
    store = PGJSONBGraphStore(db_session, "ent_test_2")

    # 添加节点
    dept = GraphNode(node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部")
    role = GraphNode(node_id="role_1", node_type=EntityType.ROLE.value, name="销售经理")
    await store.add_node(dept)
    await store.add_node(role)

    # 添加边：role BELONGS_TO dept
    edge = GraphEdge(
        source_id="role_1",
        target_id="dept_1",
        relation=RelationType.BELONGS_TO.value,
    )
    await store.add_edge(edge)
    await store.save()

    # 查询 role_1 的邻居
    store2 = PGJSONBGraphStore(db_session, "ent_test_2")
    neighbors = await store2.query_neighbors("role_1", direction="out")
    assert len(neighbors) == 1
    assert neighbors[0].node_id == "dept_1"

    # 查询 dept_1 的入边邻居
    neighbors_in = await store2.query_neighbors("dept_1", direction="in")
    assert len(neighbors_in) == 1
    assert neighbors_in[0].node_id == "role_1"


@pytest.mark.asyncio
async def test_query_nodes_by_type(db_session):
    """测试按类型查询节点。"""
    store = PGJSONBGraphStore(db_session, "ent_test_3")
    await store.add_node(GraphNode(node_id="dept_1", node_type=EntityType.DEPARTMENT.value, name="销售部"))
    await store.add_node(GraphNode(node_id="dept_2", node_type=EntityType.DEPARTMENT.value, name="财务部"))
    await store.add_node(GraphNode(node_id="role_1", node_type=EntityType.ROLE.value, name="经理"))
    await store.save()

    store2 = PGJSONBGraphStore(db_session, "ent_test_3")
    depts = await store2.query_nodes_by_type(EntityType.DEPARTMENT.value)
    assert len(depts) == 2
    roles = await store2.query_nodes_by_type(EntityType.ROLE.value)
    assert len(roles) == 1


@pytest.mark.asyncio
async def test_subgraph(db_session):
    """测试子图提取。"""
    store = PGJSONBGraphStore(db_session, "ent_test_4")
    for i in range(5):
        await store.add_node(GraphNode(
            node_id=f"node_{i}",
            node_type=EntityType.ROLE.value,
            name=f"角色{i}",
        ))
    await store.add_edge(GraphEdge(source_id="node_0", target_id="node_1", relation=RelationType.REPORTS_TO.value))
    await store.add_edge(GraphEdge(source_id="node_1", target_id="node_2", relation=RelationType.REPORTS_TO.value))
    await store.save()

    store2 = PGJSONBGraphStore(db_session, "ent_test_4")
    nodes, edges = await store2.subgraph(["node_0", "node_1", "node_2"])
    assert len(nodes) == 3
    assert len(edges) == 2


@pytest.mark.asyncio
async def test_count_nodes(db_session):
    """测试节点计数。"""
    store = PGJSONBGraphStore(db_session, "ent_test_5")
    assert await store.count_nodes() == 0
    await store.add_node(GraphNode(node_id="n1", node_type=EntityType.ROLE.value, name="r1"))
    await store.add_node(GraphNode(node_id="n2", node_type=EntityType.ROLE.value, name="r2"))
    await store.save()
    assert await store.count_nodes() == 2


@pytest.mark.asyncio
async def test_all_13_entity_types():
    """验证 13 类实体类型完整性。"""
    assert len(EntityType) == 13
    expected = {
        "Department", "Role", "Employee", "Process", "Product", "Customer",
        "KPI", "System", "Permission", "Knowledge", "Opportunity", "Order", "Tool"
    }
    actual = {e.value for e in EntityType}
    assert actual == expected


@pytest.mark.asyncio
async def test_all_8_relation_types():
    """验证 8 类关系类型完整性。"""
    assert len(RelationType) == 8
    expected = {
        "belongs_to", "reports_to", "executes", "owes",
        "has_permission", "uses", "produces", "serves"
    }
    actual = {r.value for r in RelationType}
    assert actual == expected


def test_networkx_in_memory_basic():
    """测试 NetworkX 内存图基础功能。"""
    graph = NetworkXInMemoryGraph()
    nodes = [
        GraphNode(node_id="a", node_type="Role", name="A"),
        GraphNode(node_id="b", node_type="Role", name="B"),
        GraphNode(node_id="c", node_type="Role", name="C"),
    ]
    edges = [
        GraphEdge(source_id="a", target_id="b", relation="reports_to"),
        GraphEdge(source_id="b", target_id="c", relation="reports_to"),
    ]
    graph.load_from_store(nodes, edges)

    assert graph.get_node_count() == 3
    assert graph.get_edge_count() == 2

    # 查找路径
    path = graph.find_path("a", "c")
    assert path is not None
    assert path == ["a", "b", "c"]

    # 连通分量
    components = graph.get_connected_components()
    assert len(components) == 1
    assert set(components[0]) == {"a", "b", "c"}


def test_networkx_disconnected():
    """测试 NetworkX 不连通图。"""
    graph = NetworkXInMemoryGraph()
    nodes = [
        GraphNode(node_id="a", node_type="Role", name="A"),
        GraphNode(node_id="b", node_type="Role", name="B"),
        GraphNode(node_id="c", node_type="Role", name="C"),
        GraphNode(node_id="d", node_type="Role", name="D"),
    ]
    edges = [
        GraphEdge(source_id="a", target_id="b", relation="reports_to"),
    ]
    graph.load_from_store(nodes, edges)

    components = graph.get_connected_components()
    # 4 节点 1 条边 → 3 个连通分量 {a,b}, {c}, {d}
    assert len(components) == 3
