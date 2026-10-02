import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.pool import NullPool
from app.config import settings
from app.database import Base
from app.services import runner_session
from app.services.runner_session import (
    register_grant, claim_grant, mark_connected, mark_offline, revoke_grant, _now,
)
from app.models.local_path_grant import LocalPathGrant


@pytest.mark.asyncio
async def test_runner_token_ttl_is_10_minutes(db_session):
    grant, token, expires_at = await register_grant(
        db=db_session,
        enterprise_id="ent-test-sec",
        user_id="usr-test-sec",
        local_path="D:/Test",
        scope="read_write",
        label="Sec Test",
    )
    # 验证有效期在 10 分钟左右（而非 30 天）
    delta = expires_at - _now()
    assert 540 <= delta.total_seconds() <= 660, f"Expected ~600s TTL, got {delta.total_seconds()}s"


@pytest.mark.asyncio
async def test_runner_token_single_use_replay_protection(db_session):
    grant, token, expires_at = await register_grant(
        db=db_session,
        enterprise_id="ent-test-sec",
        user_id="usr-test-sec",
        local_path="D:/Test",
        scope="read_write",
        label="Replay Test",
    )

    # 第一次认领必须成功
    ok1 = await claim_grant(db_session, grant, token)
    assert ok1 is True
    assert grant.claimed is True
    assert grant.setup_token_hash is None

    # 第二次尝试使用同一 token 重放认领，必须被拦截并返回 False
    ok2 = await claim_grant(db_session, grant, token)
    assert ok2 is False


@pytest.mark.asyncio
async def test_runner_token_cannot_be_claimed_from_stale_session(db_session, test_engine):
    grant, token, _ = await register_grant(
        db=db_session,
        enterprise_id="ent-test-sec",
        user_id="usr-test-sec",
        local_path="D:/Test",
        scope="read",
        label="Concurrent claim test",
    )

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as stale_session:
        stale_grant = await stale_session.get(LocalPathGrant, grant.id)
        assert stale_grant is not None
        assert stale_grant.claimed is False

        assert await claim_grant(db_session, grant, token) is True
        assert await claim_grant(stale_session, stale_grant, token) is False

    await db_session.refresh(grant)
    assert grant.claimed is True
    assert grant.setup_token_hash is None


@pytest.mark.asyncio
async def test_stale_connected_update_cannot_restore_revoked_grant(db_session, test_engine):
    grant, _, _ = await register_grant(
        db_session, "ent-test-sec", "usr-test-sec", "D:/Test", "read", "Stale connection"
    )
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as stale_session:
        stale_grant = await stale_session.get(LocalPathGrant, grant.id)
        assert stale_grant.status == "pending"

        await revoke_grant(db_session, grant)
        assert await mark_connected(
            stale_session, stale_grant, "runner-1", "D:/Test", {"list": True}
        ) is False

    await db_session.refresh(grant)
    assert grant.status == "revoked"
    assert grant.runner_id is None
    assert grant.resolved_path is None
    assert grant.tool_manifest is None


@pytest.mark.asyncio
async def test_stale_offline_update_cannot_replace_revoked_grant(db_session, test_engine):
    grant, _, _ = await register_grant(
        db_session, "ent-test-sec", "usr-test-sec", "D:/Test", "read", "Stale disconnect"
    )
    assert await mark_connected(db_session, grant, "runner-1", "D:/Test", None)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as stale_session:
        stale_grant = await stale_session.get(LocalPathGrant, grant.id)
        assert stale_grant.status == "connected"

        await revoke_grant(db_session, grant)
        await mark_offline(stale_session, stale_grant)

    await db_session.refresh(grant)
    assert grant.status == "revoked"


@pytest.mark.asyncio
async def test_connected_update_keeps_loaded_grant_in_sync(db_session):
    grant, _, _ = await register_grant(
        db_session, "ent-test-sec", "usr-test-sec", "D:/Test", "read", "ORM sync"
    )

    assert await mark_connected(
        db_session, grant, "runner-1", "D:/Resolved", {"list": True}
    ) is True
    assert grant.status == "connected"
    assert grant.runner_id == "runner-1"
    assert grant.resolved_path == "D:/Resolved"
    assert grant.tool_manifest == {"list": True}


@pytest.mark.asyncio
async def test_connected_api_rejects_revoke_after_initial_read(
    client, db_session, test_engine, monkeypatch,
):
    grant, _, _ = await register_grant(
        db_session, "ent-test-sec", "usr-test-sec", "D:/Test", "read", "API race"
    )
    monkeypatch.setattr(settings, "BRIDGE_INTERNAL_SECRET", "test-bridge-secret")
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def revoke_before_status_update(db, loaded_grant, runner_id, resolved_path, manifest):
        # API 已读取 pending；在服务层条件 UPDATE 前由另一个会话完成撤销。
        assert loaded_grant.status == "pending"
        async with factory() as revoke_session:
            current = await revoke_session.get(LocalPathGrant, loaded_grant.id)
            await revoke_grant(revoke_session, current)
        return await mark_connected(db, loaded_grant, runner_id, resolved_path, manifest)

    monkeypatch.setattr(runner_session, "mark_connected", revoke_before_status_update)
    response = await client.post(
        f"/api/v1/local-paths/{grant.id}/connected",
        headers={"X-Bridge-Secret": "test-bridge-secret"},
        json={"runner_id": "runner-1", "resolved_path": "D:/Test"},
    )
    assert response.status_code == 403, response.text
    await db_session.refresh(grant)
    assert grant.status == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("late_event", ["connected", "offline"])
async def test_file_sqlite_stale_status_cannot_replace_revoke(tmp_path, late_event):
    # 文件 SQLite + NullPool：两个会话使用各自的数据库连接。
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'runner_grants.db'}", poolclass=NullPool
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as owner, factory() as stale:
            grant, _, _ = await register_grant(
                owner, "ent-test-sec", "usr-test-sec", "D:/Test", "read", late_event
            )
            if late_event == "offline":
                assert await mark_connected(owner, grant, "runner-1", "D:/Test", None)
            stale_grant = await stale.get(LocalPathGrant, grant.id)
            assert stale_grant.status == (
                "connected" if late_event == "offline" else "pending"
            )

            await revoke_grant(owner, grant)
            if late_event == "connected":
                assert await mark_connected(
                    stale, stale_grant, "runner-2", "D:/Other", None
                ) is False
            else:
                await mark_offline(stale, stale_grant)

        async with factory() as reader:
            current = await reader.get(LocalPathGrant, grant.id)
            assert current.status == "revoked"
            assert current.runner_id == (
                "runner-1" if late_event == "offline" else None
            )
    finally:
        await engine.dispose()
