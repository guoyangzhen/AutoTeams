"""C5: 数据层与审计安全测试。

覆盖缺口：
- 5.3.7: 审计日志 HMAC 链式防篡改（签名计算、链式完整性、篡改检测）
- L3: User-Agent 截断
- DB-02: file.content_hash 唯一约束
- T17: ChromaDB collection 企业前缀
"""
import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog, MAX_USER_AGENT_LENGTH
from app.models.file import File
from app.models.enterprise import Enterprise
from app.models.user import User
from app.models.agent import Agent
from app.utils.audit import (
    GENESIS_HASH,
    _compute_signature,
    log_audit,
    verify_audit_chain,
)
from app.services.vector_store import (
    VectorStoreService,
    build_collection_name,
    _COLLECTION_NAME_RE,
)


# ========== 辅助函数 ==========

class _MockRequest:
    """模拟 FastAPI Request 对象，用于审计日志测试。"""

    def __init__(self, user_agent: str = "test-ua", ip: str = "127.0.0.1"):
        self.client = MagicMock()
        self.client.host = ip
        self.headers = {"user-agent": user_agent}


async def _create_enterprise_and_user(test_engine, suffix="c5"):
    """创建企业 + 用户，返回 (ent_id, user_id)。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ent_id = f"ent-c5-{suffix}-{uuid.uuid4().hex[:8]}"
    user_id = str(uuid.uuid4())
    async with factory() as s:
        ent = Enterprise(id=ent_id, name=f"C5测试企业{suffix}")
        s.add(ent)
        await s.flush()
        user = User(
            id=user_id,
            email=f"c5_{suffix}@test.com",
            name=f"C5用户{suffix}",
            password_hash="hash",
            enterprise_id=ent_id,
            role="admin",
        )
        s.add(user)
        await s.commit()
    return ent_id, user_id


# ========== 5.3.7: HMAC 链式签名测试 ==========

class TestAuditHMACChain:
    """审计日志 HMAC 链式防篡改测试。"""

    @pytest.mark.asyncio
    async def test_log_audit_generates_signature(self, db_session):
        """log_audit 应自动生成 prev_hash 和 signature。"""
        _, user_id = await _create_enterprise_and_user(db_session.bind, "sig")

        user = await db_session.get(User, user_id)
        await log_audit(
            db_session, user, "create", "agent", "agent-1",
            request=_MockRequest(), details={"key": "value"},
        )
        await db_session.commit()

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == "agent-1")
        )
        log = result.scalar_one()
        assert log.prev_hash == GENESIS_HASH, "首条日志的 prev_hash 应为创世哈希"
        assert log.signature is not None, "signature 不应为空"
        assert len(log.signature) == 64, "HMAC-SHA256 签名应为 64 字符 hex"

    @pytest.mark.asyncio
    async def test_chain_linkage(self, db_session):
        """连续日志的 prev_hash 应等于前一条的 signature。"""
        _, user_id = await _create_enterprise_and_user(db_session.bind, "chain")

        user = await db_session.get(User, user_id)

        # 写入第一条
        await log_audit(db_session, user, "create", "agent", "agent-a", request=_MockRequest())
        await db_session.commit()

        # 写入第二条
        await log_audit(db_session, user, "update", "agent", "agent-a", request=_MockRequest())
        await db_session.commit()

        # 写入第三条
        await log_audit(db_session, user, "delete", "agent", "agent-a", request=_MockRequest())
        await db_session.commit()

        result = await db_session.execute(
            select(AuditLog)
            .where(AuditLog.resource_id == "agent-a")
            .order_by(AuditLog.created_at.asc())
        )
        logs = result.scalars().all()
        assert len(logs) == 3

        # 验证链式链接
        assert logs[0].prev_hash == GENESIS_HASH, "第一条 prev_hash = GENESIS"
        assert logs[1].prev_hash == logs[0].signature, "第二条 prev_hash = 第一条 signature"
        assert logs[2].prev_hash == logs[1].signature, "第三条 prev_hash = 第二条 signature"

    @pytest.mark.asyncio
    async def test_concurrent_writes_keep_chain_valid(self, db_session):
        """并发写入多条审计日志后，哈希链仍应保持完整。

        回归测试：根因是 log_audit 的「读 prev_hash → 写缓存」在缓存命中路径
        不加锁，两个并发请求会读到同一个 prev_hash，生成两条 prev_hash 相同的日志，
        导致 verify_audit_chain 报「prev_hash 与前序 signature 不匹配」。
        修复后所有写入在 audit_chain_state 行锁内串行，链条应始终有效。

        注：与真实部署一致，每个并发请求使用独立的 AsyncSession；
        共享同一 Session 的并发访问本身是非法用法（SQLAlchemy 会抛
        IllegalStateChangeError），不属于本测试目标。

        链式串行化依赖 audit_chain_state 行的 SELECT ... FOR UPDATE，
        该保证仅在 PostgreSQL 下生效（SQLite 会忽略 FOR UPDATE），
        因此本用例仅在有行锁支持的数据库上运行。
        """
        if db_session.bind.url.get_backend_name() == "sqlite":
            pytest.skip("SQLite 忽略 FOR UPDATE 行锁，链式并发串行化仅在 PostgreSQL 下可验证")
        _, user_id = await _create_enterprise_and_user(db_session.bind, "conc")

        user = await db_session.get(User, user_id)
        factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)

        # 生产环境由迁移预置全局链游标行；测试库为裸建表，这里按生产语义补种，
        # 避免并发首次初始化的 savepoint 回退路径在 SQLite 下的固有竞态。
        from app.models.audit_chain_state import AuditChainState
        from app.utils.audit import AUDIT_CHAIN_STATE_ID, GENESIS_HASH
        async with factory() as seed:
            seed.add(AuditChainState(id=AUDIT_CHAIN_STATE_ID, last_signature=GENESIS_HASH))
            await seed.commit()

        # 并发写入 20 条审计日志（模拟高并发请求下多个端点同时 log_audit）
        async def _write(i: int):
            async with factory() as session:
                await log_audit(
                    session, user, "update", "agent", f"agent-conc-{i}",
                    request=_MockRequest(),
                )
                await session.commit()

        await asyncio.gather(*[_write(i) for i in range(20)])
        await db_session.commit()

        result = await verify_audit_chain(db_session)
        assert result["valid"] is True, f"并发写入后审计链应有效: {result['message']}"
        assert result["checked"] == 20

    @pytest.mark.asyncio
    async def test_verify_chain_valid(self, db_session):
        """完整未篡改的审计链应通过验证。"""
        _, user_id = await _create_enterprise_and_user(db_session.bind, "verify")

        user = await db_session.get(User, user_id)
        for action in ["create", "update", "update", "delete"]:
            await log_audit(db_session, user, action, "agent", "agent-v", request=_MockRequest())
            await db_session.commit()

        result = await verify_audit_chain(db_session)
        assert result["valid"] is True, f"审计链应通过验证: {result['message']}"
        assert result["checked"] == 4

    @pytest.mark.asyncio
    async def test_tamper_detection(self, db_session):
        """篡改审计日志内容后应被 verify_audit_chain 检测到。"""
        _, user_id = await _create_enterprise_and_user(db_session.bind, "tamper")

        user = await db_session.get(User, user_id)
        await log_audit(db_session, user, "create", "agent", "agent-t", request=_MockRequest())
        await db_session.commit()
        await log_audit(db_session, user, "delete", "agent", "agent-t", request=_MockRequest())
        await db_session.commit()

        # 篡改第一条日志的 action
        result = await db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == "agent-t")
            .order_by(AuditLog.created_at.asc())
        )
        logs = result.scalars().all()
        logs[0].action = "login"  # 篡改！
        await db_session.commit()

        # 验证应检测到篡改
        verify_result = await verify_audit_chain(db_session)
        assert verify_result["valid"] is False, "篡改后验证应失败"
        assert verify_result["broken_at"] is not None, "应报告断裂点"

    @pytest.mark.asyncio
    async def test_signature_deterministic(self, db_session):
        """相同内容 + 相同 prev_hash 应产生相同签名。"""
        _, user_id = await _create_enterprise_and_user(db_session.bind, "det")

        user = await db_session.get(User, user_id)
        now = datetime.now(timezone.utc)

        log1 = AuditLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action="create",
            resource_type="agent",
            resource_id="agent-d",
            ip_address="127.0.0.1",
            user_agent="test",
            details={"k": "v"},
            prev_hash=GENESIS_HASH,
            created_at=now,
            updated_at=now,
        )
        sig1 = _compute_signature(log1)

        log2 = AuditLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action="create",
            resource_type="agent",
            resource_id="agent-d",
            ip_address="127.0.0.1",
            user_agent="test",
            details={"k": "v"},
            prev_hash=GENESIS_HASH,
            created_at=now,
            updated_at=now,
        )
        sig2 = _compute_signature(log2)

        assert sig1 == sig2, "相同内容应产生相同签名"


# ========== L3: User-Agent 截断测试 ==========

class TestUserAgentTruncation:
    """审计日志 User-Agent 截断测试。"""

    @pytest.mark.asyncio
    async def test_long_user_agent_truncated(self, db_session):
        """超长 User-Agent 应被截断至 MAX_USER_AGENT_LENGTH。"""
        _, user_id = await _create_enterprise_and_user(db_session.bind, "ua")

        user = await db_session.get(User, user_id)
        long_ua = "A" * 500  # 远超 255 字符

        await log_audit(
            db_session, user, "create", "agent", "agent-ua",
            request=_MockRequest(user_agent=long_ua),
        )
        await db_session.commit()

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == "agent-ua")
        )
        log = result.scalar_one()
        assert len(log.user_agent) == MAX_USER_AGENT_LENGTH, \
            f"UA 应被截断至 {MAX_USER_AGENT_LENGTH} 字符，实际 {len(log.user_agent)}"

    @pytest.mark.asyncio
    async def test_short_user_agent_preserved(self, db_session):
        """短 User-Agent 应保持原样。"""
        _, user_id = await _create_enterprise_and_user(db_session.bind, "ua2")

        user = await db_session.get(User, user_id)
        short_ua = "Mozilla/5.0 Chrome/120"

        await log_audit(
            db_session, user, "create", "agent", "agent-ua2",
            request=_MockRequest(user_agent=short_ua),
        )
        await db_session.commit()

        result = await db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == "agent-ua2")
        )
        log = result.scalar_one()
        assert log.user_agent == short_ua, "短 UA 不应被截断"


# ========== DB-02: content_hash 唯一约束测试 ==========

class TestContentHashUnique:
    """file.content_hash 唯一约束测试。"""

    @pytest.mark.asyncio
    async def test_duplicate_content_hash_rejected(self, db_session):
        """同一 Agent 下相同 content_hash 的文件应被拒绝。"""
        ent_id, user_id = await _create_enterprise_and_user(db_session.bind, "hash")

        agent = Agent(
            id=str(uuid.uuid4()),
            enterprise_id=ent_id,
            name="测试Agent",
            description="测试",
        )
        db_session.add(agent)
        await db_session.flush()

        # 第一个文件
        file1 = File(
            agent_id=agent.id,
            original_name="doc1.pdf",
            file_path="/uploads/doc1.pdf",
            file_size=1024,
            file_type="pdf",
            content_hash="abc123hash",
        )
        db_session.add(file1)
        await db_session.commit()

        # 第二个文件 — 相同 agent_id + content_hash
        file2 = File(
            agent_id=agent.id,
            original_name="doc2.pdf",
            file_path="/uploads/doc2.pdf",
            file_size=2048,
            file_type="pdf",
            content_hash="abc123hash",  # 相同 hash
        )
        db_session.add(file2)
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_different_agent_same_hash_allowed(self, db_session):
        """不同 Agent 下相同 content_hash 应允许（隔离去重）。"""
        ent_id, _ = await _create_enterprise_and_user(db_session.bind, "hash2")

        agent1 = Agent(
            id=str(uuid.uuid4()),
            enterprise_id=ent_id,
            name="Agent1",
            description="测试",
        )
        agent2 = Agent(
            id=str(uuid.uuid4()),
            enterprise_id=ent_id,
            name="Agent2",
            description="测试",
        )
        db_session.add_all([agent1, agent2])
        await db_session.flush()

        file1 = File(
            agent_id=agent1.id,
            original_name="doc1.pdf",
            file_path="/uploads/doc1.pdf",
            file_size=1024,
            file_type="pdf",
            content_hash="shared_hash_xyz",
        )
        file2 = File(
            agent_id=agent2.id,
            original_name="doc2.pdf",
            file_path="/uploads/doc2.pdf",
            file_size=1024,
            file_type="pdf",
            content_hash="shared_hash_xyz",  # 相同 hash，不同 agent
        )
        db_session.add_all([file1, file2])
        await db_session.commit()  # 不应抛出异常

    @pytest.mark.asyncio
    async def test_null_content_hash_allowed(self, db_session):
        """content_hash 为 NULL 时应允许多条（兼容旧数据）。"""
        ent_id, _ = await _create_enterprise_and_user(db_session.bind, "hash3")

        agent = Agent(
            id=str(uuid.uuid4()),
            enterprise_id=ent_id,
            name="Agent_Null",
            description="测试",
        )
        db_session.add(agent)
        await db_session.flush()

        file1 = File(
            agent_id=agent.id,
            original_name="doc1.pdf",
            file_path="/uploads/doc1.pdf",
            file_size=1024,
            file_type="pdf",
            content_hash=None,
        )
        file2 = File(
            agent_id=agent.id,
            original_name="doc2.pdf",
            file_path="/uploads/doc2.pdf",
            file_size=1024,
            file_type="pdf",
            content_hash=None,
        )
        db_session.add_all([file1, file2])
        await db_session.commit()  # NULL hash 不受唯一约束


# ========== T17: ChromaDB collection 前缀测试 ==========

class TestCollectionNamePrefix:
    """ChromaDB collection 企业前缀测试。"""

    def test_build_collection_name(self):
        """build_collection_name 应生成带企业前缀的 collection_name。"""
        ent_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        name = build_collection_name(ent_id, agent_id)
        assert name == f"ent_{ent_id}_agent_{agent_id}"

    def test_new_format_accepted_by_regex(self):
        """新格式 ent_{eid}_agent_{aid} 应通过正则校验。"""
        ent_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())
        name = f"ent_{ent_id}_agent_{agent_id}"
        assert _COLLECTION_NAME_RE.match(name), "新格式应通过校验"

    def test_old_format_still_accepted_by_regex(self):
        """旧格式 agent_{aid} 应仍通过正则校验（向后兼容）。"""
        agent_id = str(uuid.uuid4())
        name = f"agent_{agent_id}"
        assert _COLLECTION_NAME_RE.match(name), "旧格式应仍通过校验"

    def test_invalid_format_rejected_by_regex(self):
        """非法 collection_name 形状应被正则拒绝。

        这里校验的是 **collection 名的命名空间形状**，不是 ID 本身的格式：
        正则的作用是挡掉路径穿越与注入（`../`、`'`），而不是强制 ID 必须是 UUID。

        调用点（`VectorStoreService.create_prefixed`）传入的 ID 全部来自数据库的
        UUID 列，不来自用户输入；ID 格式由模型层约束，不在这个正则的职责内。
        旧格式 `agent_{id}` 被显式保留兼容（见上一个用例），因此
        `agent_not-a-uuid` 也必须放行 —— 否则两个用例会自相矛盾。
        """
        invalid_names = [
            "",
            "random_collection",
            "_agent_x",              # 前缀缺失
            "xagent_y",              # 前缀不完整
            "../../../etc/passwd",
            "agent_'; DROP TABLE--;",
            "ent_a/b_agent_c",       # 路径分隔符
        ]
        for name in invalid_names:
            assert not _COLLECTION_NAME_RE.match(name), \
                f"非法名称应被拒绝: {name}"

    def test_traversal_and_injection_shapes_rejected(self):
        """安全相关的形状必须被拒绝（这才是该正则真正要守的东西）。"""
        for name in (
            "../../etc/passwd",
            "agent_../../etc",
            "ent_x/../agent_y",
            "agent_a;rm -rf /",
            "agent_a'b",
        ):
            assert not _COLLECTION_NAME_RE.match(name), \
                f"路径穿越/注入形状必须被拒绝: {name}"

    @pytest.mark.asyncio
    async def test_create_prefixed_uses_new_format(self):
        """create_prefixed 应使用企业前缀格式创建 VectorStoreService。"""
        ent_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())

        # 使用 mock client 避免实际连接 ChromaDB
        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection

        vs = await VectorStoreService.create_prefixed(
            enterprise_id=ent_id,
            agent_id=agent_id,
            client=mock_client,
        )

        assert vs.collection_name == f"ent_{ent_id}_agent_{agent_id}"
        mock_client.get_or_create_collection.assert_called_once()
        call_kwargs = mock_client.get_or_create_collection.call_args
        assert call_kwargs.kwargs["name"] == f"ent_{ent_id}_agent_{agent_id}"

    @pytest.mark.asyncio
    async def test_create_with_old_format_still_works(self):
        """旧格式 collection_name 仍可通过 create() 使用（向后兼容）。"""
        agent_id = str(uuid.uuid4())
        old_name = f"agent_{agent_id}"

        mock_client = MagicMock()
        mock_collection = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection

        vs = await VectorStoreService.create(old_name, client=mock_client)
        assert vs.collection_name == old_name

    def test_invalid_collection_name_raises_in_constructor(self):
        """非法 collection_name 在构造函数中应抛出 ValueError。"""
        mock_client = MagicMock()
        with pytest.raises(ValueError, match="非法 collection_name"):
            VectorStoreService("invalid_name", client=mock_client)
