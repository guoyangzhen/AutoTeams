"""AutoTeams 4.0 飞书（Feishu / Lark）机器人适配器。

处理飞书事件订阅（Event Callback）、URL 挑战校验与出站富文本/卡片消息渲染。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from app.services.connectors.gateway import InboundMessage

logger = logging.getLogger(__name__)


class FeishuAdapter:
    """飞书事件订阅与长连接适配器。"""

    @classmethod
    def handle_challenge(cls, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """飞书配置 Webhook 时的 URL 挑战验证响应。"""
        if "challenge" in payload:
            return {"challenge": payload["challenge"]}
        return None

    @classmethod
    def parse_event_payload(
        cls,
        account_id: str,
        payload: Dict[str, Any],
    ) -> Optional[InboundMessage]:
        """解析飞书开放平台 v2 事件订阅消息体。"""
        header = payload.get("header", {})
        event_id = header.get("event_id", f"feishu_{int(logging.time.time()*1000)}")

        event = payload.get("event", {})
        message = event.get("message", {})

        # 仅处理文本消息或 post 消息
        msg_type = message.get("message_type", "text")
        sender = event.get("sender", {}).get("sender_id", {})
        user_id = sender.get("open_id", sender.get("user_id", "feishu_unknown_user"))

        content = ""
        raw_content = message.get("content", "{}")
        try:
            parsed_content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
            if msg_type == "text":
                content = parsed_content.get("text", "")
            elif msg_type == "post":
                content = str(parsed_content)
        except Exception:
            content = str(raw_content)

        return InboundMessage(
            channel_type="feishu_app",
            account_id=account_id,
            message_id=str(event_id),
            external_user_id=str(user_id),
            external_user_name=sender.get("user_id", "飞书用户"),
            content=content.strip(),
            raw_payload=payload,
        )

    @classmethod
    def build_text_response(cls, text: str) -> Dict[str, Any]:
        """构建飞书文本消息结构体。"""
        return {
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
