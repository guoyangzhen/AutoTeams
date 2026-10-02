"""v3 集成测试扩展 fixtures。

提供：
1. mock_llm: 统一 LLM mock（所有 WT 共用，避免真实 LLM 调用）
2. mock_vector_store: 统一 ChromaDB mock（内存存储，避免外部依赖）
3. mock_redis: 统一 Redis mock（内存回退）
4. v3_tables: 确保 v3 迁移表存在于测试数据库（Base.metadata.create_all 不覆盖 v3 表）
5. demo_enterprise: 智链物联示例企业 fixture（5 Agent + 7 步案例数据）

使用方式（在测试文件中）：
    from tests.conftest_extensions import mock_llm, demo_enterprise

    async def test_xxx(demo_enterprise, mock_llm):
        enterprise, agents, users = demo_enterprise
        ...

依据：
    - docs/重构方案_v3.md §9.6 阶段 4（集成测试）
    - docs/spec.md §10.8（迁移需求契约）
    - backend/tests/conftest.py（现有 fixture 基础）
"""
import os
import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.utils.security import get_password_hash


# ============================================================
# 1. LLM Mock Fixture
# ============================================================


@pytest.fixture
def mock_llm():
    """统一 LLM mock：所有 LLM 调用返回预设响应，避免真实 API 调用。

    默认返回符合业务场景的 JSON 响应。测试中可覆盖 mock.return_value。
    """
    default_response = json.dumps({
        "analysis": "示例分析结果",
        "confidence": 0.85,
        "suggestions": ["建议1", "建议2"],
    }, ensure_ascii=False)

    with patch("app.services.llm_service.llm_service") as mock_service:
        mock_service.chat = AsyncMock(return_value=default_response)
        mock_service.chat_sync = MagicMock(return_value=default_response)
        yield mock_service


@pytest.fixture
def mock_llm_with_responses():
    """带多轮响应的 LLM mock：按顺序返回不同的响应。

    用法：
        mock = mock_llm_with_responses(["resp1", "resp2", "resp3"])
        mock.chat.return_value  # 第一次返回 "resp1"
    """
    def _factory(responses: list[str]):
        if not responses:
            responses = ["{}"]
        mock_service = AsyncMock()
        mock_service.chat = AsyncMock(side_effect=responses)
        return mock_service

    return _factory


# ============================================================
# 2. ChromaDB Mock Fixture
# ============================================================


class _MockVectorStore:
    """内存向量存储 mock，模拟 ChromaDB 的 add/count/delete/search 接口。"""

    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self._documents: list[str] = []
        self._metadatas: list[dict] = []
        self._ids: list[str] = []

    async def add_documents(self, documents, metadatas, ids):
        for doc, meta, id_ in zip(documents, metadatas, ids, strict=False):
            if id_ not in self._ids:
                self._documents.append(doc)
                self._metadatas.append(meta)
                self._ids.append(id_)

    async def count(self):
        return len(self._documents)

    async def delete_collection(self):
        self._documents.clear()
        self._metadatas.clear()
        self._ids.clear()

    async def search(self, query: str, top_k: int = 5):
        """模拟检索：返回所有已存储的文档（截断到 top_k）。"""
        results = []
        for i, (doc, meta) in enumerate(zip(self._documents, self._metadatas, strict=False)):
            results.append({
                "content": doc,
                "metadata": meta,
                "score": 1.0 - (i * 0.01),  # 模拟相似度递减
            })
        return results[:top_k]


@pytest.fixture
def mock_vector_store():
    """统一 ChromaDB mock：使用内存存储替代真实 ChromaDB。

    所有 VectorStoreService.create 调用返回 _MockVectorStore 实例。
    """
    _stores: dict[str, _MockVectorStore] = {}

    async def _create(collection_name: str):
        if collection_name not in _stores:
            _stores[collection_name] = _MockVectorStore(collection_name)
        return _stores[collection_name]

    with patch("app.services.vector_store.VectorStoreService.create", new=_create):
        yield _stores


# ============================================================
# 3. Redis Mock Fixture
# ============================================================


