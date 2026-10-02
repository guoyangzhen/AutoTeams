"""AutoTeams 4.0 全渠道接入网关与即时通讯连接器 API。

授权与可信来源（AUD-08）
-----------------------
* 渠道回调（飞书 / 企业微信）必须先按平台协议验签、时间戳校验与重放防护；
  账号未配置密钥时**失败关闭**，不再"没有密钥就放行"。
* `/bind/generate` 签发的是服务端台账中的一次性绑定码（只存摘要、短 TTL、
  一次性消费），`/绑定` 走同一台账校验；任意 6 位字符串不再能伪造绑定。
* 所有需要登录的端点都先要求用户已归属企业，再按企业过滤渠道账号。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.channel_account import ChannelAccount
from app.models.user import User
from app.services.connectors.gateway import (
    InboundMessage,
    OmnichannelGateway,
)
from app.services.connectors.wecom_adapter import WeComAdapter
from app.services.connectors.wecom_callback import (
    MAX_XML_BYTES,
    decrypt_callback,
    encrypted_text_response,
    verify_url,
)
from app.services.connectors.feishu_adapter import FeishuAdapter
from app.services.connectors.signature import (
    ChannelSignatureError,
    account_webhook_credentials,
    decrypt_wecom_message,
    dev_unsigned_webhook_allowed,
    verify_feishu_event,
    verify_wecom_plaintext_message,
)
from app.services.connectors.bind_tokens import (
    BindTokenError,
    DEFAULT_BIND_TOKEN_TTL_MINUTES,
    issue_bind_token,
    token_ttl_seconds,
)
from app.services.connectors.bootstrap import (
    ChannelBootstrapUnavailable,
    ChannelWebhookMaterial,
    bind_channel_tenant,
    load_channel_webhook_material,
    secret_fingerprint,
)
from app.utils.credential_crypto import decrypt_credential, encrypt_credential
from app.utils.security import get_current_user
from app.utils.tenant_scope import ROLE_ADMIN, assert_role, require_enterprise_bound
from app.utils.response import success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["AutoTeams Omnichannel Connectors"])

#: 模拟入站端点只在开发/测试开放，避免生产被用来伪造渠道消息。
SIMULATE_INBOUND_ENV = "DEV_ALLOW_SIMULATE_CHANNEL_INBOUND"


class ChannelAccountCreate(BaseModel):
    channel_type: str = Field(..., description="渠道类型: wecom_app, wecom_bot, feishu_app, dingtalk_bot")
    name: str = Field(..., min_length=1, max_length=64, description="通道名称")
    description: Optional[str] = None
    credentials: Dict[str, Any] = Field(default_factory=dict, description="认证凭据（明文输入，系统自动加密落库）")
    mounted_profile_ids: List[str] = Field(default_factory=list, description="挂载的数字员工 profile_id 列表")
    default_profile_id: Optional[str] = None


class ChannelAccountUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    mounted_profile_ids: Optional[List[str]] = None
    default_profile_id: Optional[str] = None
    is_active: Optional[bool] = None


class SimulateInboundRequest(BaseModel):
    account_id: str
    channel_type: str = "wecom_bot"
    external_user_id: str = "test_user_001"
    external_user_name: Optional[str] = "测试员工"
    content: str
    message_id: Optional[str] = None


def _decrypt_credentials(account: ChannelAccount) -> Dict[str, Any]:
    """把渠道账号的加密凭据还原为明文（仅在验签路径内使用，不回传客户端）。"""
    result: Dict[str, Any] = {}
    for key, value in (account.encrypted_credentials or {}).items():
        if isinstance(value, str) and value:
            try:
                result[key] = decrypt_credential(value)
            except Exception:  # noqa: BLE001 - 密文损坏时按"未配置"处理
                logger.warning("渠道账号 %s 的凭据 %s 解密失败", account.id, key)
                result[key] = ""
        else:
            result[key] = value
    return result


def _apply_webhook_token_hash(account: ChannelAccount, credentials: Dict[str, Any]) -> None:
    """保存账号时同步写入回调密钥摘要（AUD-19 租户引导用）。

    摘要只用于"验签通过后换租户"，不参与任何签名计算；账号没有配置回调密钥时
    置空，此时回调无法绑定租户（失败关闭）。
    """
    secret, _ = account_webhook_credentials(credentials, account.channel_type)
    account.webhook_token_hash = secret_fingerprint(secret) if secret else None


def _simulate_inbound_allowed() -> bool:
    if settings.DEBUG:
        return True
    import os

    return os.environ.get(SIMULATE_INBOUND_ENV, "").strip().lower() in ("1", "true", "yes")


@router.get("/accounts")
async def list_channel_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询当前企业已配置的渠道接入账号列表。"""
    require_enterprise_bound(current_user)
    stmt = select(ChannelAccount).where(ChannelAccount.enterprise_id == current_user.enterprise_id)
    res = await db.execute(stmt)
    accounts = res.scalars().all()

    data = []
    for acc in accounts:
        data.append({
            "id": acc.id,
            "channel_type": acc.channel_type,
            "name": acc.name,
            "description": acc.description,
            "mounted_profile_ids": acc.mounted_profile_ids,
            "default_profile_id": acc.default_profile_id,
            "status": acc.status,
            "is_active": acc.is_active,
            "created_at": acc.created_at,
            "updated_at": acc.updated_at,
        })
    return success_response(data=data)


