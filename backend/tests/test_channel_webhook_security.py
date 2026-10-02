"""渠道回调可信来源与绑定码安全性测试（AUD-08）。

复现来源：`docs/TECHNICAL_AUDIT_VALIDATION_2026-09-28.md` §5.5 —— 无签名回调
伪造 `sender` 并发送 `/bind ABC123`，数据库 `is_bound=true` 但
`internal_user_id=null`。

本测试用真实 FastAPI 应用 + ASGITransport，只替换 `get_db`（不替换认证），
webhook 端点本身没有任何用户凭据，验签完全依赖平台协议。
"""
import base64
import hashlib
import json
import os
import struct
import time
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import get_db
from app.main import app
from app.models.channel_account import ChannelAccount, ChannelIdentity
from app.models.enterprise import Enterprise
from app.models.user import User
from app.services.connectors.signature import (
    ChannelSignatureError,
    NonceReplayGuard,
    REPLAY_REDIS_PREFIX,
    reset_replay_guard,
    verify_feishu_event,
    verify_wecom_plaintext_message,
    wecom_signature,
)
from app.utils.credential_crypto import encrypt_credential
from app.utils.security import get_current_user
from app.utils.token_blacklist import TokenRevocationStoreUnavailable

ENTERPRISE_ID = "webhook-enterprise"
ENCRYPT_KEY = "feishu-encrypt-key-for-tests"
WECOM_TOKEN = "wecom-callback-token-for-tests"
WECOM_AES_KEY = base64.b64encode(bytes(range(32))).decode()[:43]


def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad = block_size - len(data) % block_size
    return data + bytes([pad]) * pad


def _wecom_encrypt(message: dict, encoding_aes_key: str, receive_id: str = "wxcorp_wxid") -> str:
    """按 WXBizMsgCrypt 构造加密回调体。"""
    aes_key = base64.b64decode(encoding_aes_key + "=")
    msg = json.dumps(message, ensure_ascii=False).encode("utf-8")
    plain = b"\x00" * 16 + struct.pack("!I", len(msg)) + msg + receive_id.encode("utf-8")
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    enc = cipher.encryptor()
    return base64.b64encode(enc.update(_pkcs7_pad(plain)) + enc.finalize()).decode()


