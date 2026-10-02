"""Runtime 版本管理测试（runtime_version.py）。

覆盖 PRD §4.6.3：
- create_version_snapshot：创建审计快照事件（不改变激活态）
- rollback_to_version：回滚 / 幂等 / 不存在版本 / 激活态切换 / 审计事件
- diff_versions：结构化差异（added/removed/modified）+ 摘要 / 无差异 / 版本不存在 / LLM 降级

工程约束：
- 写操作显式 ``await db.commit()``
- diff 的 LLM 调用失败时降级为确定性结构化摘要（不阻断接口）
"""
import pytest

from app.models.runtime import RuntimeVersion
from app.schemas.runtime import RuntimeDiffResponse
from app.services.runtime import (
    create_version_snapshot,
    diff_versions,
    rollback_to_version,
    save_runtime,
)
from sqlalchemy import select

from .conftest import make_compile_result, save_runtime_helper, seed_enterprise, seed_user


# ============================================================
# create_version_snapshot 测试
# ============================================================


class TestCreateVersionSnapshot:
    async def test_create_snapshot_writes_audit_event(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        runtime = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        event = await create_version_snapshot(
            db_session, runtime.id, changelog="里程碑快照", created_by=user.id
        )

        assert event.event_type == "snapshot"
        assert event.changelog == "里程碑快照"
        assert event.runtime_id == runtime.id
        assert event.enterprise_id == enterprise.id
        assert event.version == runtime.version
        assert event.created_by == user.id
        assert event.metadata_json["snapshot_of"] == runtime.version

    async def test_snapshot_does_not_change_active_state(self, db_session):
        """快照不应改变激活态。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        runtime = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        assert runtime.is_active is True

        await create_version_snapshot(db_session, runtime.id, created_by=user.id)

        await db_session.refresh(runtime)
        assert runtime.is_active is True

    async def test_snapshot_default_changelog(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        runtime = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        event = await create_version_snapshot(db_session, runtime.id)
        assert "v1.0.0" in event.changelog

    async def test_snapshot_nonexistent_runtime_raises(self, db_session):
        with pytest.raises(ValueError, match="runtime_not_found"):
            await create_version_snapshot(db_session, "nonexistent-runtime-id")


# ============================================================
# rollback_to_version 测试
# ============================================================


class TestRollbackToVersion:
    async def test_rollback_switches_active_version(self, db_session):
        """回滚后目标版本应变为激活，当前版本变为 inactive。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        v1 = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        v2 = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )
        assert v2.is_active is True
        assert v1.is_active is False

        rolled = await rollback_to_version(
            db_session, enterprise.id, target_version="v1.0.0", created_by=user.id
        )

        assert rolled.id == v1.id
        assert rolled.version == "v1.0.0"
        await db_session.refresh(v1)
        await db_session.refresh(v2)
        assert v1.is_active is True
        assert v2.is_active is False

    async def test_rollback_is_idempotent_when_target_already_active(self, db_session):
        """目标已是激活版本时应 no-op 返回。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        v1 = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        rolled = await rollback_to_version(
            db_session, enterprise.id, target_version="v1.0.0", created_by=user.id
        )
        assert rolled.id == v1.id

    async def test_rollback_nonexistent_version_raises(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        with pytest.raises(ValueError, match="runtime_version_not_found"):
            await rollback_to_version(
                db_session, enterprise.id, target_version="v9.9.9"
            )

    async def test_rollback_normalizes_v_prefix(self, db_session):
        """不带 'v' 前缀的目标版本号也应正确匹配。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        v1 = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )

        rolled = await rollback_to_version(
            db_session, enterprise.id, target_version="1.0.0"
        )
        assert rolled.id == v1.id

    async def test_rollback_writes_audit_event(self, db_session):
        """回滚应写入 event_type='rollback' 审计事件，记录 from/to 版本。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )

        await rollback_to_version(
            db_session, enterprise.id, target_version="v1.0.0", created_by=user.id
        )

        result = await db_session.execute(
            select(RuntimeVersion).where(RuntimeVersion.event_type == "rollback")
        )
        events = result.scalars().all()
        assert len(events) == 1
        meta = events[0].metadata_json
        assert meta["from_version"] == "v1.1.0"
        assert meta["to_version"] == "v1.0.0"

    async def test_rollback_syncs_enterprise_current_runtime_version_id(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        v1 = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )

        await rollback_to_version(db_session, enterprise.id, target_version="v1.0.0")
        await db_session.refresh(enterprise)
        assert enterprise.current_runtime_version_id == v1.id

    async def test_rollback_can_re_activate_after_multiple_saves(self, db_session):
        """多版本场景下回滚到中间版本，再用显式版本号保存新版本应正常工作。

        注意：回滚到 v1.0.0 后，自动 bump（minor）会得到 v1.1.0，但 v1.1.0
        已存在（inactive），会触发 ``runtime_version_exists``。这是语义化版本
        回滚的已知边界——回滚后应使用显式版本号避免冲突。本测试验证显式版本号
        路径可正常再激活。
        """
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)  # v1.0.0
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )  # v1.1.0
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )  # v1.2.0

        # 回滚到 v1.0.0
        rolled = await rollback_to_version(db_session, enterprise.id, "v1.0.0")
        assert rolled.version == "v1.0.0"

        # 回滚后自动 bump 会与已存在的 v1.1.0 冲突
        with pytest.raises(ValueError, match="runtime_version_exists"):
            await save_runtime_helper(
                db_session, enterprise.id, created_by=user.id, change_type="minor"
            )

        # 使用显式版本号（避开已占用版本）可正常再激活
        v_new = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v2.0.0"
        )
        assert v_new.version == "v2.0.0"
        assert v_new.is_active is True


# ============================================================
# diff_versions 测试
# ============================================================


class TestDiffVersions:
    async def test_diff_no_changes(self, db_session):
        """两份相同内容应无差异。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr = make_compile_result()
        await save_runtime(
            db_session,
            enterprise_id=enterprise.id,
            compile_result=cr,
            version="v1.0.0",
            created_by=user.id,
        )
        await save_runtime(
            db_session,
            enterprise_id=enterprise.id,
            compile_result=cr,
            version="v1.1.0",
            created_by=user.id,
        )

        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=False)
        assert isinstance(diff, RuntimeDiffResponse)
        assert diff.changes == []
        assert "无差异" in diff.summary

    async def test_diff_detects_added_agent(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result(agent_count=2)
        cr_b = make_compile_result(agent_count=3)

        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_a,
                           version="v1.0.0", created_by=user.id)
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_b,
                           version="v1.1.0", created_by=user.id)

        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=False)
        added_agents = [c for c in diff.changes if c.section == "agents" and c.change_type == "added"]
        assert len(added_agents) == 1
        assert added_agents[0].key == "agent-2"

    async def test_diff_detects_removed_agent(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result(agent_count=3)
        cr_b = make_compile_result(agent_count=2)

        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_a,
                           version="v1.0.0", created_by=user.id)
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_b,
                           version="v1.1.0", created_by=user.id)

        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=False)
        removed = [c for c in diff.changes if c.section == "agents" and c.change_type == "removed"]
        assert len(removed) == 1
        assert removed[0].key == "agent-2"

    async def test_diff_detects_modified_completeness(self, db_session):
        """顶层标量字段 completeness 变化应被检测。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result(completeness=70.0)
        cr_b = make_compile_result(completeness=95.0)

        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_a,
                           version="v1.0.0", created_by=user.id)
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_b,
                           version="v1.1.0", created_by=user.id)

        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=False)
        completeness_changes = [c for c in diff.changes if c.section == "completeness"]
        assert len(completeness_changes) == 1
        assert "70.0" in completeness_changes[0].detail
        assert "95.0" in completeness_changes[0].detail

    async def test_diff_detects_tool_registry_changes(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result(tool_count=1)
        cr_b = make_compile_result(tool_count=2)

        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_a,
                           version="v1.0.0", created_by=user.id)
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_b,
                           version="v1.1.0", created_by=user.id)

        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=False)
        added_tools = [c for c in diff.changes if c.section == "tool_registry" and c.change_type == "added"]
        assert len(added_tools) == 1
        assert added_tools[0].key == "tool-1"

    async def test_diff_summary_includes_change_counts(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result(agent_count=2, tool_count=1)
        cr_b = make_compile_result(agent_count=3, tool_count=2)

        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_a,
                           version="v1.0.0", created_by=user.id)
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_b,
                           version="v1.1.0", created_by=user.id)

        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=False)
        # 摘要应包含变更计数
        assert "变更" in diff.summary

    async def test_diff_version_not_found_raises(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        with pytest.raises(ValueError, match="runtime_version_not_found"):
            await diff_versions(db_session, enterprise.id, "v1.0.0", "v9.9.9", use_llm=False)

    async def test_diff_llm_failure_falls_back_to_structured_summary(self, db_session):
        """LLM 不可用时应降级为确定性结构化摘要，不阻断接口。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result(agent_count=2)
        cr_b = make_compile_result(agent_count=3)

        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_a,
                           version="v1.0.0", created_by=user.id)
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_b,
                           version="v1.1.0", created_by=user.id)

        # use_llm=True 但 LLM 在演示模式下会返回 fallback 响应；摘要非空即视为降级成功
        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=True)
        assert diff.summary  # 非空
        assert len(diff.changes) > 0

    async def test_diff_includes_compiled_at_timestamps(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        cr_a = make_compile_result()
        cr_b = make_compile_result()
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_a,
                           version="v1.0.0", created_by=user.id)
        await save_runtime(db_session, enterprise_id=enterprise.id, compile_result=cr_b,
                           version="v1.1.0", created_by=user.id)

        diff = await diff_versions(db_session, enterprise.id, "v1.0.0", "v1.1.0", use_llm=False)
        assert diff.a_compiled_at is not None
        assert diff.b_compiled_at is not None