@router.post("/accounts")
async def create_channel_account(
    req: ChannelAccountCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建并保存新的渠道接入配置（凭据全程对称加密落地）。"""
    require_enterprise_bound(current_user)
    assert_role(current_user, ROLE_ADMIN)
    encrypted_creds = {}
    for k, v in req.credentials.items():
        if isinstance(v, str):
            encrypted_creds[k] = encrypt_credential(v)
        else:
            encrypted_creds[k] = v

    account = ChannelAccount(
        enterprise_id=current_user.enterprise_id,
        channel_type=req.channel_type,
        name=req.name,
        description=req.description,
        encrypted_credentials=encrypted_creds,
        mounted_profile_ids=req.mounted_profile_ids,
        default_profile_id=req.default_profile_id,
        status="configured",
        is_active=True,
    )
    _apply_webhook_token_hash(account, req.credentials)
    await db.commit()
    await db.refresh(account)

    return success_response(
        data={"id": account.id, "name": account.name, "status": account.status},
        message="渠道接入配置创建成功",
    )


@router.put("/accounts/{account_id}")
async def update_channel_account(
    account_id: str,
    req: ChannelAccountUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新渠道配置（员工挂载、默认承接人或凭据）。"""
    require_enterprise_bound(current_user)
    assert_role(current_user, ROLE_ADMIN)
    stmt = select(ChannelAccount).where(
        ChannelAccount.id == account_id,
        ChannelAccount.enterprise_id == current_user.enterprise_id,
    )
    res = await db.execute(stmt)
    acc = res.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="渠道配置不存在")

    if req.name is not None:
        acc.name = req.name
    if req.description is not None:
        acc.description = req.description
    if req.mounted_profile_ids is not None:
        acc.mounted_profile_ids = req.mounted_profile_ids
    if req.default_profile_id is not None:
        acc.default_profile_id = req.default_profile_id
    if req.is_active is not None:
        acc.is_active = req.is_active
    if req.credentials is not None:
        encrypted_creds = {}
        for k, v in req.credentials.items():
            if isinstance(v, str):
                encrypted_creds[k] = encrypt_credential(v)
            else:
                encrypted_creds[k] = v
        acc.encrypted_credentials = encrypted_creds
        _apply_webhook_token_hash(acc, req.credentials)

    await db.commit()
    await db.refresh(acc)
    return success_response(data={"id": acc.id, "name": acc.name}, message="更新成功")