def _feishu_headers(body: bytes, encrypt_key: str, *, ts: int | None = None, nonce: str = "n1"):
    timestamp = str(ts if ts is not None else int(time.time()))
    signature = hashlib.sha256(
        timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    ).hexdigest()
    return {
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


def _feishu_event(account_id: str, text: str, event_id: str) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_type": "im.message.receive_v1",
            "event_id": event_id,
            "create_time": str(int(time.time() * 1000)),
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_synthetic_victim"}},
            "message": {
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


@pytest_asyncio.fixture
async def webhook_client(test_engine, monkeypatch):
    """预置一个配置了真实密钥的飞书渠道账号。"""
    monkeypatch.delenv("DEV_ALLOW_UNSIGNED_CHANNEL_WEBHOOK", raising=False)
    reset_replay_guard()

    async def override_get_db():
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(Enterprise(id=ENTERPRISE_ID, name="回调安全实验室"))
        session.add_all([
            ChannelAccount(
                id="acc-feishu",
                enterprise_id=ENTERPRISE_ID,
                channel_type="feishu_app",
                name="飞书应用",
                encrypted_credentials={"encrypt_key": encrypt_credential(ENCRYPT_KEY)},
            ),
            ChannelAccount(
                id="acc-wecom",
                enterprise_id=ENTERPRISE_ID,
                channel_type="wecom_bot",
                name="企微机器人",
                encrypted_credentials={
                    "token": encrypt_credential(WECOM_TOKEN),
                    "encoding_aes_key": encrypt_credential(WECOM_AES_KEY),
                },
            ),
            ChannelAccount(
                id="acc-wecom-second",
                enterprise_id=ENTERPRISE_ID,
                channel_type="wecom_bot",
                name="第二个企微机器人",
                encrypted_credentials={
                    "token": encrypt_credential(WECOM_TOKEN),
                    "encoding_aes_key": encrypt_credential(WECOM_AES_KEY),
                },
            ),
            ChannelAccount(
                id="acc-wecom-app",
                enterprise_id=ENTERPRISE_ID,
                channel_type="wecom_app",
                name="企微自建应用",
                encrypted_credentials={
                    "token": encrypt_credential(WECOM_TOKEN),
                    "encoding_aes_key": encrypt_credential(WECOM_AES_KEY),
                    "corp_id": encrypt_credential("wxcorp_wxid"),
                },
            ),
            ChannelAccount(
                id="acc-wecom-app-second",
                enterprise_id=ENTERPRISE_ID,
                channel_type="wecom_app",
                name="第二个企微自建应用",
                encrypted_credentials={
                    "token": encrypt_credential(WECOM_TOKEN),
                    "encoding_aes_key": encrypt_credential(WECOM_AES_KEY),
                    "corp_id": encrypt_credential("wxcorp_wxid"),
                },
            ),
            ChannelAccount(
                id="acc-wecom-app-nocorp",
                enterprise_id=ENTERPRISE_ID,
                channel_type="wecom_app",
                name="缺少 CorpID 的自建应用",
                encrypted_credentials={
                    "token": encrypt_credential(WECOM_TOKEN),
                    "encoding_aes_key": encrypt_credential(WECOM_AES_KEY),
                },
            ),
            ChannelAccount(
                id="acc-nokey",
                enterprise_id=ENTERPRISE_ID,
                channel_type="feishu_app",
                name="未配置密钥",
                encrypted_credentials={},
            ),
            # 只配置 token、不配置 EncodingAESKey：走"明文 + 签名"分支。
            ChannelAccount(
                id="acc-wecom-plain",
                enterprise_id=ENTERPRISE_ID,
                channel_type="wecom_bot",
                name="企微明文模式",
                encrypted_credentials={"token": encrypt_credential(WECOM_TOKEN)},
            ),
        ])
        await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory
    app.dependency_overrides.clear()
    reset_replay_guard()


@pytest.mark.asyncio
async def test_unsigned_feishu_callback_is_rejected(webhook_client):
    """AUD-08：无签名回调不得进入业务处理。"""
    client, factory = webhook_client
    body = json.dumps(
        _feishu_event("acc-feishu", "/绑定 ABC123", "evt-unsigned"), ensure_ascii=False
    ).encode()
    resp = await client.post("/api/v1/connectors/feishu/webhook/acc-feishu", content=body)
    # 缺签名头 = 401 未认证；签名不匹配 = 403；两者都必须被拒绝
    assert resp.status_code in (401, 403)

    async with factory() as session:
        identities = (await session.execute(select(ChannelIdentity))).scalars().all()
        assert all(i.is_bound is False for i in identities)
        assert all(i.internal_user_id is None for i in identities)


@pytest.mark.asyncio
async def test_signed_feishu_bind_token_still_requires_issued_code(webhook_client):
    """签名正确也不能用任意 6 位字符串完成绑定。"""
    client, factory = webhook_client
    payload = _feishu_event("acc-feishu", "/绑定 ABC123", "evt-signed-forged")
    body = json.dumps(payload, ensure_ascii=False).encode()
    resp = await client.post(
        "/api/v1/connectors/feishu/webhook/acc-feishu",
        content=body,
        headers=_feishu_headers(body, ENCRYPT_KEY),
    )
    assert resp.status_code == 200
    assert "绑定成功" not in resp.text

    async with factory() as session:
        identity = (
            await session.execute(
                select(ChannelIdentity).where(ChannelIdentity.external_user_id == "ou_synthetic_victim")
            )
        ).scalar_one()
        assert identity.is_bound is False
        assert identity.internal_user_id is None


@pytest.mark.asyncio
async def test_feishu_replay_is_rejected(webhook_client):
    """重放同一事件必须被拒绝。"""
    client, _factory = webhook_client
    body = json.dumps(_feishu_event("acc-feishu", "你好", "evt-replay"), ensure_ascii=False).encode()
    headers = _feishu_headers(body, ENCRYPT_KEY)

    first = await client.post("/api/v1/connectors/feishu/webhook/acc-feishu", content=body, headers=headers)
    assert first.status_code == 200
    second = await client.post("/api/v1/connectors/feishu/webhook/acc-feishu", content=body, headers=headers)
    assert second.status_code == 403


@pytest.mark.asyncio
async def test_feishu_stale_timestamp_is_rejected(webhook_client):
    """超出时钟偏差的回调必须拒绝。"""
    client, _factory = webhook_client
    body = json.dumps(_feishu_event("acc-feishu", "你好", "evt-stale"), ensure_ascii=False).encode()
    headers = _feishu_headers(body, ENCRYPT_KEY, ts=int(time.time()) - 4000)
    resp = await client.post("/api/v1/connectors/feishu/webhook/acc-feishu", content=body, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_feishu_account_without_key_fails_closed(webhook_client):
    """未配置密钥的渠道账号必须失败关闭，而不是放行。"""
    client, _factory = webhook_client
    body = json.dumps(_feishu_event("acc-nokey", "/绑定 ABC123", "evt-nokey"), ensure_ascii=False).encode()
    resp = await client.post("/api/v1/connectors/feishu/webhook/acc-nokey", content=body)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_wecom_encrypted_callback_round_trip(webhook_client):
    """企业微信密文分支：验签 + 解密后正常处理（**自造密文体**，非官方 XML 信封）。"""
    client, factory = webhook_client
    message = {
        "MsgType": "text",
        "MsgId": f"wecom-{uuid.uuid4()}",
        "FromUserName": "synthetic_wx_user",
        "Text": {"Content": "你好"},
    }
    body = _wecom_encrypt(message, WECOM_AES_KEY).encode()
    ts = str(int(time.time()))
    nonce = "n-wecom-1"
    query = {
        "msg_signature": wecom_signature(WECOM_TOKEN, ts, nonce, body.decode()),
        "timestamp": ts,
        "nonce": nonce,
    }
    resp = await client.post("/api/v1/connectors/wecom/webhook/acc-wecom", content=body, params=query)
    assert resp.status_code == 200

    async with factory() as session:
        identity = (
            await session.execute(
                select(ChannelIdentity).where(
                    ChannelIdentity.external_user_id == "synthetic_wx_user"
                )
            )
        ).scalar_one()
        assert identity.is_bound is False


@pytest.mark.asyncio
async def test_wecom_official_xml_envelope_is_rejected_not_silently_parsed(webhook_client):
    """官方 XML 信封（``<xml><Encrypt>…``）必须被拒绝，而不是被当成 JSON 放行。

    这条用例固定的是**当前实现的协议边界**：官方 XML 契约尚未实现，因此系统
    失败关闭。它同时防止后续有人把 XML 当成"多余字段"静默兼容。
    """
    client, _factory = webhook_client
    encrypted = _wecom_encrypt({"MsgType": "text", "MsgId": "xml-1", "Text": {"Content": "hi"}}, WECOM_AES_KEY)
    body = f"<xml><ToUserName><![CDATA[wxcorp]]></ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    ts = str(int(time.time()))
    resp = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom",
        content=body,
        params={
            "msg_signature": wecom_signature(WECOM_TOKEN, ts, "n-xml", body.decode()),
            "timestamp": ts,
            "nonce": "n-xml",
        },
    )
    # 不是 200：XML 信封不被误当作可解析载荷。
    assert resp.status_code != 200, resp.text


@pytest.mark.asyncio
async def test_wecom_encrypted_replay_is_rejected(webhook_client):
    """密文分支同样必须去重：同一 MsgId 的合法报文只能生效一次。"""
    client, factory = webhook_client
    message = {
        "MsgType": "text",
        "MsgId": f"wecom-replay-{uuid.uuid4()}",
        "FromUserName": "replay_wx_user",
        "Text": {"Content": "你好"},
    }
    body = _wecom_encrypt(message, WECOM_AES_KEY).encode()
    ts = str(int(time.time()))
    query = {
        "msg_signature": wecom_signature(WECOM_TOKEN, ts, "n-replay", body.decode()),
        "timestamp": ts,
        "nonce": "n-replay",
    }
    first = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom", content=body, params=query
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom", content=body, params=query
    )
    assert second.status_code == 403, second.text

    async with factory() as session:
        identity = (
            await session.execute(
                select(ChannelIdentity).where(
                    ChannelIdentity.external_user_id == "replay_wx_user"
                )
            )
        ).scalar_one()
        assert identity.is_bound is False


@pytest.mark.asyncio
async def test_wecom_bad_signature_is_rejected(webhook_client):
    client, _factory = webhook_client
    body = _wecom_encrypt({"MsgType": "text", "MsgId": "x1", "Text": {"Content": "hi"}}, WECOM_AES_KEY).encode()
    ts = str(int(time.time()))
    resp = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom",
        content=body,
        params={"msg_signature": "deadbeef", "timestamp": ts, "nonce": "n1"},
    )
    assert resp.status_code == 403


