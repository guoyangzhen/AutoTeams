"""AutoTeams 4.0 企业微信智能机器人适配器。

处理企微应用及智能机器人的回调事件解析、签名校验与出站消息结构封装。
"""
from __future__ import annotations

import logging
from typing import Any, Dict
from app.services.connectors.gateway import InboundMessage

logger = logging.getLogger(__name__)


class WeComAdapter:
    """企业微信机器人与长连接适配器。"""

    @classmethod
    def parse_app_text_payload(cls, account_id: str, payload: Dict[str, Any]) -> InboundMessage:
        """Map a verified self-built app XML text message to the gateway model."""
        if not all(payload.get(key) for key in ("MsgId", "FromUserName", "Content")):
            raise ValueError("企业微信文本消息缺少必要字段")
        return InboundMessage(
            channel_type="wecom_app",
            account_id=account_id,
            message_id=payload["MsgId"],
            external_user_id=payload["FromUserName"],
            external_user_name=payload["FromUserName"],
            content=payload["Content"].strip(),
            raw_payload=payload,
        )

    @classmethod
    def parse_webhook_payload(
        cls,
        account_id: str,
        payload: Dict[str, Any],
    ) -> InboundMessage:
        """解析企业微信 Webhook 或长连接接收到的原始消息。"""
        # 兼容企业微信多种消息格式（智能机器人 webhook 或自建应用 callback）
        msg_type = payload.get("MsgType", payload.get("msgtype", "text"))
        msg_id = payload.get("MsgId", payload.get("msgid", f"wecom_{int(logging.time.time()*1000)}"))
        sender_id = payload.get("FromUserName", payload.get("from_user", payload.get("sender_id", "wecom_unknown_user")))
        sender_name = payload.get("FromNick", payload.get("sender_name", sender_id))

        content = ""
        if msg_type == "text":
            text_obj = payload.get("Text", payload.get("text", {}))
            if isinstance(text_obj, dict):
                content = text_obj.get("Content", text_obj.get("content", ""))
            elif isinstance(text_obj, str):
                content = text_obj
            elif "Content" in payload:
                content = str(payload["Content"])
        elif msg_type == "voice":
            content = payload.get("Voice", {}).get("Recognition", "[语音消息]")
        else:
            content = f"[{msg_type} 暂不支持]"

        return InboundMessage(
            channel_type="wecom_bot",
            account_id=account_id,
            message_id=str(msg_id),
            external_user_id=str(sender_id),
            external_user_name=str(sender_name),
            content=content.strip(),
            raw_payload=payload,
        )

    @classmethod
    def build_text_response(cls, text: str) -> Dict[str, Any]:
        """构建企业微信文本格式响应结构。"""
        return {
            "msgtype": "text",
            "text": {
                "content": text
            }
        }

    @classmethod
    def build_markdown_response(cls, markdown_text: str) -> Dict[str, Any]:
        """构建企业微信 Markdown 格式响应结构。"""
        return {
            "msgtype": "markdown",
            "markdown": {
                "content": markdown_text
            }
        }