@router.delete("/accounts/{account_id}")
async def delete_channel_account(
    account_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除渠道接入配置。"""
    require_enterprise_bound(current_user)
    assert_role(current_user, ROLE_ADMIN)
    stmt = select(ChannelAccount).where(
        ChannelAccount.id == account_id,
        ChannelAccount.enterprise_id == current_user.enterprise_id,
    )
    res = await db.execute(stmt)
    acc = res.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="渠道配置不存在")

    await db.delete(acc)
    await db.commit()
    return success_response(message="渠道配置已删除")


@router.post("/bind/generate")
async def generate_binding_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """为当前登录用户签发一次性渠道身份绑定码。

    AUD-08：绑定码不再只是一个随机串 —— 服务端记录发起用户、企业、有效期，
    数据库里只保存摘要；消费一次即失效。
    """
    require_enterprise_bound(current_user)
    try:
        issued = await issue_bind_token(
            db,
            enterprise_id=current_user.enterprise_id,
            internal_user_id=current_user.id,
        )
    except BindTokenError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc
    return success_response(
        data={
            "bind_token": issued.token,
            "expires_in_seconds": token_ttl_seconds(DEFAULT_BIND_TOKEN_TTL_MINUTES),
            "instruction": f"在企微或飞书中输入：/绑定 {issued.token}",
        },
        message="绑定码生成成功",
    )


@router.post("/simulate/inbound")
async def simulate_inbound_message(
    req: SimulateInboundRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """开发与调试模拟端点：无需连通真实企业微信/飞书服务器即可全链路验证网关调度。

    AUD-08：只允许在 DEBUG 或显式开关下使用，且渠道账号必须属于调用者企业，
    否则它就是一个"任何人可伪造渠道消息"的入口。
    """
    require_enterprise_bound(current_user)
    if not _simulate_inbound_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="模拟入站端点仅在开发环境开放",
        )

    account = (
        await db.execute(
            select(ChannelAccount).where(
                ChannelAccount.id == req.account_id,
                ChannelAccount.enterprise_id == current_user.enterprise_id,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="渠道配置不存在")

    import uuid

    msg = InboundMessage(
        channel_type=req.channel_type,
        account_id=req.account_id,
        message_id=req.message_id or f"sim-{uuid.uuid4()}",
        external_user_id=req.external_user_id,
        external_user_name=req.external_user_name,
        content=req.content,
    )
    result = await OmnichannelGateway.process_inbound_message(db, msg)
    return success_response(data=result.model_dump())


class WebhookAccount:
    """验签阶段的账号视图（由引导连接提供，不含租户信息）。"""

    def __init__(self, material: "ChannelWebhookMaterial") -> None:
        self.id = material.account_id
        self.channel_type = material.channel_type
        self.encrypted_credentials = material.encrypted_credentials


async def _load_webhook_account(db: AsyncSession, account_id: str) -> WebhookAccount:
    """R3 引导第一阶段：只取验签材料，不绑定租户。

    这里**不能**按 account_id 直接换租户 —— 那等于把租户交给任何拿到该 ID 的
    调用方。租户在验签成功之后由 `_bind_webhook_tenant` 用回调密钥摘要绑定。
    """
    try:
        material = await load_channel_webhook_material(account_id, db)
    except ChannelBootstrapUnavailable as exc:
        # 引导角色没配好是部署问题，明确 503，不要伪装成"账号不存在"。
        logger.error("渠道回调引导不可用: %s", exc)
        raise HTTPException(status_code=503, detail="渠道回调暂时不可用") from exc
    if material is None or not material.is_active:
        raise HTTPException(status_code=404, detail="渠道账号不存在或已停用")
    return WebhookAccount(material)


async def _bind_webhook_tenant(db: AsyncSession, account_id: str, token: str) -> None:
    """R3 引导第二阶段：验签通过后，用回调密钥摘要把租户绑定到业务会话。"""
    enterprise_id = await bind_channel_tenant(db, account_id, token)
    if enterprise_id is None:
        raise HTTPException(status_code=403, detail="渠道回调验签失败: 账号回调密钥不匹配")


def _reject_unsigned(reason: str) -> None:
    """开发模式下未显式开关时也拒绝无签名回调。"""
    if dev_unsigned_webhook_allowed():
        logger.warning("已放行未验签的渠道回调（开发模式）: %s", reason)
        return
    raise HTTPException(status_code=403, detail=f"渠道回调验签失败: {reason}")


@router.get("/wecom/webhook/{account_id}")
async def wecom_verify_url_endpoint(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    account = await _load_webhook_account(db, account_id)
    if account.channel_type != "wecom_app":
        raise HTTPException(status_code=404, detail="渠道类型不支持 URL 验证")
    credentials = _decrypt_credentials(account)
    token, aes_key = account_webhook_credentials(credentials, account.channel_type)
    try:
        echo = verify_url(dict(request.query_params), token=token, encoding_aes_key=aes_key,
                          corp_id=str(credentials.get("corp_id") or ""))
    except ChannelSignatureError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc
    return Response(content=echo, media_type="text/plain")


@router.post("/wecom/webhook/{account_id}")
async def wecom_webhook_endpoint(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """企业微信回调入口（验签 + AES 解密后才进入业务处理）。"""
    account = await _load_webhook_account(db, account_id)

    if account.channel_type == "wecom_app":
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_XML_BYTES:
                raise HTTPException(status_code=413, detail="企业微信 XML 载荷过大")
            chunks.append(chunk)
        raw_body = b"".join(chunks)
        credentials = _decrypt_credentials(account)
        token, aes_key = account_webhook_credentials(credentials, account.channel_type)
        corp_id = str(credentials.get("corp_id") or "")
        try:
            payload = decrypt_callback(raw_body, dict(request.query_params), token=token,
                                       encoding_aes_key=aes_key, corp_id=corp_id, account_id=account_id)
        except ChannelSignatureError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc
        await _bind_webhook_tenant(db, account_id, token)
        # Only ordinary text messages have a supported passive reply. Events and
        # other message types are acknowledged without inventing a reply format.
        if payload.get("MsgType") != "text" or not payload.get("FromUserName"):
            return Response(content="", media_type="text/plain")
        try:
            inbound = WeComAdapter.parse_app_text_payload(account_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        dispatch_res = await OmnichannelGateway.process_inbound_message(db, inbound)
        if dispatch_res.is_command:
            reply = dispatch_res.command_response or "已处理"
        else:
            prefix = f"【{dispatch_res.dispatched_profile_name}】为您服务：\n"
            reply = (f"{prefix}我正在为您执行业务流程，已收到您补充的信息：{inbound.content}"
                     if dispatch_res.is_sop_protected else f"{prefix}您好，我已经理解您的诉求并已介入跟进。")
        response_xml = encrypted_text_response(reply, to_user=inbound.external_user_id,
                                               corp_id=corp_id, agent_id=payload.get("AgentID", ""),
                                               token=token, encoding_aes_key=aes_key)
        return Response(content=response_xml, media_type="application/xml")

    if account.channel_type != "wecom_bot":
        raise HTTPException(status_code=404, detail="渠道类型不支持企业微信回调")

    raw_body = await request.body()

    token, encoding_aes_key = account_webhook_credentials(
        _decrypt_credentials(account), account.channel_type
    )
    if token and encoding_aes_key:
        try:
            payload = decrypt_wecom_message(
                body=raw_body,
                query=dict(request.query_params),
                token=token,
                encoding_aes_key=encoding_aes_key,
                account_id=account_id,
            )
        except ChannelSignatureError as exc:
            logger.warning("企业微信回调验签失败: %s", exc.reason)
            raise HTTPException(status_code=exc.status_code, detail=f"企业微信回调验签失败: {exc.reason}") from exc
    else:
        # 未配置 AES 密钥时只接受"明文 + 签名"模式：仍需验签、时钟偏差与重放去重
        # （AUD-08：明文分支此前只比对签名，抓包可无限重放）。
        if not token:
            _reject_unsigned("渠道账号未配置回调 token")
        try:
            payload = verify_wecom_plaintext_message(
                body=raw_body,
                query=dict(request.query_params),
                token=token,
                account_id=account_id,
            )
        except ChannelSignatureError as exc:
            logger.warning("企业微信回调验签失败: %s", exc.reason)
            raise HTTPException(
                status_code=exc.status_code, detail=f"企业微信回调验签失败: {exc.reason}"
            ) from exc

    # R3 引导第二阶段：**验签成功之后**才用回调密钥摘要绑定租户；验签失败路径
    # 在上面就返回了，永远走不到这里。
    await _bind_webhook_tenant(db, account_id, token)

    inbound = WeComAdapter.parse_webhook_payload(account_id, payload)
    dispatch_res = await OmnichannelGateway.process_inbound_message(db, inbound)

    if dispatch_res.is_command:
        reply = dispatch_res.command_response or "已处理"
        return WeComAdapter.build_text_response(reply)

    prefix = f"【{dispatch_res.dispatched_profile_name}】为您服务：\n"
    if dispatch_res.is_sop_protected:
        reply = f"{prefix}我正在为您执行业务流程，已收到您补充的信息：{inbound.content}"
    else:
        reply = f"{prefix}您好，我已经理解您的诉求并已介入跟进。"

    return WeComAdapter.build_text_response(reply)


@router.post("/feishu/webhook/{account_id}")
async def feishu_webhook_endpoint(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """飞书事件订阅回调入口（验签后才进入业务处理）。

    飞书配置订阅时的 URL Challenge 不带业务事件头，但仍需签名；
    官方约定用 challenge 原文参与签名，因此这里单独处理。
    """
    account = await _load_webhook_account(db, account_id)
    if not account.channel_type.startswith("feishu"):
        raise HTTPException(status_code=404, detail="渠道类型不支持飞书回调")
    raw_body = await request.body()

    encrypt_key, _encoding_aes_key = account_webhook_credentials(
        _decrypt_credentials(account), account.channel_type
    )
    if not encrypt_key:
        _reject_unsigned("渠道账号未配置 encrypt_key")

    try:
        payload = verify_feishu_event(
            body=raw_body,
            headers=dict(request.headers),
            encrypt_key=encrypt_key,
            account_id=account_id,
        )
    except ChannelSignatureError as exc:
        logger.warning("飞书回调验签失败: %s", exc.reason)
        raise HTTPException(status_code=exc.status_code, detail=f"飞书回调验签失败: {exc.reason}") from exc

    # R3 引导第二阶段：验签通过之后才绑定租户（URL Challenge 也已完成验签）。
    await _bind_webhook_tenant(db, account_id, encrypt_key)

    challenge = FeishuAdapter.handle_challenge(payload)
    if challenge:
        return challenge

    inbound = FeishuAdapter.parse_event_payload(account_id, payload)
    if not inbound:
        return {"code": 0, "msg": "ignored"}

    dispatch_res = await OmnichannelGateway.process_inbound_message(db, inbound)
    if dispatch_res.is_command:
        reply = dispatch_res.command_response or "已处理"
    else:
        prefix = f"【{dispatch_res.dispatched_profile_name}】为您服务：\n"
        reply = f"{prefix}已收到您的消息：{inbound.content}"

    return FeishuAdapter.build_text_response(reply)
