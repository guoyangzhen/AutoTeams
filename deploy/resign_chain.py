import asyncio, sys
sys.path.insert(0, '/app')
from sqlalchemy import select
from app.database import async_session_factory
from app.models.audit_log import AuditLog
from app.utils.audit import GENESIS_HASH, _compute_signature, verify_audit_chain

async def resign():
    async with async_session_factory() as db:
        result = await db.execute(select(AuditLog).order_by(AuditLog.created_at.asc()))
        logs = result.scalars().all()
        print("total logs:", len(logs))
        expected_prev = GENESIS_HASH
        resigned = 0
        for log in logs:
            if log.signature is None:
                continue
            log.prev_hash = expected_prev
            log.signature = _compute_signature(log)
            expected_prev = log.signature
            resigned += 1
        await db.commit()
        print("resigned:", resigned)
        check = await verify_audit_chain(db, limit=0)
        print("verify:", check)

asyncio.run(resign())