def _wecom_plain_body(msg_id: str, text: str = "hi") -> bytes:
    return json.dumps(
        {"MsgType": "text", "MsgId": msg_id, "FromUserName": "plain_user", "Text": {"Content": text}},
        ensure_ascii=False,
    ).encode("utf-8")


def _app_xml(msg_id: str, *, receive_id: str = "wxcorp_wxid", text: str = "hi",
             msg_type: str = "text") -> tuple[bytes, dict]:
    # Build a real XML plaintext and independent encrypted callback envelope.
    plain = (f"<xml><ToUserName>{receive_id}</ToUserName><FromUserName>app_user</FromUserName>"
             f"<CreateTime>1409659813</CreateTime><MsgType>{msg_type}</MsgType><Content>{text}</Content>"
             f"<MsgId>{msg_id}</MsgId><AgentID>218</AgentID></xml>").encode()
    key = base64.b64decode(WECOM_AES_KEY + "=")
    data = b"0" * 16 + struct.pack("!I", len(plain)) + plain + receive_id.encode()
    encrypted = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    ciphertext = base64.b64encode(encrypted.update(_pkcs7_pad(data)) + encrypted.finalize()).decode()
    body = f"<xml><ToUserName>{receive_id}</ToUserName><AgentID>218</AgentID><Encrypt>{ciphertext}</Encrypt></xml>".encode()
    ts = str(int(time.time()))
    query = {"timestamp": ts, "nonce": "app-nonce", "msg_signature": wecom_signature(WECOM_TOKEN, ts, "app-nonce", ciphertext)}
    return body, query


