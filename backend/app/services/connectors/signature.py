"""渠道回调签名校验与解密（AUD-08）。

历史实现里 `POST /connectors/{feishu,wecom}/webhook/{account_id}` 直接解析
请求体，没有任何验签、时间戳校验或重放防护。任何人都能伪造 `sender` 并发送
`/绑定 ABC123`，把渠道身份标记为"已绑定"（`internal_user_id` 仍为 NULL）。

本模块实现飞书与历史 ``wecom_bot`` JSON 入站校验。自建应用
``wecom_app`` 的官方加密 XML 契约在 ``wecom_callback.py`` 实现。

* **飞书事件订阅**：`X-Lark-Request-Timestamp` / `X-Lark-Request-Nonce` /
  `X-Lark-Signature`，签名 = `sha256(timestamp + nonce + encrypt_key + raw_body)`。
  额外校验时钟偏差（默认 ±5 分钟）与事件重放。
  飞书 `encrypt` 字段的**事件体解密**未在本模块实现。
* **历史 wecom_bot 密文 JSON 分支**：`msg_signature = sha1(sorted([token, timestamp, nonce, body]))`；
  按 WXBizMsgCrypt 用 AES-256-CBC 解密，校验 `receive_id`，
  明文结构为 `random(16) + msg_len(4, 大端) + msg + receive_id`。
  **边界**：本函数假设*整个请求体就是 base64 密文*，而官方加密模式的请求体是
  XML 信封 `<xml><Encrypt>…</Encrypt></xml>`，签名对象是 `Encrypt` 元素内容。
  该历史分支不接受官方 XML；``wecom_app`` 使用独立协议路径。
* **企业微信明文分支**（未配置 EncodingAESKey 时）：处理"整个请求体是 JSON 明文"
  的**自定义兼容形态**，不是官方 XML 契约。它与密文分支共用同一套安全语义
  （验签 + 时钟偏差 + 去重）。

**默认失败关闭**：渠道账号没有配置 `encrypt_key` / `token` 时，回调一律拒绝，
不再"没有密钥就放行"。仅在显式的本地开发开关下才允许跳过。

**重放去重**：使用安全状态 Redis 的单条原子 `SET key 1 NX EX 600`
（`utils.token_blacklist.security_redis_client`，该实例已强制 `noeviction`），
因此**跨实例生效**；生产环境存储不可用或运行中故障一律失败关闭（503），
只有 `DEBUG=true` 才退回进程内字典。键为 `{平台}:{account_id}:{消息ID的 SHA-256}`
——必须带 `account_id`，否则不同企业的相同消息 ID 会互相误判为重放。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.utils.branding import LEGACY_STATE_NAMESPACE
from app.utils.token_blacklist import (
    TokenRevocationStoreUnavailable,
    security_redis_client,
)


logger = logging.getLogger(__name__)

#: 允许的时钟偏差（秒）。
DEFAULT_MAX_SKEW_SECONDS = 300

#: 本地开发逃逸开关。生产环境必须保持关闭（`config.py` 启动检查会拒绝）。
DEV_ALLOW_UNSIGNED_WEBHOOK_ENV = "DEV_ALLOW_UNSIGNED_CHANNEL_WEBHOOK"


class ChannelSignatureError(Exception):
    """渠道回调验签/解密失败。调用方应返回 401/403，不做任何业务处理。"""

    def __init__(self, reason: str, *, status_code: int = 401) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def dev_unsigned_webhook_allowed() -> bool:
    """是否允许跳过验签（仅本地开发）。"""
    return os.environ.get(DEV_ALLOW_UNSIGNED_WEBHOOK_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _check_skew(timestamp: str, max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS) -> int:
    """校验时间戳偏差，返回解析出的时间戳。"""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise ChannelSignatureError("时间戳格式非法") from exc
    skew = abs(int(time.time()) - ts)
    if skew > max_skew_seconds:
        raise ChannelSignatureError(
            f"时间戳超出允许偏差（{skew}s > {max_skew_seconds}s）", status_code=403
        )
    return ts


#: 重放记录在安全状态 Redis 中的键前缀（与撤销键同实例、独立命名空间）。
REPLAY_REDIS_PREFIX = f"{LEGACY_STATE_NAMESPACE}:channel_event_seen:"


def _security_redis():
    """安全状态 Redis（已校验淘汰策略）。生产不可用时抛异常。"""
    return security_redis_client()


class NonceReplayGuard:
    """事件/消息 ID 重放防护。

    平台在重试时会复用 `event_id` / `MsgId`，签名本身不防重放，必须按消息 ID
    去重。签名校验通过的回调**不因为换一个 API 实例就能重复生效**，因此去重状态
    放在安全状态 Redis（`SET NX EX`）；只有 DEBUG 且 Redis 不可用时才退回进程内。
    生产环境存储不可用一律失败关闭（503），不静默降级。
    """

    def __init__(self, ttl_seconds: int = 600, store: Optional[Any] = None) -> None:
        self._ttl = ttl_seconds
        self._store = store or _security_redis
        self._seen: Dict[str, float] = {}

    @staticmethod
    def _redis_key(key: str) -> str:
        return REPLAY_REDIS_PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()

    def is_replay(self, key: str) -> bool:
        try:
            client = self._store()
        except TokenRevocationStoreUnavailable as exc:
            if not settings.DEBUG:
                raise ChannelSignatureError(
                    f"重放防护存储不可用，按失败关闭处理: {exc}", status_code=503
                ) from exc
            client = None

        if client is not None:
            try:
                # SET NX 只在首次出现时成功，天然跨实例去重。
                return not bool(client.set(self._redis_key(key), "1", nx=True, ex=self._ttl))
            except ChannelSignatureError:
                raise
            except Exception as exc:  # noqa: BLE001 - 运行期故障
                if not settings.DEBUG:
                    raise ChannelSignatureError(
                        f"重放防护存储不可用，按失败关闭处理: {exc}", status_code=503
                    ) from exc
                logger.warning("重放防护存储写入失败，回退到进程内缓存: %s", exc)

        now = time.time()
        self._seen = {k: v for k, v in self._seen.items() if now - v < self._ttl}
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def reset(self) -> None:
        self._seen.clear()


_nonce_guard = NonceReplayGuard()


def reset_replay_guard() -> None:
    """清空重放缓存（仅供测试使用）。"""
    _nonce_guard.reset()


# --------------------------------------------------------------------------
# 飞书
# --------------------------------------------------------------------------


def verify_feishu_event(
    *,
    body: bytes,
    headers: Dict[str, str],
    encrypt_key: str,
    account_id: str = "",
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> Dict[str, Any]:
    """校验飞书事件订阅回调并返回已解析的 JSON 载荷。"""
    if not encrypt_key:
        raise ChannelSignatureError("渠道账号未配置 encrypt_key，拒绝处理回调", status_code=403)

    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    timestamp = lowered.get("x-lark-request-timestamp", "")
    nonce = lowered.get("x-lark-request-nonce", "")
    signature = lowered.get("x-lark-signature", "")
    if not (timestamp and nonce and signature):
        raise ChannelSignatureError("缺少飞书签名请求头")

    _check_skew(timestamp, max_skew_seconds)

    digest = hashlib.sha256(
        timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    ).hexdigest()
    if not hmac.compare_digest(digest, signature.strip().lower()):
        raise ChannelSignatureError("飞书签名不匹配", status_code=403)

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChannelSignatureError("回调体不是合法 JSON") from exc

    event_id = str((payload.get("header") or {}).get("event_id") or nonce)
    # 键必须带渠道账号作用域：不同企业的 event_id 空间彼此独立，共用一个
    # 命名空间会让 A 企业的回调误判 B 企业的回调为重放。
    if _nonce_guard.is_replay(f"feishu:{account_id}:{event_id}"):
        raise ChannelSignatureError("检测到重放的飞书事件", status_code=403)
    return payload


# --------------------------------------------------------------------------
# 企业微信
# --------------------------------------------------------------------------


def wecom_signature(token: str, timestamp: str, nonce: str, payload: str) -> str:
    """企业微信签名：sha1(sorted([token, timestamp, nonce, payload]))。

    SHA-1 是平台协议规定的固定算法，不可替换。
    """
    parts = sorted([token, timestamp, nonce, payload])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()  # noqa: S324


def _pkcs7_unpad(data: bytes, block_size: int = 32) -> bytes:
    if not data or len(data) % block_size != 0:
        raise ChannelSignatureError("AES 明文长度非法")
    pad = data[-1]
    if pad < 1 or pad > block_size or data[-pad:] != bytes([pad]) * pad:
        raise ChannelSignatureError("AES PKCS#7 填充非法")
    return data[:-pad]


def decode_encoding_aes_key(encoding_aes_key: str) -> bytes:
    """把 43 字符的 EncodingAESKey 解码成 32 字节 AES 密钥。"""
    raw = encoding_aes_key.strip()
    if len(raw) != 43:
        raise ChannelSignatureError("EncodingAESKey 长度非法（应为 43）")
    try:
        aes_key = base64.b64decode(raw + "=")
    except ValueError as exc:
        raise ChannelSignatureError("EncodingAESKey 不是合法 Base64") from exc
    if len(aes_key) != 32:
        raise ChannelSignatureError("EncodingAESKey 解码后不是 32 字节")
    return aes_key


def _aes_cbc_decrypt(aes_key: bytes, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - 依赖已在 requirements.lock 中
        raise ChannelSignatureError("缺少 cryptography，无法解密企业微信回调") from exc

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()


def decrypt_wecom_message(
    *,
    body: bytes,
    query: Dict[str, str],
    token: str,
    encoding_aes_key: str,
    expected_receive_id: Optional[str] = None,
    account_id: str = "",
) -> Dict[str, Any]:
    """校验并解密企业微信加密回调，返回明文 JSON 载荷。

    企业微信把 `msg_signature` / `timestamp` / `nonce` 放在**查询参数**里，
    因此显式接收 `query`，不从 header 猜测。

    历史 ``wecom_bot`` JSON 边界：官方加密模式的请求体是 XML 信封
    ``<xml><Encrypt>…</Encrypt></xml>``，签名对象是 ``Encrypt`` 元素的内容；
    本函数假设**整个请求体就是 base64 密文**。自建应用官方 XML、GET
    ``echostr`` 与密文回复在 ``wecom_callback.py`` 处理。
    """
    if not token or not encoding_aes_key:
        raise ChannelSignatureError(
            "渠道账号未配置 token / encoding_aes_key，拒绝处理回调", status_code=403
        )

    lowered = {k.lower(): v for k, v in (query or {}).items()}
    timestamp = lowered.get("timestamp", "")
    nonce = lowered.get("nonce", "")
    msg_signature = lowered.get("msg_signature") or lowered.get("msgsignature")
    if not (timestamp and nonce and msg_signature):
        raise ChannelSignatureError("缺少企业微信签名参数")

    _check_skew(timestamp)

    try:
        body_text = body.decode("ascii")
        encrypted = base64.b64decode(body_text, validate=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChannelSignatureError("企业微信回调体不是合法 Base64") from exc

    expected = wecom_signature(token, timestamp, nonce, body_text)
    if not hmac.compare_digest(expected, (msg_signature or "").strip().lower()):
        raise ChannelSignatureError("企业微信签名不匹配", status_code=403)

    aes_key = decode_encoding_aes_key(encoding_aes_key)
    plaintext = _pkcs7_unpad(_aes_cbc_decrypt(aes_key, encrypted))

    # 结构：random(16) + msg_len(4, 大端) + msg + receive_id
    if len(plaintext) < 20:
        raise ChannelSignatureError("企业微信明文长度非法")
    msg_len = struct.unpack("!I", plaintext[16:20])[0]
    if 20 + msg_len > len(plaintext):
        raise ChannelSignatureError("企业微信消息长度字段非法")
    message = plaintext[20 : 20 + msg_len]
    receive_id = plaintext[20 + msg_len :].decode("utf-8", errors="ignore")
    if expected_receive_id and receive_id != expected_receive_id:
        raise ChannelSignatureError("企业微信 receive_id 不匹配", status_code=403)

    try:
        payload = json.loads(message.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChannelSignatureError("企业微信明文不是合法 JSON") from exc

    msg_id = str(payload.get("MsgId") or payload.get("msgid") or nonce)
    # 与飞书同理由：重放键必须带渠道账号作用域，否则 A 企业的消息会误判
    # B 企业同 ID 的消息为重放（两个平台的 ID 空间彼此独立）。
    if _nonce_guard.is_replay(f"wecom:{account_id}:{msg_id}"):
        raise ChannelSignatureError("检测到重放的企业微信消息", status_code=403)
    return payload


def verify_wecom_plaintext_message(
    *,
    body: bytes,
    query: Dict[str, str],
    token: str,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
    account_id: str = "",
) -> Dict[str, Any]:
    """校验企业微信「明文 + 签名」模式的 **JSON 兼容分支**（渠道账号未配置 EncodingAESKey）。

    这是一个**自定义 JSON 分支**，不是官方 XML 契约的完整实现：企业微信官方
    回调是 XML 信封（加密模式下为 ``<xml><Encrypt>…</Encrypt></xml>``），而本函数
    只处理"整个请求体就是 JSON 明文"的历史形态。官方 XML 契约在
    ``wecom_callback.py`` 处理，不能将两者混用。

    无论 body 形态如何，本分支强制与加密模式同一套安全语义：验签 + 时钟偏差 +
    按渠道账号作用域的消息去重。历史上它只比对签名，一份抓包即可无限重放。
    """
    if not token:
        raise ChannelSignatureError("渠道账号未配置回调 token", status_code=403)

    lowered = {k.lower(): v for k, v in (query or {}).items()}
    timestamp = lowered.get("timestamp", "")
    nonce = lowered.get("nonce", "")
    msg_signature = lowered.get("msg_signature") or lowered.get("msgsignature")
    if not (timestamp and nonce and msg_signature):
        raise ChannelSignatureError("缺少企业微信签名参数")

    _check_skew(timestamp, max_skew_seconds)

    try:
        body_text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChannelSignatureError("企业微信明文回调体不是合法 UTF-8") from exc

    expected = wecom_signature(token, timestamp, nonce, body_text)
    if not hmac.compare_digest(expected, (msg_signature or "").strip().lower()):
        raise ChannelSignatureError("企业微信签名不匹配", status_code=403)

    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise ChannelSignatureError("企业微信回调体不是合法 JSON", status_code=400) from exc
    if not isinstance(payload, dict):
        raise ChannelSignatureError("企业微信回调体不是 JSON 对象", status_code=400)

    # 与加密分支同理由：重放键必须带渠道账号作用域，否则不同企业的消息会
    # 因 MsgId 相同而互相误判为重放。
    msg_id = str(payload.get("MsgId") or payload.get("msgid") or nonce)
    if _nonce_guard.is_replay(f"wecom:{account_id}:{msg_id}"):
        raise ChannelSignatureError("检测到重放的企业微信消息", status_code=403)
    return payload


# --------------------------------------------------------------------------
# 凭据读取
# --------------------------------------------------------------------------


def account_webhook_credentials(
    decrypted_credentials: Dict[str, Any],
    channel_type: str,
) -> Tuple[str, str]:
    """从渠道账号明文凭据里取出验签所需字段。

    :return: ``(encrypt_key_or_token, encoding_aes_key)``；缺少必要字段时返回
        空串，由调用方按"失败关闭"处理。
    """
    creds = decrypted_credentials or {}
    if channel_type.startswith("feishu"):
        key = creds.get("encrypt_key") or creds.get("verification_token") or ""
        return str(key), str(creds.get("encoding_aes_key") or "")
    if channel_type.startswith("wecom"):
        token = creds.get("token") or creds.get("callback_token") or ""
        return str(token), str(creds.get("encoding_aes_key") or "")
    return str(creds.get("token") or ""), str(creds.get("encoding_aes_key") or "")


__all__ = [
    "ChannelSignatureError",
    "DEFAULT_MAX_SKEW_SECONDS",
    "DEV_ALLOW_UNSIGNED_WEBHOOK_ENV",
    "NonceReplayGuard",
    "account_webhook_credentials",
    "decode_encoding_aes_key",
    "decrypt_wecom_message",
    "dev_unsigned_webhook_allowed",
    "reset_replay_guard",
    "verify_feishu_event",
    "wecom_signature",
]
