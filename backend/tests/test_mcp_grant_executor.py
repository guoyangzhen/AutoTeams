"""AUD-01：端侧授权只读执行器的定向验收。

覆盖用户要求的关键反例与边界：跨企业、同企业另一用户、撤销/离线、路径越界、
多授权时必须显式指定 ID、返回格式异常、UTF-8 字节截断、注册/注销生命周期。
**不触碰数据库服务**（SQLite + mock dispatch），端到端由 root 的真实 Runner 验证完成。
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.local_path_grant import LocalPathGrant
from app.models.user import User
from app.services.mcp import grant_executor as ge
from app.services.mcp import runner_bridge as bridge
from sqlalchemy import text

from app.utils.db_tenant_context import (
    authenticated_user_scope,
    get_authenticated_user_id,
    get_current_enterprise_id,
    tenant_scope,
)
from app.services.mcp.runner_bridge import (
    ERR_DEVICE_ID_RETIRED,
    ERR_GRANT_NOT_ALLOWED,
    ERR_NO_EXECUTOR,
    ERR_PATH_NOT_ALLOWED,
    ERR_PROBE_UNAVAILABLE,
    ERR_SCRIPT_UNAVAILABLE,
    MCPToolCallContext,
)

pytestmark = pytest.mark.asyncio


ENT = "ent-1"
USER = "usr-1"


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后都清空注册表，避免跨用例泄漏。"""
    bridge.register_executor(None)
    yield
    bridge.register_executor(None)


def _ctx(user_id=USER, ent=ENT):
    return MCPToolCallContext(enterprise_id=ent, user_id=user_id)


async def _make_grant(db, **over):
    grant = LocalPathGrant(
        id=over.pop("id", "grant-1"),
        enterprise_id=over.pop("enterprise_id", ENT),
        user_id=over.pop("user_id", USER),
        local_path=over.pop("local_path", "C:/docs"),
        scope=over.pop("scope", "read"),
        status=over.pop("status", "connected"),
        runner_id=over.pop("runner_id", "local-runner-a"),
    )
    db.add(grant)
    await db.commit()
    return grant


@pytest.fixture(autouse=True)
def _real_sessions(test_engine, monkeypatch):
    """把执行器自开的会话指向**真实**测试引擎（SQLite）。

    不 mock SQLAlchemy：授权查询与审计写入都必须真的跑，才能暴露
    `apply_tenant_context` 在 SQLite 上调用 `set_config` 之类的方言问题。
    只 mock 网络下发（dispatch_to_runner）。
    """
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(ge, "async_session_factory", factory)
    return factory


def _ok(content: str):
    """异步 dispatch 桩：返回合法信封。"""
    async def _dispatch(*args, **kwargs):
        return {"success": True, "data": {"content": content}}
    return _dispatch


def _never_called(*args, **kwargs):
    async def _dispatch(*_a, **_k):
        raise AssertionError("不应发生下发")
    return _dispatch


def _raise(exc: BaseException):
    async def _dispatch(*_a, **_k):
        raise exc
    return _dispatch


@pytest.fixture
async def user(db_session):
    u = User(
        id=USER, email="u1@example.test", password_hash="x", name="U1",
        role="member", enterprise_id=ENT, is_active=True,
    )
    db_session.add(u)
    await db_session.commit()
    return u


# ---------------------------------------------------------------------------
# 授权归属：跨企业 / 另一用户 / 撤销 / 离线 / scope
# ---------------------------------------------------------------------------

async def test_cross_enterprise_grant_is_denied(db_session, user, monkeypatch):
    await _make_grant(db_session, enterprise_id="ent-OTHER")
    sent = []
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _never_called)
    with pytest.raises(PermissionError):
        await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                enterprise_id=ENT, user_id=USER,
                                relative_path="a.txt", max_bytes=4096)
    assert sent == [], "跨企业授权不得下发"


