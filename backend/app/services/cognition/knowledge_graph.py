"""企业知识图谱服务（PRD §4.2 数据结构定义 1）。

GraphStore 抽象接口 + PG JSONB 存储 + NetworkX 内存图。
支持 13 类实体 + 8 类关系（PRD §4.2 定义）。

设计说明：
- GraphStore 是抽象接口，MVP 用 PGJSONBGraphStore（PostgreSQL JSONB）+ NetworkXInMemoryGraph
- P1 阶段可切换 Neo4j 实现，只需新增 Neo4jGraphStore 并实现相同接口
- 实体/关系类型用枚举约束，防止拼写不一致
"""
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cognition import KnowledgeGraph
from app.schemas.compiler import GraphNode, GraphEdge

logger = logging.getLogger(__name__)


class EntityType(str, Enum):
    """13 类实体类型（PRD §4.2）。"""
    DEPARTMENT = "Department"       # 部门
    ROLE = "Role"                   # 岗位
    EMPLOYEE = "Employee"           # 员工
    PROCESS = "Process"             # 流程
    PRODUCT = "Product"             # 产品
    CUSTOMER = "Customer"           # 客户
    KPI = "KPI"                     # 指标
    SYSTEM = "System"               # 业务系统
    PERMISSION = "Permission"       # 权限
    KNOWLEDGE = "Knowledge"         # 知识条目
    OPPORTUNITY = "Opportunity"     # 商机
    ORDER = "Order"                 # 订单
    TOOL = "Tool"                   # 工具


class RelationType(str, Enum):
    """8 类关系类型（PRD §4.2）。

    P2 字段对齐: 枚举值统一为小写 snake_case，与前端 GraphRelationType 对齐。
    """
    BELONGS_TO = "belongs_to"           # Role → Department
    REPORTS_TO = "reports_to"           # Employee → Employee
    EXECUTES = "executes"               # Role → Process
    OWES = "owes"                       # Role → KPI
    HAS_PERMISSION = "has_permission"   # Role → Permission
    USES = "uses"                       # Process → System
    PRODUCES = "produces"               # Process → Knowledge
    SERVES = "serves"                   # Customer → Product


class GraphStore(ABC):
    """图存储抽象接口。

    MVP 实现：PGJSONBGraphStore（PostgreSQL JSONB）+ NetworkXInMemoryGraph
    P1 可切换：Neo4jGraphStore
    """

    @abstractmethod
    async def add_node(self, node: GraphNode) -> None:
        """添加节点。"""

    @abstractmethod
    async def add_edge(self, edge: GraphEdge) -> None:
        """添加边。"""

    @abstractmethod
    async def get_node(self, node_id: str) -> Optional[GraphNode]:
        """按 ID 查询节点。"""

    @abstractmethod
    async def query_neighbors(
        self, node_id: str, relation: Optional[str] = None, direction: str = "both"
    ) -> list[GraphNode]:
        """查询邻居节点。"""

    @abstractmethod
    async def query_nodes_by_type(self, node_type: str) -> list[GraphNode]:
        """按类型查询节点。"""

    @abstractmethod
    async def subgraph(self, node_ids: list[str]) -> tuple[list[GraphNode], list[GraphEdge]]:
        """提取子图。"""

    @abstractmethod
    async def get_all(self) -> tuple[list[GraphNode], list[GraphEdge]]:
        """获取全图。"""

    @abstractmethod
    async def count_nodes(self) -> int:
        """节点总数。"""