@pytest.fixture
def mock_redis():
    """统一 Redis mock：使用内存字典替代真实 Redis。

    Mock 的接口：get/set/delete/exists/expire/ttl/keys。
    """
    _store: dict[str, str] = {}
    _ttl: dict[str, float] = {}

    class _MockRedis:
        async def get(self, key: str):
            return _store.get(key)

        async def set(self, key: str, value: str, ex: int = None):
            _store[key] = value
            if ex:
                _ttl[key] = (datetime.now(timezone.utc) + timedelta(seconds=ex)).timestamp()

        async def delete(self, key: str):
            _store.pop(key, None)
            _ttl.pop(key, None)
            return 1

        async def exists(self, key: str):
            return key in _store

        def set_sync(self, key: str, value: str, ex: int = None):
            _store[key] = value
            if ex:
                _ttl[key] = (datetime.now(timezone.utc) + timedelta(seconds=ex)).timestamp()

        def get_sync(self, key: str):
            return _store.get(key)

        def delete_sync(self, key: str):
            _store.pop(key, None)
            _ttl.pop(key, None)

        def keys(self, pattern: str = "*"):
            import fnmatch
            return [k for k in _store.keys() if fnmatch.fnmatch(k, pattern)]

    mock = _MockRedis()
    with patch("app.utils.cache.redis_client", mock), \
         patch("app.utils.token_blacklist.redis_client", mock):
        yield mock


# ============================================================
# 4. V3 Tables Helper
# ============================================================