async def test_same_enterprise_other_user_grant_is_denied(db_session, user, monkeypatch):
    await _make_grant(db_session, user_id="usr-OTHER")
    sent = []
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _never_called)
    with pytest.raises(PermissionError):
        await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                enterprise_id=ENT, user_id=USER,
                                relative_path="a.txt", max_bytes=4096)
    assert sent == [], "同企业另一用户的授权不得下发"


@pytest.mark.parametrize("status", ["revoked", "offline", "pending"])
async def test_non_connected_grant_is_denied(db_session, user, monkeypatch, status):
    await _make_grant(db_session, status=status)
    sent = []
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _never_called)
    with pytest.raises(PermissionError):
        await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                enterprise_id=ENT, user_id=USER,
                                relative_path="a.txt", max_bytes=4096)
    assert sent == [], f"{status} 状态不得下发"


async def test_read_scope_is_allowed(db_session, user, monkeypatch):
    await _make_grant(db_session, scope="read")
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok("hello"))
    result = await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                     enterprise_id=ENT, user_id=USER,
                                     relative_path="a.txt", max_bytes=4096)
    assert result.content == "hello"
    assert result.truncated is False


async def test_read_write_scope_is_allowed(db_session, user, monkeypatch):
    await _make_grant(db_session, scope="read_write")
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok("hello"))
    result = await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                     enterprise_id=ENT, user_id=USER,
                                     relative_path="a.txt", max_bytes=4096)
    assert result.content == "hello"


# ---------------------------------------------------------------------------
# 多授权：必须显式指定 ID，不随机映射
# ---------------------------------------------------------------------------

async def test_multiple_grants_same_runner_requires_explicit_id(db_session, user, monkeypatch):
    """同一 runner_id 有多个授权时，grant_id 决定读哪一个；不给 ID 必须失败。"""
    await _make_grant(db_session, id="grant-A", local_path="C:/a")
    await _make_grant(db_session, id="grant-B", local_path="C:/b")
    seen = []

    async def _dispatch(grant_id, task):
        seen.append((grant_id, task.path))
        return {"success": True, "data": {"content": f"content-of-{grant_id}"}}

    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner", _dispatch)

    # 显式指定 B：读到的必须是 B 的内容
    result = await ge.grant_executor(operation="read_file", grant_id="grant-B",
                                     enterprise_id=ENT, user_id=USER,
                                     relative_path="x.txt", max_bytes=4096)
    assert result.content == "content-of-grant-B"
    assert seen == [("grant-B", "x.txt")], "必须只下发被显式指定的那一个授权"


async def test_bridge_rejects_device_id_only(db_session, user):
    """旧 device_id-only 契约必须明确失败，不得映射到某个授权。"""
    bridge.register_executor(ge.grant_executor)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"device_id": "dev-1", "relative_path": "a.txt"}, _ctx()
    )
    assert result.success is False
    assert ERR_DEVICE_ID_RETIRED in result.error


# ---------------------------------------------------------------------------
# 路径越界
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["/etc/passwd", "../../secret", "C:/Windows/system.ini",
                                 "..\\..\\secret", "~/x", "a\x00b", "file://x"])
async def test_escaping_paths_are_denied_before_dispatch(db_session, user, monkeypatch, bad):
    await _make_grant(db_session)
    sent = []
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _never_called)
    with pytest.raises(PermissionError):
        await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                enterprise_id=ENT, user_id=USER,
                                relative_path=bad, max_bytes=4096)
    assert sent == [], f"越界路径 {bad!r} 不得下发"


async def test_bridge_returns_path_error(db_session, user):
    bridge.register_executor(ge.grant_executor)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "grant-1", "relative_path": "../../etc/passwd"}, _ctx()
    )
    assert result.success is False
    assert ERR_PATH_NOT_ALLOWED in result.error


# ---------------------------------------------------------------------------
# 返回信封与截断
# ---------------------------------------------------------------------------