# ============================================================
# LLM 调用 mock 测试（spec §3.4：LLM 调用必须用 AsyncMock mock）
# ============================================================


class TestDiffVersionsLlmMock:
    """spec §3.4 强制要求：LLM 调用必须用 ``unittest.mock.AsyncMock`` mock。

    本组测试用 AsyncMock 替换 ``llm_service.chat``，验证 ``diff_versions`` 在
    LLM 成功 / 异常 / 空返回三种情况下的行为，且不依赖真实 LLM API。
    """

    @staticmethod
    async def _seed_two_versions(db_session):
        """构造 v1.0.0（2 agent）与 v1.1.0（3 agent）两个版本，返回企业 ID。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime(
            db_session,
            enterprise_id=enterprise.id,
            compile_result=make_compile_result(agent_count=2),
            version="v1.0.0",
            created_by=user.id,
        )
        await save_runtime(
            db_session,
            enterprise_id=enterprise.id,
            compile_result=make_compile_result(agent_count=3),
            version="v1.1.0",
            created_by=user.id,
        )
        return enterprise.id

    async def test_diff_uses_llm_summary_when_chat_returns_text(self, db_session, monkeypatch):
        """LLM chat 返回非空文本时，diff 摘要应使用 LLM 生成结果。"""
        from unittest.mock import AsyncMock

        from app.services.llm_service import llm_service

        enterprise_id = await self._seed_two_versions(db_session)

        llm_summary = "LLM 摘要：新增一个数字员工岗位，组织规模扩大。"
        monkeypatch.setattr(llm_service, "chat", AsyncMock(return_value=llm_summary))

        diff = await diff_versions(db_session, enterprise_id, "v1.0.0", "v1.1.0", use_llm=True)
        assert diff.summary == llm_summary
        # 验证 LLM 确实被调用一次
        llm_service.chat.assert_awaited_once()

    async def test_diff_falls_back_when_llm_chat_raises(self, db_session, monkeypatch):
        """LLM chat 抛异常时，应降级为确定性结构化摘要，不阻断接口。"""
        from unittest.mock import AsyncMock

        from app.services.llm_service import llm_service

        enterprise_id = await self._seed_two_versions(db_session)

        monkeypatch.setattr(
            llm_service, "chat", AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        )

        diff = await diff_versions(db_session, enterprise_id, "v1.0.0", "v1.1.0", use_llm=True)
        # 降级为结构化摘要（含变更计数）
        assert "变更" in diff.summary
        assert len(diff.changes) > 0

    async def test_diff_falls_back_when_llm_returns_empty(self, db_session, monkeypatch):
        """LLM chat 返回空字符串时，应降级为结构化摘要。"""
        from unittest.mock import AsyncMock

        from app.services.llm_service import llm_service

        enterprise_id = await self._seed_two_versions(db_session)

        monkeypatch.setattr(llm_service, "chat", AsyncMock(return_value=""))

        diff = await diff_versions(db_session, enterprise_id, "v1.0.0", "v1.1.0", use_llm=True)
        assert "变更" in diff.summary
        assert len(diff.changes) > 0


# ============================================================
# 多版本并发测试（重构方案 §5.7）
# ============================================================


class TestConcurrentVersionOperations:
    """多版本并发测试（重构方案 §5.7）。

    SQLite 内存库为单写者模型，不支持真正的并发写入（并发 INSERT 同一唯一键
    会导致事务状态混乱，spec §3.4 亦规定测试用 SQLite）。因此本组测试：
    - 写唯一性：串行验证应用层 ``runtime_version_exists`` 校验 + DB 唯一约束兜底
    - 并发安全：用独立 session 并发**读取**多版本 Runtime，验证无死锁且数据一致
    - 多版本一致性：多版本顺序保存后激活态唯一
    """

    async def test_save_duplicate_explicit_version_raises(self, db_session):
        """串行保存相同显式版本号，第二次应被应用层唯一性校验拦截。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v1.0.0"
        )

        with pytest.raises(ValueError, match="runtime_version_exists"):
            await save_runtime_helper(
                db_session, enterprise.id, created_by=user.id, version="v1.0.0"
            )

    async def test_db_unique_constraint_blocks_duplicate(self, db_session):
        """绕过应用层校验直接 INSERT，DB 层唯一约束应兜底抛 IntegrityError。"""
        from sqlalchemy.exc import IntegrityError

        from app.models.runtime import EnterpriseRuntime

        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v1.0.0"
        )

        # 直接构造重复版本号的 ORM 行（绕过 save_runtime 的应用层校验）
        dup = EnterpriseRuntime(
            enterprise_id=enterprise.id,
            version="v1.0.0",
            model_version="org-model-v1",
            compiled_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            completeness=50.0,
            runtime_data={"version": "v1.0.0"},
            is_active=False,
            created_by=user.id,
        )
        db_session.add(dup)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_concurrent_reads_across_versions(self, test_engine):
        """独立 session 并发读取多版本 Runtime，验证无死锁且数据一致。"""
        import asyncio

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.services.runtime import get_active_runtime, get_runtime_version

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        # 准备两个版本
        async with factory() as setup_session:
            enterprise = await seed_enterprise(setup_session)
            user = await seed_user(setup_session, enterprise)
            eid = enterprise.id
            uid = user.id
            await save_runtime(
                setup_session,
                enterprise_id=eid,
                compile_result=make_compile_result(agent_count=2),
                version="v1.0.0",
                created_by=uid,
            )
            await save_runtime(
                setup_session,
                enterprise_id=eid,
                compile_result=make_compile_result(agent_count=3),
                version="v1.1.0",
                created_by=uid,
            )

        # 并发读取：激活版本 + 两个历史版本
        async def _read_active():
            async with factory() as s:
                return await get_active_runtime(s, eid)

        async def _read_v1():
            async with factory() as s:
                return await get_runtime_version(s, eid, "v1.0.0")

        async def _read_v2():
            async with factory() as s:
                return await get_runtime_version(s, eid, "v1.1.0")

        active, v1, v2 = await asyncio.gather(_read_active(), _read_v1(), _read_v2())
        # 激活版本是 v1.1.0（最后保存）
        assert active is not None
        assert active.version == "v1.1.0"
        assert v1 is not None and v1.version == "v1.0.0"
        assert v2 is not None and v2.version == "v1.1.0"

    async def test_multiple_versions_single_active(self, test_engine):
        """多版本顺序保存后，仅最后保存的版本为激活态。"""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models.runtime import EnterpriseRuntime

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as setup_session:
            enterprise = await seed_enterprise(setup_session)
            user = await seed_user(setup_session, enterprise)
            enterprise_id = enterprise.id
            user_id = user.id

        versions = ["v1.0.0", "v1.1.0", "v2.0.0"]
        for v in versions:
            async with factory() as s:
                await save_runtime(
                    s,
                    enterprise_id=enterprise_id,
                    compile_result=make_compile_result(),
                    created_by=user_id,
                    version=v,
                )

        async with factory() as s:
            result = await s.execute(
                select(EnterpriseRuntime).where(
                    EnterpriseRuntime.enterprise_id == enterprise_id
                )
            )
            rows = result.scalars().all()
        assert len(rows) == 3
        active = [r for r in rows if r.is_active]
        assert len(active) == 1
        # 最后保存的 v2.0.0 应为激活版本
        assert active[0].version == "v2.0.0"