# v3 表 DDL（从迁移文件提取，用于在测试数据库中创建 v3 表）
# 已对齐 WT1（cognition.py/compiler.py）与 WT2（runtime.py）实际模型定义，
# 包含修正迁移 b1c2d3e4f5a1/b2c3d4e5f6a2/b3c4d5e6f7a3 补全的字段。
V3_TABLE_DDL = [
    # WT1: 认知层（字段名与 WT1 模型一致：profile/model 而非 profile_data/model_data）
    """CREATE TABLE IF NOT EXISTS knowledge_graphs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        nodes JSON NOT NULL,
        edges JSON NOT NULL,
        version VARCHAR(32) NOT NULL DEFAULT 'v1.0.0',
        is_active BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        UNIQUE(enterprise_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS enterprise_profiles (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        profile JSON NOT NULL,
        version VARCHAR(32) NOT NULL DEFAULT 'v1.0.0',
        completeness_score INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        UNIQUE(enterprise_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS enterprise_operating_models (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        model JSON NOT NULL,
        version VARCHAR(32) NOT NULL DEFAULT 'v1.0.0',
        completeness INTEGER NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        UNIQUE(enterprise_id, version)
    )""",
    # WT1: 编译器（含 trigger_source/affected_stages/lease_owner 等，对齐 CompilationJob 模型）
    """CREATE TABLE IF NOT EXISTS compilation_jobs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        trigger_source VARCHAR(64) NOT NULL DEFAULT 'manual',
        stage VARCHAR(32) NOT NULL DEFAULT 'information',
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        folder_path TEXT,
        interview_completion FLOAT NOT NULL DEFAULT 0.0,
        idempotency_key VARCHAR(128),
        lease_owner VARCHAR(128),
        lease_until DATETIME,
        heartbeat_at DATETIME,
        attempt INTEGER NOT NULL DEFAULT 0,
        cancel_requested BOOLEAN NOT NULL DEFAULT 0,
        affected_stages VARCHAR(256),
        confidence FLOAT NOT NULL DEFAULT 0,
        completeness FLOAT NOT NULL DEFAULT 0,
        progress FLOAT NOT NULL DEFAULT 0.0,
        stage_started_at DATETIME,
        error_message TEXT,
        started_at DATETIME,
        completed_at DATETIME,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE
    )""",
    # WT1: 编译产物（CompilationArtifact，WT1 base.py/pipeline.py 直接读写）
    """CREATE TABLE IF NOT EXISTS compilation_artifacts (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        job_id VARCHAR(36) NOT NULL,
        stage VARCHAR(32) NOT NULL,
        output JSON NOT NULL,
        confidence FLOAT NOT NULL DEFAULT 0,
        discovered_summary TEXT,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        FOREIGN KEY(job_id) REFERENCES compilation_jobs(id) ON DELETE CASCADE
    )""",
    # WT2: Runtime（含 created_by，对齐 WT2 模型 + 修正迁移 b4c5d6e7f8a4 的索引名/FK）
    """CREATE TABLE IF NOT EXISTS enterprise_runtimes (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        version VARCHAR(32) NOT NULL,
        model_version VARCHAR(32) NOT NULL,
        compiled_at DATETIME NOT NULL,
        completeness FLOAT NOT NULL DEFAULT 0,
        runtime_data JSON NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT 0,
        created_by VARCHAR(36),
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT uq_runtime_enterprise_version UNIQUE (enterprise_id, version)
    )""",
    # WT2: Runtime 版本（含 enterprise_id/event_type/metadata/updated_at，对齐 WT2 模型 + 修正迁移 c5d6e7f8a9b5 NOT NULL）
    """CREATE TABLE IF NOT EXISTS runtime_versions (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        runtime_id VARCHAR(36) NOT NULL,
        enterprise_id VARCHAR(36) NOT NULL,
        version VARCHAR(32) NOT NULL,
        event_type VARCHAR(32) NOT NULL DEFAULT 'save',
        changelog TEXT,
        is_active BOOLEAN NOT NULL DEFAULT 0,
        created_by VARCHAR(36),
        metadata JSON,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(runtime_id) REFERENCES enterprise_runtimes(id) ON DELETE CASCADE,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
    )""",
    # WT3: Workforce
    """CREATE TABLE IF NOT EXISTS workforce_lifecycle (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        agent_id VARCHAR(36) NOT NULL,
        enterprise_id VARCHAR(36) NOT NULL,
        stage VARCHAR(32) NOT NULL,
        stage_entered_at DATETIME NOT NULL,
        transition_reason TEXT,
        metadata JSON,
        FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE
    )""",
    # WT3: 记忆
    """CREATE TABLE IF NOT EXISTS long_term_memories (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        agent_id VARCHAR(36) NOT NULL,
        enterprise_id VARCHAR(36) NOT NULL,
        conversation_id VARCHAR(36),
        summary TEXT NOT NULL,
        vector_id VARCHAR(128),
        created_at DATETIME NOT NULL,
        FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
    )""",
    """CREATE TABLE IF NOT EXISTS entity_memories (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        agent_id VARCHAR(36) NOT NULL,
        enterprise_id VARCHAR(36) NOT NULL,
        entity_type VARCHAR(32) NOT NULL,
        entity_id VARCHAR(64) NOT NULL,
        attributes JSON NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE,
        UNIQUE(agent_id, entity_type, entity_id)
    )""",
    # WT4: 进化层
    """CREATE TABLE IF NOT EXISTS advisor_suggestions (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        type VARCHAR(32) NOT NULL,
        title VARCHAR(256) NOT NULL,
        description TEXT NOT NULL,
        impact TEXT,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        applied_at DATETIME,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE
    )""",
    # WT4: 组织分析指标（对齐迁移 a8b9c0d1f4e8：metric_type + metric_value(JSON) + period）
    """CREATE TABLE IF NOT EXISTS org_metrics (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        metric_type VARCHAR(32) NOT NULL,
        metric_value JSON NOT NULL,
        period VARCHAR(32),
        created_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE
    )""",
    # WT4: 访谈
    """CREATE TABLE IF NOT EXISTS interview_sessions (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        answered_count INTEGER NOT NULL DEFAULT 0,
        total_count INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE
    )""",
    # WT4: 访谈问题（对齐迁移 a9b0c1d2f5e9 + 修正迁移 c5d6e7f8a9b5：含 created_at）
    """CREATE TABLE IF NOT EXISTS interview_questions (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        session_id VARCHAR(36) NOT NULL,
        category VARCHAR(32) NOT NULL,
        question TEXT NOT NULL,
        expected_output TEXT,
        affected_field VARCHAR(128),
        priority VARCHAR(4) NOT NULL DEFAULT 'P1',
        answer TEXT,
        answered_at DATETIME,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(session_id) REFERENCES interview_sessions(id) ON DELETE CASCADE
    )""",
    # WT4: 协作
    """CREATE TABLE IF NOT EXISTS collaboration_events (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        payload JSON NOT NULL,
        source_agent_id VARCHAR(36),
        target_agent_id VARCHAR(36),
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        created_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS approval_gates (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        enterprise_id VARCHAR(36) NOT NULL,
        process_id VARCHAR(64) NOT NULL,
        node_id VARCHAR(64) NOT NULL,
        agent_id VARCHAR(36),
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        approver_id VARCHAR(36),
        decided_at DATETIME,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(enterprise_id) REFERENCES enterprises(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS operation_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        agent_id VARCHAR(36) NOT NULL,
        operation VARCHAR(128) NOT NULL,
        snapshot JSON NOT NULL,
        created_at DATETIME NOT NULL,
        FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE
    )""",
]

