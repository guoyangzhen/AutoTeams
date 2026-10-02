"""Runtime 存储层测试（runtime_store.py）。

覆盖 PRD §4.6 / §4.6.3 的存储与查询：
- bump_semver：语义化版本号递增（patch/minor/major/首次/异常兜底）
- save_runtime：首次保存 / 版本递增 / 显式版本 / 重复版本号 / 激活态切换 / 审计事件
- get_active_runtime：激活版本查询 / 无数据返回 None
- get_runtime_by_version：v 前缀兼容 / 不存在
- list_versions：分页 / 最新在前 / total 计数

工程约束验证：
- service 层写操作显式 ``await db.commit()``（save_runtime 内部已 commit）
- 激活态唯一性（每企业至多一个 is_active=True）
"""
import pytest

from app.models.enterprise import Enterprise
from app.models.runtime import EnterpriseRuntime, RuntimeVersion
from app.schemas.runtime import RuntimeCompileResult
from app.services.runtime import (
    bump_semver,
    get_active_runtime as query_get_active_runtime,
    get_runtime_by_version,
    list_versions,
    save_runtime,
    store_get_active_runtime,
)

from .conftest import make_compile_result, save_runtime_helper, seed_enterprise, seed_user


# ============================================================
# bump_semver 单元测试（纯函数，无 DB）
# ============================================================


class TestBumpSemver:
    """语义化版本号递增（PRD §4.6.3）。"""

    def test_first_version_returns_v1_0_0(self):
        assert bump_semver(None) == "v1.0.0"
        assert bump_semver("") == "v1.0.0"

    def test_patch_increment(self):
        assert bump_semver("v1.0.0", "patch") == "v1.0.1"
        assert bump_semver("v2.3.4", "patch") == "v2.3.5"

    def test_minor_increment(self):
        assert bump_semver("v1.0.0", "minor") == "v1.1.0"
        assert bump_semver("v1.1.0", "minor") == "v1.2.0"

    def test_major_increment(self):
        assert bump_semver("v1.0.0", "major") == "v2.0.0"
        assert bump_semver("v1.1.0", "major") == "v2.0.0"
        assert bump_semver("v9.9.9", "major") == "v10.0.0"

    def test_without_v_prefix(self):
        """无 'v' 前缀的版本号也应正确解析。"""
        assert bump_semver("1.2.3", "minor") == "v1.3.0"
        assert bump_semver("1.2.3", "major") == "v2.0.0"

    def test_default_change_type_is_minor(self):
        assert bump_semver("v1.0.0") == "v1.1.0"

    def test_unparseable_falls_back(self):
        """无法解析的版本号兜底追加 -next 保证唯一性。"""
        result = bump_semver("v1.0", "minor")
        assert result == "v1.0-next"


# ============================================================
# save_runtime 测试
# ============================================================


class TestSaveRuntime:
    """Runtime 保存与版本递增。"""

    async def test_save_first_runtime_gets_v1_0_0(self, db_session):
        """首次保存应得到 v1.0.0 并设为激活。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        runtime = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id
        )

        assert runtime.version == "v1.0.0"
        assert runtime.is_active is True
        assert runtime.enterprise_id == enterprise.id
        assert runtime.created_by == user.id
        assert runtime.completeness == 80.0
        assert runtime.runtime_data is not None
        assert runtime.runtime_data["agents"][0]["agent_id"] == "agent-0"

    async def test_save_activates_new_and_deactivates_old(self, db_session):
        """保存新版本时旧版本应置为 inactive，激活态唯一。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        v1 = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        v2 = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )

        assert v1.version == "v1.0.0"
        assert v2.version == "v1.1.0"
        assert v2.is_active is True

        # 重新查询 v1 确认已被置为 inactive
        await db_session.refresh(v1)
        assert v1.is_active is False

        # DB 中该企业只有一个激活版本
        active = await store_get_active_runtime(db_session, enterprise.id, use_cache=False)
        assert active is not None
        assert active.id == v2.id

    async def test_save_with_explicit_version(self, db_session):
        """显式指定版本号应被采用（自动补 'v' 前缀）。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        runtime = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="2.5.0"
        )
        assert runtime.version == "v2.5.0"
        assert runtime.is_active is True

    async def test_save_with_explicit_v_prefixed_version(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        runtime = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v3.0.0"
        )
        assert runtime.version == "v3.0.0"

    async def test_save_duplicate_version_raises(self, db_session):
        """重复版本号应抛出 ValueError('runtime_version_exists')。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v1.0.0"
        )
        with pytest.raises(ValueError, match="runtime_version_exists"):
            await save_runtime_helper(
                db_session, enterprise.id, created_by=user.id, version="v1.0.0"
            )

    async def test_save_change_types_produce_correct_versions(self, db_session):
        """patch/minor/major 三种变更类型应产生正确的版本号序列。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        v1 = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        assert v1.version == "v1.0.0"

        v2 = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="patch"
        )
        assert v2.version == "v1.0.1"

        v3 = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="minor"
        )
        assert v3.version == "v1.1.0"

        v4 = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, change_type="major"
        )
        assert v4.version == "v2.0.0"

    async def test_save_writes_audit_event(self, db_session):
        """保存应写入一条 event_type='save' 的审计事件。"""
        from sqlalchemy import select

        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        runtime = await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, changelog="首次编译"
        )

        result = await db_session.execute(
            select(RuntimeVersion).where(
                RuntimeVersion.runtime_id == runtime.id,
                RuntimeVersion.event_type == "save",
            )
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].changelog == "首次编译"
        assert events[0].created_by == user.id
        assert events[0].metadata_json["change_type"] == "minor"

    async def test_save_syncs_enterprise_current_runtime_version_id(self, db_session):
        """保存后 Enterprise.current_runtime_version_id 应指向新激活版本。"""
        from sqlalchemy import select

        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        runtime = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        await db_session.refresh(enterprise)
        assert enterprise.current_runtime_version_id == runtime.id

    async def test_save_persists_full_runtime_data_9_fields(self, db_session):
        """runtime_data 应包含 PRD §4.6.1 的 9 字段块。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        runtime = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)
        data = runtime.runtime_data

        # 9 字段块（organization/agents/process_engines/collaboration_graph/
        # knowledge_index/tool_registry + 顶层 model_version/compiled_at/completeness）
        for key in (
            "organization",
            "agents",
            "process_engines",
            "collaboration_graph",
            "knowledge_index",
            "tool_registry",
            "model_version",
            "compiled_at",
            "completeness",
        ):
            assert key in data, f"runtime_data 缺少字段 {key}"