class PGJSONBGraphStore(GraphStore):
    """PostgreSQL JSONB 图存储实现。

    将图谱的 nodes/edges 以 JSONB 存储在 knowledge_graphs 表中。
    适合 MVP 阶段（单租户、数据量不大），P1 可切换 Neo4j。
    """

    def __init__(self, db: AsyncSession, enterprise_id: str):
        self._db = db
        self._enterprise_id = enterprise_id
        self._graph_record: Optional[KnowledgeGraph] = None

    async def _load_record(self) -> KnowledgeGraph:
        """加载或创建图记录。"""
        if self._graph_record is not None:
            return self._graph_record
        result = await self._db.execute(
            select(KnowledgeGraph).where(
                KnowledgeGraph.enterprise_id == self._enterprise_id,
                KnowledgeGraph.is_active == True,  # noqa: E712
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            record = KnowledgeGraph(
                enterprise_id=self._enterprise_id,
                nodes=[],
                edges=[],
                version="v1.0.0",
                is_active=True,
            )
            self._db.add(record)
            await self._db.flush()
            self._graph_record = record
        else:
            self._graph_record = record
        return self._graph_record

    async def add_node(self, node: GraphNode) -> None:
        record = await self._load_record()
        nodes: list[dict] = list(record.nodes or [])
        # 去重：同 node_id 覆盖
        nodes = [n for n in nodes if n.get("node_id") != node.node_id]
        nodes.append(node.model_dump())
        record.nodes = nodes

    async def add_edge(self, edge: GraphEdge) -> None:
        record = await self._load_record()
        edges: list[dict] = list(record.edges or [])
        edges.append(edge.model_dump())
        record.edges = edges

    async def get_node(self, node_id: str) -> Optional[GraphNode]:
        record = await self._load_record()
        for n in record.nodes or []:
            if n.get("node_id") == node_id:
                return GraphNode(**n)
        return None

    async def query_neighbors(
        self, node_id: str, relation: Optional[str] = None, direction: str = "both"
    ) -> list[GraphNode]:
        record = await self._load_record()
        neighbor_ids: set[str] = set()
        for e in record.edges or []:
            src = e.get("source_id")
            tgt = e.get("target_id")
            rel = e.get("relation")
            if relation and rel != relation:
                continue
            if direction in ("both", "out") and src == node_id:
                neighbor_ids.add(tgt)
            if direction in ("both", "in") and tgt == node_id:
                neighbor_ids.add(src)
        result: list[GraphNode] = []
        for n in record.nodes or []:
            if n.get("node_id") in neighbor_ids:
                result.append(GraphNode(**n))
        return result

    async def query_nodes_by_type(self, node_type: str) -> list[GraphNode]:
        record = await self._load_record()
        return [
            GraphNode(**n)
            for n in (record.nodes or [])
            if n.get("node_type") == node_type
        ]

    async def subgraph(self, node_ids: list[str]) -> tuple[list[GraphNode], list[GraphEdge]]:
        record = await self._load_record()
        id_set = set(node_ids)
        nodes = [GraphNode(**n) for n in (record.nodes or []) if n.get("node_id") in id_set]
        edges = [
            GraphEdge(**e)
            for e in (record.edges or [])
            if e.get("source_id") in id_set and e.get("target_id") in id_set
        ]
        return nodes, edges

    async def get_all(self) -> tuple[list[GraphNode], list[GraphEdge]]:
        record = await self._load_record()
        nodes = [GraphNode(**n) for n in (record.nodes or [])]
        edges = [GraphEdge(**e) for e in (record.edges or [])]
        return nodes, edges

    async def count_nodes(self) -> int:
        record = await self._load_record()
        return len(record.nodes or [])

    async def save(self) -> None:
        """显式提交到数据库。"""
        await self._db.commit()


class NetworkXInMemoryGraph:
    """NetworkX 内存图——从 GraphStore 加载到 NetworkX 进行图算法计算。

    用于需要图算法的场景（如连通性分析、最短路径、社区发现）。
    MVP 阶段主要用于连通性检查和子图提取，不需要复杂图算法。
    """

    def __init__(self):
        try:
            import networkx as nx
            self._nx = nx
            self._graph = nx.DiGraph()
            self._available = True
        except ImportError:
            logger.warning("networkx 未安装，NetworkXInMemoryGraph 降级为简单字典图")
            self._graph = None
            self._available = False

    def load_from_store(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        """从 GraphStore 的全量数据加载到内存图。"""
        if self._available:
            self._graph.clear()
            for node in nodes:
                self._graph.add_node(
                    node.node_id,
                    node_type=node.node_type,
                    name=node.name,
                    **node.attributes,
                )
            for edge in edges:
                self._graph.add_edge(
                    edge.source_id,
                    edge.target_id,
                    relation=edge.relation,
                    **edge.attributes,
                )
        else:
            # 降级：用字典存储邻接表
            self._simple_nodes = {n.node_id: n for n in nodes}
            self._simple_adj: dict[str, list[str]] = {n.node_id: [] for n in nodes}
            for edge in edges:
                self._simple_adj.setdefault(edge.source_id, []).append(edge.target_id)

    def get_node_count(self) -> int:
        if self._available:
            return self._graph.number_of_nodes()
        return len(getattr(self, "_simple_nodes", {}))

    def get_edge_count(self) -> int:
        if self._available:
            return self._graph.number_of_edges()
        return sum(len(v) for v in getattr(self, "_simple_adj", {}).values())

    def find_path(self, source: str, target: str) -> Optional[list[str]]:
        """查找从 source 到 target 的路径。"""
        if self._available:
            try:
                return self._nx.shortest_path(self._graph, source, target)
            except Exception:
                return None
        else:
            # 降级：BFS
            if source not in getattr(self, "_simple_adj", {}):
                return None
            visited = {source}
            queue = [[source]]
            while queue:
                path = queue.pop(0)
                node = path[-1]
                if node == target:
                    return path
                for neighbor in self._simple_adj.get(node, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(path + [neighbor])
            return None

    def get_connected_components(self) -> list[list[str]]:
        """获取连通分量（无向视角）。"""
        if self._available:
            return [list(c) for c in self._nx.weakly_connected_components(self._graph)]
        # 降级：简单 BFS 连通分量
        visited: set[str] = set()
        components: list[list[str]] = []
        adj = getattr(self, "_simple_adj", {})
        nodes = set(adj.keys())
        # 构建无向邻接表
        undirected: dict[str, set[str]] = {n: set() for n in nodes}
        for src, targets in adj.items():
            for tgt in targets:
                undirected[src].add(tgt)
                undirected.setdefault(tgt, set()).add(src)
        for node in nodes:
            if node not in visited:
                component = [node]
                visited.add(node)
                queue = [node]
                while queue:
                    curr = queue.pop(0)
                    for neighbor in undirected.get(curr, []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            component.append(neighbor)
                            queue.append(neighbor)
                components.append(component)
        return components