@pytest.mark.asyncio
async def test_wecom_app_official_xml_response_and_replay(webhook_client):
    from app.services.connectors.wecom_callback import _decrypt, parse_flat_xml

    client, factory = webhook_client
    body, query = _app_xml("app-msg-1")
    path = "/api/v1/connectors/wecom/webhook/acc-wecom-app"
    response = await client.post(path, content=body, params=query)
    assert response.status_code == 200, response.text
    outer = parse_flat_xml(response.content)
    assert outer["MsgSignature"] == wecom_signature(WECOM_TOKEN, outer["TimeStamp"], outer["Nonce"], outer["Encrypt"])
    inner = parse_flat_xml(_decrypt(outer["Encrypt"], base64.b64decode(WECOM_AES_KEY + "="), "wxcorp_wxid"))
    assert inner["ToUserName"] == "app_user"
    assert inner["MsgType"] == "text"
    assert inner["Content"]
    assert (await client.post(path, content=body, params=query)).status_code == 403
    async with factory() as session:
        identity = (await session.execute(select(ChannelIdentity).where(ChannelIdentity.external_user_id == "app_user"))).scalar_one()
        assert identity.channel_type == "wecom_app"


@pytest.mark.asyncio
async def test_wecom_app_url_verify_and_bad_inputs(webhook_client):
    client, _ = webhook_client
    key = base64.b64decode(WECOM_AES_KEY + "=")
    echo = b"verified-echo"
    data = b"0" * 16 + struct.pack("!I", len(echo)) + echo + b"wxcorp_wxid"
    enc = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = base64.b64encode(enc.update(_pkcs7_pad(data)) + enc.finalize()).decode()
    ts = str(int(time.time()))
    query = {"timestamp": ts, "nonce": "echo-nonce", "echostr": encrypted,
             "msg_signature": wecom_signature(WECOM_TOKEN, ts, "echo-nonce", encrypted)}
    path = "/api/v1/connectors/wecom/webhook/acc-wecom-app"
    response = await client.get(path, params=query)
    assert response.status_code == 200 and response.content == echo
    assert (await client.get(path, params={**query, "msg_signature": "bad"})).status_code == 403
    assert (await client.get(path, params={**query, "msg_signature": "é" * 40})).status_code == 403
    from app.services.connectors.wecom_callback import verify_url
    with pytest.raises(ChannelSignatureError) as oversized:
        verify_url({**query, "echostr": "A" * (256 * 1024 + 1)}, token=WECOM_TOKEN,
                   encoding_aes_key=WECOM_AES_KEY, corp_id="wxcorp_wxid")
    assert oversized.value.status_code == 400
    body, params = _app_xml("bad-receive", receive_id="wrong-corp")
    body = body.replace(b"<ToUserName>wrong-corp</ToUserName>",
                        b"<ToUserName>wxcorp_wxid</ToUserName>")
    assert (await client.post(path, content=body, params=params)).status_code == 403
    body, params = _app_xml("bad-xml")
    assert (await client.post(path, content=b"<xml><Encrypt>", params=params)).status_code == 400
    assert (await client.post(path, content=b"<!DOCTYPE xml [<!ENTITY x 'x'>]>" + body,
                              params=params)).status_code == 400
    assert (await client.post(path, content=body, params={**params, "msg_signature": "bad"})).status_code == 403
    assert (await client.post(path, content=body + b" " * (256 * 1024), params=params)).status_code == 413
    assert (await client.post("/api/v1/connectors/wecom/webhook/acc-wecom-app-nocorp",
                              content=body, params=params)).status_code == 403