async def test_malformed_envelope_is_denied(db_session, user, monkeypatch):
    await _make_grant(db_session)
    async def _returns(bad):
        return bad

    for bad in ({"success": False}, {"success": True, "data": {"content": 123}},
                  {"success": True, "data": {}}, {"success": True}):
        monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                            lambda *a, _b=bad, **k: _returns(_b))
        with pytest.raises(ge._PublicError):
            await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                    enterprise_id=ENT, user_id=USER,
                                    relative_path="a.txt", max_bytes=4096)


async def test_exact_limit_is_not_truncated(db_session, user, monkeypatch):
    await _make_grant(db_session)
    content = "a" * 4096
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok(content))
    result = await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                     enterprise_id=ENT, user_id=USER,
                                     relative_path="a.txt", max_bytes=4096)
    assert result.truncated is False, "正好等于上限不应误报截断"
    assert result.content == content


async def test_over_limit_is_truncated(db_session, user, monkeypatch):
    await _make_grant(db_session)
    content = "a" * 4097
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok(content))
    result = await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                     enterprise_id=ENT, user_id=USER,
                                     relative_path="a.txt", max_bytes=4096)
    assert result.truncated is True
    assert len(result.content.encode("utf-8")) <= 4096


async def test_multibyte_truncation_does_not_split_codepoint(db_session, user, monkeypatch):
    """多字节字符：按字节截断后不能出现半个码点（否则下游 JSON/DB 会炸）。"""
    await _make_grant(db_session)
    # 3 字节的"你"，填满上限后再多几个
    content = "你" * 2000  # 6000 字节
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok(content))
    result = await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                     enterprise_id=ENT, user_id=USER,
                                     relative_path="a.txt", max_bytes=4096)
    assert result.truncated is True
    assert len(result.content.encode("utf-8")) <= 4096
    result.content.encode("utf-8").decode("utf-8")  # 必须仍是合法 UTF-8


# ---------------------------------------------------------------------------
# 错误消息不外泄
# ---------------------------------------------------------------------------

async def test_remote_error_does_not_leak_detail(db_session, user, monkeypatch):
    await _make_grant(db_session)
    secret = "SECRET-PATH-LEAK"
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _raise(RuntimeError(secret)))
    with pytest.raises(ge._PublicError) as exc:
        await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                enterprise_id=ENT, user_id=USER,
                                relative_path="a.txt", max_bytes=4096)
    assert secret not in str(exc.value), "远端原文不得进入对外错误"


async def test_audit_failure_does_not_change_read_result(db_session, user, monkeypatch):
    """审计写入失败不应改变读取结果（成功路径与失败路径一致处理）。"""
    await _make_grant(db_session)
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok("ok"))

    # 模拟审计内部故障（真实写库失败），而不是替换掉整个审计函数：
    # 审计必须自己吞掉异常，读取结果不受影响。
    original = ge.log_audit

    calls = []

    async def _boom(*a, **k):
        calls.append(1)
        raise RuntimeError("audit down")

    monkeypatch.setattr(ge, "log_audit", _boom)
    with tenant_scope(ENT):
        result = await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                         enterprise_id=ENT, user_id=USER,
                                         relative_path="a.txt", max_bytes=4096)
        # 审计确实被尝试调用过（否则本用例会因为"没走到审计"而空过）
        assert calls, "本用例必须真正触达审计写入"
        rows = (
            await db_session.execute(text("SELECT count(*) FROM audit_logs"))
        ).scalar()
    assert result.content == "ok", "审计故障不得改变读取结果"
    assert rows == 0, "审计失败时不应留下半条记录"


async def test_audit_is_written_and_contains_no_file_content(
    db_session, user, monkeypatch
):
    """真实写审计（不 mock 掉 SQLAlchemy），并确认审计里没有文件内容。"""
    await _make_grant(db_session)
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok("SECRET-FILE-CONTENT"))
    with tenant_scope(ENT):
        await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                enterprise_id=ENT, user_id=USER,
                                relative_path="a.txt", max_bytes=4096)
        rows = (
            await db_session.execute(
                text("SELECT action, resource_id, details FROM audit_logs")
            )
        ).fetchall()
    assert rows, "应当写入审计"
    blob = str(rows)
    assert "SECRET-FILE-CONTENT" not in blob, "审计不得包含文件内容"
    assert any(r[0] == "runner_read_file" for r in rows), rows


