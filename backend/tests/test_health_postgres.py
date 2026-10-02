"""健康检查在**真实迁移过的 PostgreSQL** 上的验收（AUD-29）。

SQLite 夹具是 `create_all` + stamp，只能证明代码路径可跑；迁移版本比对只有在
**真的跑过 `alembic upgrade head` 的库**上才有意义。因此本文件要求专用验收库，
并断言：

* 迁移到 head 的库，schema 探测返回 up，且 revision 与解析出的 head 一致；
* 同一套探测对"落后一个 revision"的库返回 down。

没有 RLS_TEST_ADMIN_URL 时整体跳过。
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL), reason="需要专用 PostgreSQL 验收库（RLS_TEST_*_URL）"
)

_HEAD = "e7f8a9b0c1d2"
_PREVIOUS_HEAD = "c2d3e4f5a6b8"


async def _probe_schema(engine):
    from app.utils import health

    original = health.engine
    health.engine = engine
    try:
        return await health._check_schema()
    finally:
        health.engine = original


@pytest.mark.asyncio
async def test_migrated_database_reports_schema_up():
    engine = create_async_engine(APP_URL)
    try:
        status = await _probe_schema(engine)
        assert status.status == "up", status.message
        assert _HEAD in (status.message or "")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_behind_head_reports_schema_down():
    """版本落后的库必须判 down —— 只查"表在不在"会放过漏跑的迁移。"""
    from app.utils import health

    admin = create_engine(ADMIN_URL)
    engine = create_async_engine(APP_URL)
    try:
        with admin.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :rev"),
                         {"rev": _PREVIOUS_HEAD})
        status = await _probe_schema(engine)
        assert status.status == "down", status.message
        assert _PREVIOUS_HEAD in (status.message or "")
    finally:
        with admin.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :rev"),
                         {"rev": _HEAD})
        await engine.dispose()
        admin.dispose()


@pytest.mark.asyncio
async def test_unknown_revision_reports_schema_down():
    """库里出现脚本目录里不存在的 revision（手工 stamp / 旧分支）同样判 down。"""
    from app.utils import health

    admin = create_engine(ADMIN_URL)
    engine = create_async_engine(APP_URL)
    try:
        with admin.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = 'no-such-revision'"))
        status = await _probe_schema(engine)
        assert status.status == "down", status.message
    finally:
        with admin.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :rev"),
                         {"rev": _HEAD})
        await engine.dispose()
        admin.dispose()


@pytest.mark.asyncio
async def test_multiple_stamped_heads_report_schema_down():
    """多个 head：stamped 集合与 head 集合不一致时判 down（分叉库不能接流量）。"""
    from app.utils import health

    admin = create_engine(ADMIN_URL)
    engine = create_async_engine(APP_URL)
    try:
        with admin.begin() as conn:
            conn.execute(text(
                "INSERT INTO alembic_version (version_num) VALUES (:other)"
            ), {"other": _PREVIOUS_HEAD})
        status = await _probe_schema(engine)
        assert status.status == "down", status.message
    finally:
        with admin.begin() as conn:
            conn.execute(
                text("DELETE FROM alembic_version WHERE version_num = :other"),
                {"other": _PREVIOUS_HEAD},
            )
        await engine.dispose()
        admin.dispose()


@pytest.mark.asyncio
async def test_multiple_script_heads_are_accepted_when_stamped():
    """迁移脚本真的出现多个 head 且库也 stamp 了这两个：不应误判。"""
    from app.utils import health

    admin = create_engine(ADMIN_URL)
    engine = create_async_engine(APP_URL)
    original = health._ALEMBIC_HEADS
    try:
        with admin.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :rev"),
                         {"rev": _HEAD})
        health._ALEMBIC_HEADS = frozenset({_HEAD, "another-head"})
        status = await _probe_schema(engine)
        assert status.status == "down", status.message  # 只 stamp 了一个
        with admin.begin() as conn:
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('another-head')"))
        status = await _probe_schema(engine)
        assert status.status == "up", status.message
    finally:
        health._ALEMBIC_HEADS = original
        with admin.begin() as conn:
            conn.execute(
                text("DELETE FROM alembic_version WHERE version_num = 'another-head'")
            )
        await engine.dispose()
        admin.dispose()