def test_wecom_official_known_signature_vector():
    # WeCom encryption guide, 1409659813 / 1372623149 example.
    encrypted = ("RypEvHKD8QQKFhvQ6QleEB4J58tiPdvo+rtK1I9qca6aM/wvqnLSV5zEPeusUiX5L5X/0lWfrf0QADHHhGd3QczcdCUpj911L3vg3W/sYYvuJTs3TUUkSUXxaccAS0qhxchrRYt66wiSpGLYL42aM6A8dTT+6k4aSknmPj48kzJs8qLjvd4Xgpue06DOdnLxAUHzM6+kDZ+HMZfJYuR+LtwGc2hgf5gsijff0ekUNXZiqATP7PF5mZxZ3Izoun1s4zG4LUMnvw2r+KqCKIw+3IQH03v+BCA9nMELNqbSf6tiWSrXJB3LAVGUcallcrw8V2t9EL4EhzJWrQUax5wLVMNS0+rUPA3k22Ncx4XXZS9o0MBH27Bo6BpNelZpS+/uh9KsNlY6bHCmJU9p8g7m3fVKn28H3KDYA5Pl/T8Z1ptDAVe0lXdQ2YoyyH2uyPIGHBZZIs2pDBS8R07+qN+E7Q==")
    assert wecom_signature("QDG6eK", "1409659813", "1372623149", encrypted) == "477715d11cdb4164915debcba66cb864d751f3e6"
    from app.services.connectors.wecom_callback import _decrypt, parse_flat_xml

    key = base64.b64decode("jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C=")
    message = parse_flat_xml(_decrypt(encrypted, key, "wx5823bf96d3bd56c7"))
    assert message["Content"] == "hello"
    assert message["MsgId"] == "4561255354251345929"