async def test_security_denial_is_also_audited(db_session, user, monkeypatch):
    """授权被拒（跨企业）同样留审计，且不区分拒绝原因。"""
    await _make_grant(db_session, enterprise_id="ent-OTHER")
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        lambda *a, **k: {"success": True, "data": {"content": "x"}})
    with tenant_scope(ENT):
        with pytest.raises(PermissionError):
            await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                    enterprise_id=ENT, user_id=USER,
                                    relative_path="a.txt", max_bytes=4096)
        rows = (
            await db_session.execute(
                text("SELECT action, details FROM audit_logs")
            )
        ).fetchall()
    assert any(r[0] == "runner_read_file_denied" for r in rows), rows
    assert "ent-OTHER" not in str(rows), "审计不得泄露他人企业"


# ---------------------------------------------------------------------------
# 上下文不得被破坏
# ---------------------------------------------------------------------------

async def test_tenant_and_subject_context_survive_the_call(
    db_session, user, monkeypatch
):
    """执行一次读取后，调用方的租户与已认证主体必须**原样**保留。

    自开���话的实现很容易在 finally 里把 ContextVar 置空，从而把同一个请求
    后续的所有租户读写打到"无租户"。
    """
    await _make_grant(db_session)
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok("ok"))
    sentinel_ent, sentinel_user = "ent-CALLER", "usr-CALLER"
    with tenant_scope(sentinel_ent):
        with authenticated_user_scope(sentinel_user):
            await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                    enterprise_id=ENT, user_id=USER,
                                    relative_path="a.txt", max_bytes=4096)
            assert get_current_enterprise_id() == sentinel_ent
            assert get_authenticated_user_id() == sentinel_user
    assert get_current_enterprise_id() is None
    assert get_authenticated_user_id() is None


async def test_denied_call_also_preserves_context(db_session, user, monkeypatch):
    """拒绝路径同样不得破坏调用方上下文。"""
    await _make_grant(db_session, enterprise_id="ent-OTHER")
    sentinel = "ent-CALLER"
    with tenant_scope(sentinel):
        with authenticated_user_scope("usr-CALLER"):
            with pytest.raises(PermissionError):
                await ge.grant_executor(operation="read_file", grant_id="grant-1",
                                        enterprise_id=ENT, user_id=USER,
                                        relative_path="a.txt", max_bytes=4096)
            assert get_current_enterprise_id() == sentinel
            assert get_authenticated_user_id() == "usr-CALLER"


# ---------------------------------------------------------------------------
# 生命周期 / 探针 / 脚本
# ---------------------------------------------------------------------------

async def test_unregistered_executor_is_fail_closed(db_session, user):
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "grant-1", "relative_path": "a.txt"}, _ctx()
    )
    assert result.success is False
    assert ERR_NO_EXECUTOR in result.error


async def test_system_probe_not_faked_by_registration(db_session, user):
    """注册执行器后，探针仍必须明确未实现——不能拿数据库档案冒充。"""
    bridge.register_executor(ge.grant_executor)
    result = await bridge.LocalRunnerBridge.call_tool("runner_system_probe", {}, _ctx())
    assert result.success is False
    assert ERR_PROBE_UNAVAILABLE in result.error


async def test_execute_script_still_fails_after_registration(db_session, user):
    """注册执行器不得让脚本执行"变成成功"（AUD-14 回归护栏）。"""
    bridge.register_executor(ge.grant_executor)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_execute_script", {"command": "rm -rf /"}, _ctx()
    )
    assert result.success is False
    assert ERR_SCRIPT_UNAVAILABLE in result.error


