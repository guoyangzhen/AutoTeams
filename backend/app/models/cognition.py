"""认知层数据模型。

对应 PRD §4.2 企业认知层数据结构：
- 企业知识图谱（PG JSONB 存储节点/边）
- 企业画像（PG JSONB 存储特征标签）
- 企业运行模型（PG JSONB 存储设计态逻辑定义，含版本管理）

物理存储策略（MVP）：
- 图谱以 JSONB 存储（nodes/edges 两列），通过 GraphStore 抽象接口访问
- 画像与运行模型同样以 JSONB 存储，便于灵活扩展
- P1 阶段可切换 Neo4j 作为图存储后端（GraphStore 接口已抽象）
"""
from sqlalchemy import Column, String, Boolean, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from app.database import Base
from app.models.base import TimestampMixin


class JSON(TypeDecorator):
    """跨数据库 JSON 类型：PostgreSQL 用 JSONB，SQLite 用 Text 存 JSON 字符串。

    这样测试环境（SQLite）与生产环境（PostgreSQL）共用同一套模型定义，
    无需为不同数据库维护两套模型。
    """
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        # SQLite：序列化为 JSON 字符串
        import json
        return json.dumps(value, ensure_ascii=False, default=str)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        # SQLite：从 JSON 字符串反序列化
        import json
        if isinstance(value, str):
            return json.loads(value)
        return value


class KnowledgeGraph(Base, TimestampMixin):
    """企业知识图谱（PRD §4.2 数据结构定义 1）。

    以"实体—关系—属性"三元组建模，覆盖 13 类实体 + 8 类关系。
    nodes/edges 以 JSONB 存储，支持灵活的属性扩展。
    """
    __tablename__ = "knowledge_graphs"

    enterprise_id = Column(String(36), nullable=False, index=True)
    nodes = Column(JSON, nullable=False, default=list)  # 节点列表
    edges = Column(JSON, nullable=False, default=list)  # 边列表
    version = Column(String(32), nullable=False, default="v1.0.0")
    is_active = Column(Boolean, nullable=False, default=True)


class EnterpriseProfile(Base, TimestampMixin):
    """企业画像（PRD §4.2 数据结构定义 2）。

    结构化文档，含基本信息/标签/组织概览/成熟度/业务概览/知识空白。
    """
    __tablename__ = "enterprise_profiles"

    enterprise_id = Column(String(36), nullable=False, index=True)
    profile = Column(JSON, nullable=False, default=dict)  # 画像数据
    version = Column(String(32), nullable=False, default="v1.0.0")
    completeness_score = Column(Integer, nullable=False, default=0)


class EnterpriseOperatingModel(Base, TimestampMixin):
    """企业运行模型（PRD §4.2 数据结构定义 3）。

    设计态逻辑定义，含 6 大块：organization/roles/processes/capabilities/runtime_rules/gaps。
    是企业运转逻辑的单一事实来源，Enterprise Runtime 由它编译而来。
    """
    __tablename__ = "enterprise_operating_models"

    enterprise_id = Column(String(36), nullable=False, index=True)
    model = Column(JSON, nullable=False, default=dict)  # 运行模型数据
    version = Column(String(32), nullable=False, default="v1.0.0")
    completeness = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