@pytest.mark.asyncio
async def test_two_wecom_accounts_same_message_id_http(webhook_client):
    client, _ = webhook_client
    body, query = _app_xml("shared-app-id")
    base = "/api/v1/connectors/wecom/webhook/"
    assert (await client.post(base + "acc-wecom-app", content=body, params=query)).status_code == 200
    assert (await client.post(base + "acc-wecom-app-second", content=body, params=query)).status_code == 200
    assert (await client.post(base + "acc-wecom-app", content=body, params=query)).status_code == 403


@pytest.mark.asyncio
async def test_wecom_app_event_is_acknowledged_without_json_reply(webhook_client):
    client, _ = webhook_client
    body, query = _app_xml("event-1", msg_type="event")
    response = await client.post("/api/v1/connectors/wecom/webhook/acc-wecom-app",
                                 content=body, params=query)
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.asyncio
async def test_two_legacy_json_accounts_same_message_id_http(webhook_client):
    client, _ = webhook_client
    message = {"MsgType": "text", "MsgId": "shared-json-id", "FromUserName": "json_user", "Text": {"Content": "hi"}}
    body = _wecom_encrypt(message, WECOM_AES_KEY).encode()
    ts = str(int(time.time()))
    query = {"timestamp": ts, "nonce": "json-nonce", "msg_signature": wecom_signature(WECOM_TOKEN, ts, "json-nonce", body.decode())}
    base = "/api/v1/connectors/wecom/webhook/"
    assert (await client.post(base + "acc-wecom", content=body, params=query)).status_code == 200
    assert (await client.post(base + "acc-wecom-second", content=body, params=query)).status_code == 200
    assert (await client.post(base + "acc-wecom", content=body, params=query)).status_code == 403


def _wecom_plain_query(body: bytes, *, ts: int, nonce: str = "plain-n1") -> dict:
    timestamp = str(ts)
    return {
        "msg_signature": wecom_signature(WECOM_TOKEN, timestamp, nonce, body.decode("utf-8")),
        "timestamp": timestamp,
        "nonce": nonce,
    }


@pytest.mark.asyncio
async def test_wecom_plaintext_callback_round_trip(webhook_client):
    """明文 + 签名模式：签名正确即可正常进入业务处理。"""
    client, factory = webhook_client
    body = _wecom_plain_body(f"plain-{uuid.uuid4()}")
    resp = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom-plain",
        content=body,
        params=_wecom_plain_query(body, ts=int(time.time())),
    )
    assert resp.status_code == 200, resp.text

    async with factory() as session:
        identity = (
            await session.execute(
                select(ChannelIdentity).where(
                    ChannelIdentity.external_user_id == "plain_user"
                )
            )
        ).scalar_one()
        assert identity.is_bound is False


@pytest.mark.asyncio
async def test_wecom_plaintext_replay_is_rejected(webhook_client):
    """明文分支此前只验签不查重：同一份合法报文可被无限重放。"""
    client, factory = webhook_client
    body = _wecom_plain_body("plain-replay-1")
    query = _wecom_plain_query(body, ts=int(time.time()))

    first = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom-plain", content=body, params=query
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom-plain", content=body, params=query
    )
    assert second.status_code == 403, second.text

    # 绑定台账不得因重放而被消费。
    async with factory() as session:
        identity = (
            await session.execute(
                select(ChannelIdentity).where(
                    ChannelIdentity.external_user_id == "plain_user"
                )
            )
        ).scalar_one()
        assert identity.is_bound is False
        assert identity.internal_user_id is None


@pytest.mark.asyncio
async def test_wecom_plaintext_stale_timestamp_is_rejected(webhook_client):
    """明文分支也必须限制时钟偏差，否则抓包可长期重放。"""
    client, _factory = webhook_client
    body = _wecom_plain_body("plain-stale-1")
    resp = await client.post(
        "/api/v1/connectors/wecom/webhook/acc-wecom-plain",
        content=body,
        params=_wecom_plain_query(body, ts=int(time.time()) - 4000),
    )
    assert resp.status_code == 403, resp.text