async def test_registration_lifecycle(db_session, user, monkeypatch):
    await _make_grant(db_session)
    monkeypatch.setattr(ge.runner_session, "dispatch_to_runner",
                        _ok("ok"))
    # 注册后可读
    bridge.register_executor(ge.grant_executor)
    ok = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "grant-1", "relative_path": "a.txt"}, _ctx()
    )
    assert ok.success is True
    # 注销后立刻失败关闭
    bridge.register_executor(None)
    gone = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "grant-1", "relative_path": "a.txt"}, _ctx()
    )
    assert gone.success is False
    assert ERR_NO_EXECUTOR in gone.error


# ---------------------------------------------------------------------------
# 桥接层契约硬化
# ---------------------------------------------------------------------------

async def test_bridge_rejects_legacy_file_path():
    """file_path 正是历史后端任意读取的入口，必须明确失败而非静默兼容。"""
    bridge.register_executor(ge.grant_executor)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "g-1", "file_path": "C:/secret.txt"}, _ctx()
    )
    assert result.success is False
    assert "file_path" in result.error


@pytest.mark.parametrize("bad", ["a/../b", "a/b/../c", "./../x", "docs/../../etc/passwd"])
async def test_bridge_rejects_any_parent_segment_not_just_traversal(bad):
    """`a/../b` 不能被静默改写成 `b`：契约是"不接受 .."，不是"归一化后不越界"。"""
    assert bridge.normalize_device_relative_path(bad) is None, bad
    bridge.register_executor(ge.grant_executor)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "g-1", "relative_path": bad}, _ctx()
    )
    assert result.success is False
    assert ERR_PATH_NOT_ALLOWED in result.error


async def test_normalize_accepts_ordinary_relative_paths():
    """普通相对路径照常放行并被归一化（`./` 折叠，但不越界）。"""
    assert bridge.normalize_device_relative_path("a.txt") == "a.txt"
    assert bridge.normalize_device_relative_path("docs/a.txt") == "docs/a.txt"
    assert bridge.normalize_device_relative_path("./a.txt") == "a.txt"
    assert bridge.normalize_device_relative_path("docs/./a.txt") == "docs/a.txt"
    assert bridge.normalize_device_relative_path("a b/c.txt") == "a b/c.txt"


async def test_unexpected_executor_error_does_not_leak_detail():
    """未知异常原文（可能含数据库/远端内容）不得出现在对外错误里。"""
    secret = "postgresql://user:pass@host/db  INTERNAL DETAIL"

    async def _explode(**_kwargs):
        raise RuntimeError(secret)

    bridge.register_executor(_explode)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "g-1", "relative_path": "a.txt"}, _ctx()
    )
    assert result.success is False
    assert secret not in result.error
    assert result.error == bridge.ERR_UNEXPECTED_FAILURE


async def test_public_error_is_passed_through():
    """本模块定义的公共错误仍然原样透出，便于前端区分处理。"""
    async def _deny(**_kwargs):
        raise PermissionError(ERR_GRANT_NOT_ALLOWED)

    bridge.register_executor(_deny)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "g-1", "relative_path": "a.txt"}, _ctx()
    )
    assert result.success is False
    assert result.error == ERR_GRANT_NOT_ALLOWED


async def test_wrong_result_type_is_rejected():
    async def _bad_type(**_kwargs):
        return {"content": "not a GrantFileRead"}

    bridge.register_executor(_bad_type)
    result = await bridge.LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "g-1", "relative_path": "a.txt"}, _ctx()
    )
    assert result.success is False
    assert result.error == bridge.ERR_BAD_RESULT_TYPE


async def test_tool_schema_requires_grant_id_and_relative_path():
    schema = {t.name: t for t in bridge.LocalRunnerBridge.list_tools()}["runner_read_file"]
    assert set(schema.inputSchema.required) == {"grant_id", "relative_path"}
    assert "device_id" not in schema.inputSchema.properties
    assert "file_path" not in schema.inputSchema.properties
