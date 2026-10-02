import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.audit_chain_state import AuditChainState
from app.models.audit_log import AuditLog
from app.utils.audit import (
    AUDIT_CHAIN_STATE_ID,
    log_audit,
    verify_audit_chain,
)


async def _make_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(AuditLog.__table__.create)
        await connection.run_sync(AuditChainState.__table__.create)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_audit_chain_state_advances_with_each_committed_audit_event():
    engine, factory = await _make_session_factory()
    user = SimpleNamespace(id="user-audit-chain")
    try:
        async with factory() as session:
            await log_audit(session, user, "first", "agent", "agent-1")
            await session.commit()
        async with factory() as session:
            await log_audit(session, user, "second", "agent", "agent-2")
            await session.commit()

        async with factory() as session:
            logs = (await session.execute(select(AuditLog).order_by(AuditLog.created_at.asc()))).scalars().all()
            state = await session.get(AuditChainState, AUDIT_CHAIN_STATE_ID)
            result = await verify_audit_chain(session)

        assert len(logs) == 2
        assert logs[1].prev_hash == logs[0].signature
        assert state is not None
        assert state.last_signature == logs[-1].signature
        assert result["valid"] is True
        assert result["checked"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_state_row_recovers_from_existing_signed_chain_tail():
    engine, factory = await _make_session_factory()
    user = SimpleNamespace(id="user-audit-recovery")
    try:
        async with factory() as session:
            await log_audit(session, user, "first", "agent", "agent-1")
            await session.commit()

        async with factory() as session:
            first_log = (await session.execute(select(AuditLog))).scalar_one()
            await session.execute(delete(AuditChainState))
            await session.commit()

        async with factory() as session:
            await log_audit(session, user, "second", "agent", "agent-2")
            await session.commit()

        async with factory() as session:
            logs = (await session.execute(select(AuditLog).order_by(AuditLog.created_at.asc()))).scalars().all()
            result = await verify_audit_chain(session)

        assert logs[1].prev_hash == first_log.signature
        assert result["valid"] is True
    finally:
        await engine.dispose()