def test_verify_wecom_plaintext_message_rejects_missing_token():
    with pytest.raises(ChannelSignatureError) as exc:
        verify_wecom_plaintext_message(body=b"{}", query={}, token="")
    assert exc.value.status_code == 403


class _FakeRedis:
    """最小 Redis 替身：只实现 SET NX EX 语义，并记录调用参数。"""

    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.calls: list[dict] = []

    def set(self, key, value, nx=False, ex=None):  # noqa: ANN001, ANN201 - 测试替身
        self.calls.append({"key": key, "nx": nx, "ex": ex})
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def test_replay_guard_dedupes_across_instances(monkeypatch):
    """重放去重必须跨进程生效，不能只靠进程内字典。"""
    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", False)
    store = _FakeRedis()
    instance_a = NonceReplayGuard(store=lambda: store)
    instance_b = NonceReplayGuard(store=lambda: store)
    assert instance_a.is_replay("feishu:acc-a:evt-1") is False
    assert instance_b.is_replay("feishu:acc-a:evt-1") is True
    assert instance_b.is_replay("feishu:acc-a:evt-2") is False


def test_replay_guard_uses_atomic_set_nx_ex(monkeypatch):
    """去重必须用单条原子 SET NX EX：先读后写会在并发下重复放行。"""
    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", False)
    store = _FakeRedis()
    guard = NonceReplayGuard(store=lambda: store)
    guard.is_replay("feishu:acc-a:evt-atomic")
    assert len(store.calls) == 1, "只允许一次存储往返"
    call = store.calls[0]
    assert call["nx"] is True
    assert call["ex"] == 600
    assert call["key"].startswith(REPLAY_REDIS_PREFIX)


def test_replay_guard_keeps_tenants_independent(monkeypatch):
    """不同企业的同名 event_id 不得互相误判为重放。"""
    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", False)
    store = _FakeRedis()
    guard = NonceReplayGuard(store=lambda: store)
    assert guard.is_replay("feishu:acc-tenant-a:evt-1") is False
    assert guard.is_replay("feishu:acc-tenant-b:evt-1") is False
    assert guard.is_replay("feishu:acc-tenant-a:evt-1") is True
    assert guard.is_replay("wecom:acc-tenant-b:evt-1") is False


def test_replay_guard_does_not_leak_raw_keys(monkeypatch):
    """存储键是消息 ID 的摘要，不得把外部可控 ID 明文写进 Redis。"""
    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", False)
    store = _FakeRedis()
    NonceReplayGuard(store=lambda: store).is_replay("feishu:acc-a:evt-secret-id")
    key = store.calls[0]["key"]
    assert "evt-secret-id" not in key
    assert len(key) == len(REPLAY_REDIS_PREFIX) + 64