# v3 扩展字段（加性迁移：仅新增字段到现有表）
# 来自迁移：
#   2026_07_29_0306-a4b5c6d7f0a4_extend_enterprise_fields.py
#   2026_07_29_0312-a7b8c9d0f3d7_extend_agent_fields.py
# SQLite 不支持 IF NOT EXISTS 于 ADD COLUMN，使用 try/except 容错
V3_ALTER_DDL = [
    "ALTER TABLE enterprises ADD COLUMN current_runtime_version_id VARCHAR(36)",
    "ALTER TABLE agents ADD COLUMN lifecycle_stage VARCHAR(32) DEFAULT 'recruit'",
    "ALTER TABLE agents ADD COLUMN memory_config JSON",
    "ALTER TABLE agents ADD COLUMN kpi_ids JSON",
    "ALTER TABLE agents ADD COLUMN position_id VARCHAR(64)",
]


@pytest_asyncio.fixture
async def v3_tables(test_engine):
    """确保 v3 迁移表与扩展字段存在于测试数据库。

    Base.metadata.create_all 只创建 ORM 模型对应的表与字段，
    v3 新增内容（collaboration_events/approval_gates 等新表 +
    agents.lifecycle_stage 等扩展字段）需通过 DDL 显式创建。

    SQLite 的 ADD COLUMN 不支持 IF NOT EXISTS，重复执行会报错，
    故使用 try/except 容错（幂等）。
    """
    async with test_engine.begin() as conn:
        for ddl in V3_TABLE_DDL:
            await conn.execute(text(ddl))
        # v3 扩展字段（加性迁移），重复执行容错
        for ddl in V3_ALTER_DDL:
            try:
                await conn.execute(text(ddl))
            except Exception:
                pass  # 字段已存在
    yield test_engine


# ============================================================
# 5. 示例企业 Fixture（智链物联）
# ============================================================


DEMO_ENTERPRISE_ID = "demo-zhilian-v3-test"
DEMO_ENTERPRISE_NAME = "智链物联（测试）"
DEMO_EMAIL = "test-v3@smartlink-iot.com"
DEMO_PASSWORD = "demo123456"

# 5 个 AI 员工定义
V3_TEST_AGENTS = [
    {
        "name": "销售 Agent（测试）",
        "description": "销售代表 AI 员工，负责询盘响应、报价生成。",
        "system_prompt": "你是智链物联销售代表 AI 员工。职责：询盘响应、报价生成、客户跟进。",
        "position_id": "SL-2021-045",
    },
    {
        "name": "产品专家 Agent（测试）",
        "description": "售前技术支持 AI 员工，负责产品参数查询。",
        "system_prompt": "你是智链物联售前技术支持 AI 员工。职责：产品参数查询、技术方案补充。",
        "position_id": "SL-2020-032",
    },
    {
        "name": "财务 Agent（测试）",
        "description": "财务经理 AI 员工，负责价格合规审核。",
        "system_prompt": "你是智链物联财务经理 AI 员工。职责：价格合规审核、税点校验。",
        "position_id": "SL-2018-005",
    },
    {
        "name": "客服 Agent（测试）",
        "description": "客服专员 AI 员工，负责订单同步、客户档案。",
        "system_prompt": "你是智链物联客服专员 AI 员工。职责：订单同步、客户档案创建、交付通知。",
        "position_id": "SL-2022-062",
    },
    {
        "name": "售后 Agent（测试）",
        "description": "售后服务专员 AI 员工，负责售后跟进。",
        "system_prompt": "你是智链物联售后服务专员 AI 员工。职责：售后跟进、客户反馈记录。",
        "position_id": "SL-2020-038",
    },
]


def _utc_now():
    return datetime.now(timezone.utc)


def _days_ago(days: int, hour: int = 10):
    now = _utc_now()
    target = now - timedelta(days=days)
    return target.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest_asyncio.fixture
