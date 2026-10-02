"""WeCom XML callbacks through real, restricted PostgreSQL role connections.

Run after upgrade head with RLS_TEST_ADMIN_URL, RLS_TEST_APP_URL and
RLS_TEST_BOOTSTRAP_URL. Redis replay storage is deliberately local in this suite.
"""
import base64
import hashlib
import json
import os
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.test_channel_webhook_security import WECOM_AES_KEY, WECOM_TOKEN, _app_xml

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")
BOOTSTRAP_URL = os.getenv("RLS_TEST_BOOTSTRAP_URL", "")
pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL and BOOTSTRAP_URL),
    reason="Requires migrated PostgreSQL and restricted app/bootstrap credentials",
)


@pytest_asyncio.fixture
async def pg_callback(monkeypatch):
    from app import database
    from app.api import connectors
    from app.config import settings
    from app.main import app
    from app.services.connectors import signature
    from app.utils.credential_crypto import encrypt_credential
    from app.utils.db_tenant_context import authenticated_user_scope, tenant_scope

    admin = create_engine(ADMIN_URL)
    business = create_async_engine(APP_URL, poolclass=NullPool)
    bootstrap = create_async_engine(BOOTSTRAP_URL, poolclass=NullPool)
    event.listen(business.sync_engine, "begin", database._apply_tenant_context_on_begin)
    event.listen(business.sync_engine, "connect", lambda conn, record:
                 database.verify_postgres_runtime_role(conn, "autoteams_app"))
    event.listen(bootstrap.sync_engine, "connect", lambda conn, record:
                 database.verify_postgres_runtime_role(
                     conn, "autoteams_bootstrap", forbid_table_access=True,
                     require_execute=("public.app_get_channel_webhook_material(text)",)))
    factory = async_sessionmaker(business, expire_on_commit=False)
    monkeypatch.setattr(database, "bootstrap_session_factory",
                        async_sessionmaker(bootstrap, expire_on_commit=False))
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(signature, "security_redis_client", lambda: None)
    signature.reset_replay_guard()
    spy = AsyncMock(wraps=connectors._bind_webhook_tenant)
    monkeypatch.setattr(connectors, "_bind_webhook_tenant", spy)
    suffix = uuid.uuid4().hex[:12]
    accounts = [(f"wxpg-ent-{suffix}-{i}", f"wxpg-acc-{suffix}-{i}") for i in range(2)]
    credentials = json.dumps({key: encrypt_credential(value) for key, value in {
        "token": WECOM_TOKEN, "encoding_aes_key": WECOM_AES_KEY,
        "corp_id": "wxcorp_wxid",
    }.items()})
    with admin.begin() as conn:
        for enterprise_id, account_id in accounts:
            conn.execute(text("INSERT INTO enterprises (id, name, is_active, invite_max_uses, "
                              "invite_used_count) VALUES (:id, :id, true, 10, 0)"),
                         {"id": enterprise_id})
            conn.execute(text(
                "INSERT INTO channel_accounts (id, enterprise_id, channel_type, name, "
                "encrypted_credentials, mounted_profile_ids, status, is_active, "
                "created_at, updated_at, webhook_token_hash) VALUES "
                "(:id, :ent, 'wecom_app', :id, CAST(:credentials AS json), '[]', "
                "'configured', true, now(), now(), :secret_hash)"),
                {"id": account_id, "ent": enterprise_id, "credentials": credentials,
                 "secret_hash": hashlib.sha256(WECOM_TOKEN.encode()).hexdigest()})

    async def scoped_db():
        with tenant_scope(None), authenticated_user_scope(None):
            async with factory() as session:
                yield session

    prior = app.dependency_overrides.get(database.get_db)
    app.dependency_overrides[database.get_db] = scoped_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield client, accounts, admin, spy
    finally:
        if prior is None:
            app.dependency_overrides.pop(database.get_db, None)
        else:
            app.dependency_overrides[database.get_db] = prior
        await business.dispose()
        await bootstrap.dispose()
        signature.reset_replay_guard()
        with admin.begin() as conn:
            for enterprise_id, account_id in accounts:
                conn.execute(text("DELETE FROM channel_identities WHERE enterprise_id = :id"), {"id": enterprise_id})
                conn.execute(text("DELETE FROM channel_accounts WHERE id = :id"), {"id": account_id})
                conn.execute(text("DELETE FROM enterprises WHERE id = :id"), {"id": enterprise_id})
        admin.dispose()


@pytest.mark.asyncio
async def test_verified_xml_binds_before_real_gateway_and_isolates_accounts(pg_callback):
    from app.services.connectors.wecom_callback import _decrypt, parse_flat_xml

    client, accounts, admin, bind = pg_callback
    body, query = _app_xml(uuid.uuid4().hex, text="/帮助")
    first_path = f"/api/v1/connectors/wecom/webhook/{accounts[0][1]}"
    invalid = await client.post(first_path, content=body,
                                params={**query, "msg_signature": "0" * 40})
    assert invalid.status_code == 403, invalid.text
    bind.assert_not_awaited()
    for enterprise_id, account_id in accounts:
        response = await client.post(f"/api/v1/connectors/wecom/webhook/{account_id}",
                                     content=body, params=query)
        assert response.status_code == 200, response.text
        outer = parse_flat_xml(response.content)
        inner = parse_flat_xml(_decrypt(outer["Encrypt"], base64.b64decode(WECOM_AES_KEY + "="), "wxcorp_wxid"))
        assert inner["ToUserName"] == "app_user"
        assert inner["Content"]
        with admin.connect() as conn:
            rows = conn.execute(text("SELECT enterprise_id, channel_type FROM channel_identities "
                                     "WHERE enterprise_id = :id"), {"id": enterprise_id}).fetchall()
        assert rows == [(enterprise_id, "wecom_app")]
    assert bind.await_count == 2
    replay = await client.post(first_path, content=body, params=query)
    assert replay.status_code == 403, replay.text
    assert bind.await_count == 2


@pytest.mark.asyncio
async def test_missing_bootstrap_fails_closed_before_binding(pg_callback, monkeypatch):
    from app import database

    client, accounts, _, bind = pg_callback
    monkeypatch.setattr(database, "bootstrap_session_factory", None)
    body, query = _app_xml(uuid.uuid4().hex)
    response = await client.post(f"/api/v1/connectors/wecom/webhook/{accounts[0][1]}",
                                 content=body, params=query)
    assert response.status_code == 503, response.text
    bind.assert_not_awaited()


@pytest.mark.asyncio
async def test_url_verification_never_binds_business_session(pg_callback):
    from app.services.connectors.wecom_callback import parse_flat_xml

    client, accounts, admin, bind = pg_callback
    body, query = _app_xml(uuid.uuid4().hex)
    response = await client.get(f"/api/v1/connectors/wecom/webhook/{accounts[0][1]}",
                                params={**query, "echostr": parse_flat_xml(body)["Encrypt"]})
    assert response.status_code == 200, response.text
    assert parse_flat_xml(response.content)["FromUserName"] == "app_user"
    bind.assert_not_awaited()
    with admin.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM channel_identities WHERE enterprise_id = :id"),
                             {"id": accounts[0][0]}).scalar_one()
    assert count == 0