def test_replay_guard_fails_closed_on_runtime_store_failure(monkeypatch):
    """Redis 运行中故障（非连接失败）同样必须失败关闭。"""
    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", False)

    class _BrokenRedis:
        def set(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201 - 测试替身
            raise ConnectionError("connection reset by peer")

    with pytest.raises(ChannelSignatureError) as exc:
        NonceReplayGuard(store=lambda: _BrokenRedis()).is_replay("feishu:acc-a:evt-4")
    assert exc.value.status_code == 503


def test_replay_guard_fails_closed_in_production(monkeypatch):
    """生产环境去重存储不可用时必须拒绝处理，不能退回进程内字典。"""
    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", False)

    def _unavailable():
        raise TokenRevocationStoreUnavailable("redis down")

    with pytest.raises(ChannelSignatureError) as exc:
        NonceReplayGuard(store=_unavailable).is_replay("feishu:acc-a:evt-3")
    assert exc.value.status_code == 503


def test_replay_guard_falls_back_only_in_debug(monkeypatch):
    """DEBUG 下存储不可用才允许进程内兜底（开发单机）。"""
    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", True)

    def _unavailable():
        raise TokenRevocationStoreUnavailable("redis down")

    guard = NonceReplayGuard(store=_unavailable)
    assert guard.is_replay("feishu:acc-a:evt-4") is False
    assert guard.is_replay("feishu:acc-a:evt-4") is True


def test_feishu_replay_key_is_scoped_per_account(monkeypatch):
    """端到端：两个渠道账号收到同一 event_id 时互不误拒。"""
    from app.services.connectors import signature as signature_module

    monkeypatch.setattr("app.services.connectors.signature.settings.DEBUG", False)
    store = _FakeRedis()
    # 单例守卫在每次调用时解析存储，这里替换它的存储来源。
    monkeypatch.setattr(signature_module._nonce_guard, "_store", lambda: store)
    body = json.dumps({"header": {"event_id": "shared-evt"}}).encode()
    headers = _feishu_headers(body, ENCRYPT_KEY)
    assert verify_feishu_event(
        body=body, headers=headers, encrypt_key=ENCRYPT_KEY, account_id="acc-1"
    )
    # 同 ID 在另一个企业账号上首次出现，必须放行。
    assert verify_feishu_event(
        body=body, headers=headers, encrypt_key=ENCRYPT_KEY, account_id="acc-2"
    )
    with pytest.raises(ChannelSignatureError):
        verify_feishu_event(
            body=body, headers=headers, encrypt_key=ENCRYPT_KEY, account_id="acc-1"
        )


def test_verify_feishu_event_rejects_missing_headers():
    with pytest.raises(ChannelSignatureError):
        verify_feishu_event(body=b"{}", headers={}, encrypt_key=ENCRYPT_KEY)


def test_verify_feishu_event_rejects_empty_key():
    with pytest.raises(ChannelSignatureError) as exc:
        verify_feishu_event(body=b"{}", headers={}, encrypt_key="")
    assert exc.value.status_code == 403


def test_verify_feishu_event_rejects_tampered_body():
    body = json.dumps({"header": {"event_id": "e1"}}).encode()
    headers = _feishu_headers(body, ENCRYPT_KEY)
    with pytest.raises(ChannelSignatureError):
        verify_feishu_event(body=body + b" ", headers=headers, encrypt_key=ENCRYPT_KEY)


@pytest.mark.asyncio
async def test_bind_generate_persists_ledger_and_is_admin_only(test_engine):
    """`/bind/generate` 必须落库、只对已登录且已归属企业的用户开放。"""
    from app.services.connectors.bind_tokens import hash_bind_token
    from app.models.channel_bind_token import ChannelBindToken

    async def override_get_db():
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(Enterprise(id=ENTERPRISE_ID, name="回调安全实验室"))
        await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 未认证 -> 401
        assert (await client.post("/api/v1/connectors/bind/generate")).status_code == 401

        # 已认证但未归属企业 -> 403
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id="u1", enterprise_id=None, role="member", is_active=True
        )
        assert (await client.post("/api/v1/connectors/bind/generate")).status_code == 403

        # 已归属企业 -> 200 且落库（只存摘要）
        user = User(
            id="u2",
            email="bind@test.com",
            password_hash="x",
            name="绑定用户",
            role="member",
            enterprise_id=ENTERPRISE_ID,
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=user.id, enterprise_id=ENTERPRISE_ID, role="member", is_active=True
        )
        resp = await client.post("/api/v1/connectors/bind/generate")
        assert resp.status_code == 200
        token = resp.json()["data"]["bind_token"]

        async with factory() as session:
            record = (
                await session.execute(
                    select(ChannelBindToken).where(
                        ChannelBindToken.token_hash == hash_bind_token(token)
                    )
                )
            ).scalar_one()
            assert record.internal_user_id == user.id
            assert record.enterprise_id == ENTERPRISE_ID
            assert record.consumed_at is None
            assert record.token_hash != token  # 明文不落库

    app.dependency_overrides.clear()
