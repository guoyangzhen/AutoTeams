"""Enterprise WeCom self-built application callback wire protocol.

This module accepts only the official encrypted XML contract. The legacy bot
JSON callback lives in ``signature.py`` and is selected by account type.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import struct
import time
from xml.parsers import expat
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services.connectors.signature import (
    ChannelSignatureError,
    _check_skew,
    _nonce_guard,
    _pkcs7_unpad,
    decode_encoding_aes_key,
    wecom_signature,
)

MAX_XML_BYTES = 256 * 1024


def parse_flat_xml(data: bytes) -> dict[str, str]:
    """Parse the small WeCom XML envelope without DTDs, entities or nesting."""
    if not data or len(data) > MAX_XML_BYTES:
        raise ChannelSignatureError("企业微信 XML 载荷大小非法", status_code=400)
    fields: dict[str, str] = {}
    stack: list[str] = []
    chunks: list[str] = []
    parser = expat.ParserCreate()

    def reject(*_args):
        raise ChannelSignatureError("企业微信 XML 禁止实体或 DTD", status_code=400)

    def start(name, _attrs):
        if (not stack and name != "xml") or len(stack) >= 2 or (stack and stack[0] != "xml"):
            reject()
        if len(stack) == 1 and name in fields:
            reject()
        stack.append(name)
        chunks.clear()

    def end(name):
        if not stack or stack[-1] != name:
            reject()
        if len(stack) == 2:
            fields[name] = "".join(chunks)
        stack.pop()
        chunks.clear()

    def content(value):
        if len(stack) == 2:
            chunks.append(value)
        elif value.strip():
            reject()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = content
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ChannelSignatureError("企业微信 XML 格式非法", status_code=400) from exc
    if stack:
        raise ChannelSignatureError("企业微信 XML 格式非法", status_code=400)
    return fields


def _decrypt(encrypted: str, aes_key: bytes, receive_id: str) -> bytes:
    try:
        encrypted.encode("ascii")
        ciphertext = base64.b64decode(encrypted, validate=True)
        if not ciphertext or len(ciphertext) % 16:
            raise ValueError
        decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16])).decryptor()
        plain = _pkcs7_unpad(decryptor.update(ciphertext) + decryptor.finalize())
        if len(plain) < 20:
            raise ValueError
        length = struct.unpack("!I", plain[16:20])[0]
        if 20 + length > len(plain) or plain[20 + length:] != receive_id.encode():
            raise ValueError
        return plain[20:20 + length]
    except (UnicodeError, ValueError, ChannelSignatureError) as exc:
        raise ChannelSignatureError("企业微信密文或 receive_id 校验失败", status_code=403) from exc


def _verify(encrypted: str, query: dict[str, str], token: str, aes_key: bytes, receive_id: str) -> bytes:
    timestamp = query.get("timestamp", "")
    nonce = query.get("nonce", "")
    signature = query.get("msg_signature", "")
    if not (timestamp and nonce and signature and encrypted):
        raise ChannelSignatureError("缺少企业微信签名参数", status_code=403)
    if len(timestamp) > 20 or len(nonce) > 128:
        raise ChannelSignatureError("企业微信签名参数过长", status_code=400)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", signature, flags=re.ASCII):
        raise ChannelSignatureError("企业微信签名格式非法", status_code=403)
    if len(encrypted) > MAX_XML_BYTES:
        raise ChannelSignatureError("企业微信密文大小非法", status_code=400)
    _check_skew(timestamp)
    if not hmac.compare_digest(wecom_signature(token, timestamp, nonce, encrypted), signature.lower()):
        raise ChannelSignatureError("企业微信签名不匹配", status_code=403)
    return _decrypt(encrypted, aes_key, receive_id)


def verify_url(query: dict[str, str], *, token: str, encoding_aes_key: str, corp_id: str) -> str:
    if not (token and encoding_aes_key and corp_id):
        raise ChannelSignatureError("企业微信自建应用回调配置不完整", status_code=403)
    message = _verify(query.get("echostr", ""), query, token, decode_encoding_aes_key(encoding_aes_key), corp_id)
    try:
        return message.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChannelSignatureError("企业微信 echostr 编码非法", status_code=400) from exc


def decrypt_callback(body: bytes, query: dict[str, str], *, token: str, encoding_aes_key: str,
                     corp_id: str, account_id: str) -> dict[str, str]:
    if not (token and encoding_aes_key and corp_id):
        raise ChannelSignatureError("企业微信自建应用回调配置不完整", status_code=403)
    envelope = parse_flat_xml(body)
    if envelope.get("ToUserName") != corp_id:
        raise ChannelSignatureError("企业微信收件企业不匹配", status_code=403)
    encrypted = envelope.get("Encrypt", "")
    message = _verify(encrypted, query, token, decode_encoding_aes_key(encoding_aes_key), corp_id)
    payload = parse_flat_xml(message)
    if payload.get("ToUserName") != corp_id:
        raise ChannelSignatureError("企业微信明文收件企业不匹配", status_code=403)
    event_key = payload.get("MsgId") or ":".join((payload.get("FromUserName", ""), payload.get("CreateTime", ""), payload.get("Event", "")))
    if not event_key.strip(":"):
        event_key = hashlib.sha256(encrypted.encode()).hexdigest()
    if _nonce_guard.is_replay(f"wecom:{account_id}:{event_key}"):
        raise ChannelSignatureError("检测到重放的企业微信消息", status_code=403)
    return payload


def encrypted_text_response(text: str, *, to_user: str, corp_id: str, agent_id: str,
                            token: str, encoding_aes_key: str) -> str:
    timestamp = str(int(time.time()))
    nonce = os.urandom(12).hex()
    inner = (f"<xml><ToUserName>{escape(to_user)}</ToUserName><FromUserName>{escape(corp_id)}</FromUserName>"
             f"<CreateTime>{timestamp}</CreateTime><MsgType>text</MsgType><Content>{escape(text)}</Content>"
             f"<AgentID>{escape(agent_id)}</AgentID></xml>").encode("utf-8")
    key = decode_encoding_aes_key(encoding_aes_key)
    plain = os.urandom(16) + struct.pack("!I", len(inner)) + inner + corp_id.encode()
    pad = 32 - len(plain) % 32
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = base64.b64encode(encryptor.update(plain + bytes([pad]) * pad) + encryptor.finalize()).decode()
    signature = wecom_signature(token, timestamp, nonce, encrypted)
    return (f"<xml><Encrypt>{encrypted}</Encrypt><MsgSignature>{signature}</MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp><Nonce>{nonce}</Nonce></xml>")