async def demo_enterprise(db_session, v3_tables):
    """初始化智链物联示例企业 fixture。

    返回：(enterprise, agents, users)
    - enterprise: Enterprise ORM 对象
    - agents: 5 个 Agent ORM 对象（销售/产品专家/财务/客服/售后）
    - users: 5 个 User 对象（CEO/销售/客服/财务/售后）

    包含 7 步演示案例初始数据：
    - 2 条商机事件（OPP-001 / OPP-005）
    - 2 条报价单草稿
    - 2 条审批流记录
    - 1 条触发事件
    """
    db = db_session

    # 1. 创建企业
    enterprise = Enterprise(
        id=DEMO_ENTERPRISE_ID,
        name=DEMO_ENTERPRISE_NAME,
        is_active=True,
    )
    db.add(enterprise)
    await db.flush()

    # 2. 创建用户（原生 SQL，绕过 ORM refresh_token_family_id 缺失问题）
    users_data = [
        (DEMO_EMAIL, "张明远（CEO）", "admin"),
        ("chen.sy@test.com", "陈思远（销售）", "member"),
        ("huang.sq@test.com", "黄思琪（客服）", "member"),
        ("he.dz@test.com", "何德志（财务）", "member"),
        ("xu.jh@test.com", "徐建华（售后）", "member"),
    ]
    users = []
    now = _utc_now()
    for email, name, role in users_data:
        user_id = str(uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO users "
                "(id, email, password_hash, name, role, enterprise_id, "
                "is_active, created_at, updated_at) "
                "VALUES (:id, :email, :ph, :name, :role, :eid, 1, :now, :now)"
            ),
            {
                "id": user_id,
                "email": email,
                "ph": get_password_hash(DEMO_PASSWORD),
                "name": name,
                "role": role,
                "eid": enterprise.id,
                "now": now,
            },
        )
        user = User(
            id=user_id, email=email, password_hash="", name=name,
            role=role, enterprise_id=enterprise.id, is_active=True,
        )
        users.append(user)

    # 3. 创建 5 个 AI 员工 Agent
    agents = []
    for data in V3_TEST_AGENTS:
        agent = Agent(
            enterprise_id=enterprise.id,
            name=data["name"],
            description=data["description"],
            system_prompt=data["system_prompt"],
            status="ready",
            version="3.0.0",
            folder_path=f"./uploads/test-v3/{data['position_id']}",
            config={"temperature": 0.3, "top_k": 5, "max_tokens": 2000},
        )
        db.add(agent)
        agents.append(agent)
    await db.flush()

    # 设置 v3 扩展字段（lifecycle_stage / position_id）
    # MVP 3 阶段：recruit / training / production，演示用 Agent 已在生产阶段
    for agent, data in zip(agents, V3_TEST_AGENTS, strict=False):
        await db.execute(
            text(
                "UPDATE agents SET lifecycle_stage = 'production', "
                "position_id = :pos_id WHERE id = :aid"
            ),
            {"pos_id": data["position_id"], "aid": agent.id},
        )

    # 4. 创建 Enterprise Runtime
    runtime_id = f"{DEMO_ENTERPRISE_ID}-runtime"
    await db.execute(
        text(
            "INSERT INTO enterprise_runtimes "
            "(id, enterprise_id, version, model_version, compiled_at, "
            "completeness, runtime_data, is_active, created_at, updated_at) "
            "VALUES (:id, :eid, :ver, :mv, :cat, :comp, :rdata, 1, :now, :now)"
        ),
        {
            "id": runtime_id,
            "eid": enterprise.id,
            "ver": "v3.0.0",
            "mv": "test-compiler-1.0",
            "cat": now,
            "comp": 0.85,
            "rdata": json.dumps({
                "enterprise_name": "智链物联",
                "departments": 7,
                "positions": 24,
            }),
            "now": now,
        },
    )

    # 5. 创建五级编译任务
    # 字段对齐 WT1 模型：trigger_source / affected_stages
    # 并创建对应的 compilation_artifacts 记录（WT1 base.py/pipeline.py 直接读写）
    for stage in ["information", "knowledge", "process", "capability", "runtime"]:
        job_id = f"{DEMO_ENTERPRISE_ID}-compile-{stage}"
        await db.execute(
            text(
                "INSERT INTO compilation_jobs "
                "(id, enterprise_id, trigger_source, stage, status, confidence, completeness, "
                "started_at, completed_at, created_at, updated_at) "
                "VALUES (:id, :eid, 'manual', :stage, 'completed', 0.85, 0.85, "
                ":start, :end, :now, :now)"
            ),
            {
                "id": job_id,
                "eid": enterprise.id,
                "stage": stage,
                "start": _days_ago(1, 10),
                "end": _days_ago(1, 11),
                "now": now,
            },
        )
        # 同步创建编译产物（compilation_artifacts，WT1 pipeline 读写）
        await db.execute(
            text(
                "INSERT INTO compilation_artifacts "
                "(id, enterprise_id, job_id, stage, output, confidence, "
                "discovered_summary, created_at, updated_at) "
                "VALUES (:id, :eid, :jid, :stage, :out, 0.85, :summary, :now, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-artifact-{stage}",
                "eid": enterprise.id,
                "jid": job_id,
                "stage": stage,
                "out": json.dumps({"stage": stage, "items_discovered": 10}, ensure_ascii=False),
                "summary": f"{stage} 级编译完成，发现 10 项",
                "now": now,
            },
        )

    # 6. 创建 7 步演示案例初始数据
    sales_agent = agents[0]
    finance_agent = agents[2]

    # 商机事件
    for opp_id, cust_id, cust_name, amount in [
        ("OPP-001", "C-001", "华智新能源", 128400),
        ("OPP-005", "C-005", "深圳智链园区", 15300),
    ]:
        await db.execute(
            text(
                "INSERT INTO collaboration_events "
                "(id, enterprise_id, event_type, payload, target_agent_id, status, created_at) "
                "VALUES (:id, :eid, 'opportunity_created', :payload, :tgt, 'pending', :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-opp-{opp_id}",
                "eid": enterprise.id,
                "payload": json.dumps({
                    "opp_id": opp_id, "customer_id": cust_id,
                    "customer_name": cust_name, "estimated_amount": amount,
                    "stage": "报价中",
                }),
                "tgt": sales_agent.id,
                "now": _days_ago(2, 10),
            },
        )

    # 报价单草稿
    for quo_id, opp_id, amount in [
        ("QUO-2026-001", "OPP-001", 128400),
        ("QUO-2026-002", "OPP-005", 15300),
    ]:
        await db.execute(
            text(
                "INSERT INTO collaboration_events "
                "(id, enterprise_id, event_type, payload, source_agent_id, target_agent_id, status, created_at) "
                "VALUES (:id, :eid, 'quotation_generated', :payload, :src, :tgt, 'pending', :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-quo-{quo_id}",
                "eid": enterprise.id,
                "payload": json.dumps({
                    "quotation_id": quo_id, "opp_id": opp_id,
                    "total_amount": amount, "status": "草稿",
                }),
                "src": sales_agent.id,
                "tgt": finance_agent.id,
                "now": _days_ago(1, 14),
            },
        )

    # 审批流记录
    for appr_id, _quo_id, _amount, tier in [
        ("APR-2026-001", "QUO-2026-001", 128400, "5-20万"),
        ("APR-2026-002", "QUO-2026-002", 15300, "<5万"),
    ]:
        # 财务审核节点
        await db.execute(
            text(
                "INSERT INTO approval_gates "
                "(id, enterprise_id, process_id, node_id, agent_id, status, approver_id, created_at) "
                "VALUES (:id, :eid, :pid, 'financial_review', :aid, 'pending', :approver, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-appr-{appr_id}-fin",
                "eid": enterprise.id,
                "pid": appr_id,
                "aid": finance_agent.id,
                "approver": users[3].id,
                "now": now,
            },
        )
        # 分级审批节点
        await db.execute(
            text(
                "INSERT INTO approval_gates "
                "(id, enterprise_id, process_id, node_id, status, approver_id, created_at) "
                "VALUES (:id, :eid, :pid, :nid, 'pending', :approver, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-appr-{appr_id}-tier",
                "eid": enterprise.id,
                "pid": appr_id,
                "nid": f"tier_approval_{tier}",
                "approver": users[0].id,
                "now": now,
            },
        )

    # 触发事件（新询盘）
    await db.execute(
        text(
            "INSERT INTO collaboration_events "
            "(id, enterprise_id, event_type, payload, target_agent_id, status, created_at) "
            "VALUES (:id, :eid, 'inquiry_received', :payload, :tgt, 'pending', :now)"
        ),
        {
            "id": f"{DEMO_ENTERPRISE_ID}-trigger-inquiry",
            "eid": enterprise.id,
            "payload": json.dumps({
                "step": 1, "opp_id": "OPP-001",
                "customer_name": "华智新能源",
                "inquiry": "SL-T100×80+SL-GW500×2",
            }),
            "tgt": sales_agent.id,
            "now": now,
        },
    )

    await db.commit()

    yield enterprise, agents, users