# ============================================================
# get_active_runtime 测试
# ============================================================


class TestGetActiveRuntime:
    async def test_returns_none_when_no_runtime(self, db_session):
        enterprise = await seed_enterprise(db_session)
        active = await store_get_active_runtime(db_session, enterprise.id, use_cache=False)
        assert active is None

    async def test_returns_active_runtime(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        runtime = await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        active = await store_get_active_runtime(db_session, enterprise.id, use_cache=False)
        assert active is not None
        assert active.id == runtime.id
        assert active.is_active is True

    async def test_get_active_via_query_returns_compile_result(self, db_session):
        """query 层 get_active_runtime 应返回结构化 RuntimeCompileResult（验证无递归）。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        result = await query_get_active_runtime(db_session, enterprise.id)
        assert result is not None
        assert isinstance(result, RuntimeCompileResult)
        assert len(result.agents) == 2
        assert result.organization.departments[0].dept_id == "dept-0"


# ============================================================
# get_runtime_by_version 测试
# ============================================================


class TestGetRuntimeByVersion:
    async def test_with_v_prefix(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v1.2.3"
        )

        runtime = await get_runtime_by_version(db_session, enterprise.id, "v1.2.3")
        assert runtime is not None
        assert runtime.version == "v1.2.3"

    async def test_without_v_prefix_normalizes(self, db_session):
        """不带 'v' 前缀的版本号应自动补全匹配。"""
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)
        await save_runtime_helper(
            db_session, enterprise.id, created_by=user.id, version="v1.2.3"
        )

        runtime = await get_runtime_by_version(db_session, enterprise.id, "1.2.3")
        assert runtime is not None
        assert runtime.version == "v1.2.3"

    async def test_not_found_returns_none(self, db_session):
        enterprise = await seed_enterprise(db_session)
        runtime = await get_runtime_by_version(db_session, enterprise.id, "v9.9.9")
        assert runtime is None


# ============================================================
# list_versions 测试
# ============================================================


class TestListVersions:
    async def test_list_returns_all_versions_newest_first(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        for _ in range(3):
            await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        versions, total = await list_versions(db_session, enterprise.id, limit=20, offset=0)
        assert total == 3
        assert len(versions) == 3
        # 最新在前（compiled_at desc）；最后保存的 v1.2.0 应排首位
        assert versions[0].version == "v1.2.0"
        assert versions[-1].version == "v1.0.0"

    async def test_list_pagination(self, db_session):
        enterprise = await seed_enterprise(db_session)
        user = await seed_user(db_session, enterprise)

        for _ in range(5):
            await save_runtime_helper(db_session, enterprise.id, created_by=user.id)

        # 第一页 2 条
        page1, total = await list_versions(db_session, enterprise.id, limit=2, offset=0)
        assert total == 5
        assert len(page1) == 2

        # 第二页 2 条
        page2, _ = await list_versions(db_session, enterprise.id, limit=2, offset=2)
        assert len(page2) == 2

        # 第三页 1 条
        page3, _ = await list_versions(db_session, enterprise.id, limit=2, offset=4)
        assert len(page3) == 1

        # 页间无重叠
        page1_ids = {v.id for v in page1}
        page2_ids = {v.id for v in page2}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_list_empty_enterprise(self, db_session):
        enterprise = await seed_enterprise(db_session)
        versions, total = await list_versions(db_session, enterprise.id, limit=20, offset=0)
        assert total == 0
        assert versions == []
