"""AutoTeams 4.0 Omnichannel 统一渠道接入网关与意图调度中心。

包含：
1. 入站幂等去重（Idempotent Deduplication）
2. 跨渠道身份合并与一次性绑定校验（/绑定 123456）
3. 渠道指令拦截调度器（/员工, /切换, /当前, /重置, /帮助）
4. 活跃 SOP 粘性保护窗（SOP Protection Window）
5. 智能意图分发器（Intent Router）
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel_account import ChannelAccount, ChannelIdentity
from app.models.workforce import WorkforceProfile
from app.utils.time import utcnow
from app.services.connectors.bind_tokens import BindTokenError, consume_bind_token

logger = logging.getLogger(__name__)


class InboundMessage(BaseModel):
    channel_type: str                   # wecom_bot, feishu_app, etc.
    account_id: str
    message_id: str
    external_user_id: str
    external_user_name: Optional[str] = None
    content: str
    timestamp: Optional[int] = None
    raw_payload: Optional[Dict[str, Any]] = None


class GatewayDispatchResult(BaseModel):
    is_command: bool = False
    command_response: Optional[str] = None
    dispatched_profile_id: Optional[str] = None
    dispatched_profile_name: Optional[str] = None
    dispatched_profile_badge: Optional[str] = None
    is_sop_protected: bool = False
    active_flow_id: Optional[str] = None
    should_execute_flow: bool = False
    system_notice: Optional[str] = None


class DeduplicationCache:
    """基于内存和时间戳的消息去重缓存（支持生产环境 Redis 替换）。"""
    def __init__(self, ttl_seconds: int = 300):
        self._cache: Dict[str, float] = {}
        self._ttl = ttl_seconds

    def is_duplicate(self, message_id: str) -> bool:
        now = time.time()
        # 清理过期项
        self._cache = {k: v for k, v in self._cache.items() if now - v < self._ttl}
        if message_id in self._cache:
            return True
        self._cache[message_id] = now
        return False


_global_dedup_cache = DeduplicationCache()


class OmnichannelGateway:
    """全渠道网关接入与路由调度核心服务。"""

    @classmethod
    async def get_or_create_identity(
        cls,
        db: AsyncSession,
        enterprise_id: str,
        channel_type: str,
        external_user_id: str,
        external_user_name: Optional[str] = None,
    ) -> ChannelIdentity:
        """根据渠道外部 ID 获取或初始化身份映射。"""
        stmt = select(ChannelIdentity).where(
            ChannelIdentity.enterprise_id == enterprise_id,
            ChannelIdentity.channel_type == channel_type,
            ChannelIdentity.external_user_id == external_user_id,
        )
        res = await db.execute(stmt)
        identity = res.scalar_one_or_none()

        if not identity:
            identity = ChannelIdentity(
                enterprise_id=enterprise_id,
                channel_type=channel_type,
                external_user_id=external_user_id,
                external_user_name=external_user_name,
            )
            db.add(identity)
            await db.commit()
            await db.refresh(identity)
        return identity

    @classmethod
    async def process_inbound_message(
        cls,
        db: AsyncSession,
        msg: InboundMessage,
    ) -> GatewayDispatchResult:
        """处理渠道入站消息核心流水线。"""
        # 1. 幂等去重检查
        if _global_dedup_cache.is_duplicate(f"{msg.channel_type}:{msg.account_id}:{msg.message_id}"):
            logger.info(f"忽略重复入站消息: {msg.message_id}")
            return GatewayDispatchResult(
                is_command=True,
                command_response="[系统提示: 忽略重复消息]",
            )

        # 2. 查询渠道账号配置
        account_stmt = select(ChannelAccount).where(ChannelAccount.id == msg.account_id)
        acc_res = await db.execute(account_stmt)
        account = acc_res.scalar_one_or_none()
        if not account or not account.is_active:
            return GatewayDispatchResult(
                is_command=True,
                command_response="[系统提示: 该渠道账号已下线或未启用]",
            )

        # 3. 获取或初始化用户映射身份
        identity = await cls.get_or_create_identity(
            db=db,
            enterprise_id=account.enterprise_id,
            channel_type=msg.channel_type,
            external_user_id=msg.external_user_id,
            external_user_name=msg.external_user_name,
        )

        # 4. 获取当前挂载的数字员工列表
        mounted_ids = account.mounted_profile_ids or []
        profiles: List[WorkforceProfile] = []
        if mounted_ids:
            p_stmt = select(WorkforceProfile).where(WorkforceProfile.id.in_(mounted_ids))
            p_res = await db.execute(p_stmt)
            profiles = list(p_res.scalars().all())

        raw_text = msg.content.strip()

        # 5. 渠道内置指令拦截判断 (以 '/' 开头)
        if raw_text.startswith("/"):
            return await cls._handle_channel_command(
                db=db,
                command_text=raw_text,
                identity=identity,
                account=account,
                profiles=profiles,
            )

        # 6. SOP 保护窗检查 (Affinity & Protection Window)
        now_dt = utcnow()
        if identity.sop_window_locked_until and identity.sop_window_locked_until > now_dt:
            if identity.active_profile_id:
                active_profile = next((p for p in profiles if p.id == identity.active_profile_id), None)
                profile_name = active_profile.display_name if active_profile else "数字员工"
                profile_badge = active_profile.employee_badge if active_profile else ""
                logger.info(
                    f"用户 {msg.external_user_id} 处于 SOP 保护窗内，锁定承接员工: {profile_name} ({identity.active_flow_id})"
                )
                return GatewayDispatchResult(
                    is_command=False,
                    dispatched_profile_id=identity.active_profile_id,
                    dispatched_profile_name=profile_name,
                    dispatched_profile_badge=profile_badge,
                    is_sop_protected=True,
                    active_flow_id=identity.active_flow_id,
                    should_execute_flow=True,
                )

        # 7. 智能意图路由分发 (Intent Router)
        dispatched_profile = cls._route_intent(raw_text, profiles, account.default_profile_id)
        if not dispatched_profile and profiles:
            dispatched_profile = profiles[0]

        if dispatched_profile:
            # 记录会话粘性
            identity.active_profile_id = dispatched_profile.id
            await db.commit()

            return GatewayDispatchResult(
                is_command=False,
                dispatched_profile_id=dispatched_profile.id,
                dispatched_profile_name=dispatched_profile.display_name,
                dispatched_profile_badge=dispatched_profile.employee_badge,
                is_sop_protected=False,
                should_execute_flow=False,
            )

        return GatewayDispatchResult(
            is_command=True,
            command_response="您好，当前机器人尚未挂载可履约的数字员工，请联系企业管理员配置。",
        )

    @classmethod
    async def _handle_channel_command(
        cls,
        db: AsyncSession,
        command_text: str,
        identity: ChannelIdentity,
        account: ChannelAccount,
        profiles: List[WorkforceProfile],
    ) -> GatewayDispatchResult:
        """处理渠道交互指令。"""
        parts = command_text.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if cmd in ("/员工", "/list", "/team"):
            if not profiles:
                return GatewayDispatchResult(is_command=True, command_response="当前机器人未挂载任何数字员工。")
            lines = ["📋 当前机器人挂载的数字员工列表："]
            for p in profiles:
                is_cur = " (当前服务)" if p.id == identity.active_profile_id else ""
                lines.append(f"• [{p.employee_badge}] {p.display_name} - {p.job_title}{is_cur}")
            lines.append("\n💡 提示：输入 `/切换 <姓名或工号>` 可直接切换服务员工。")
            return GatewayDispatchResult(is_command=True, command_response="\n".join(lines))

        elif cmd in ("/切换", "/switch"):
            if not args:
                return GatewayDispatchResult(is_command=True, command_response="格式错误。请使用：`/切换 <姓名或工号>`")
            target = args[0]
            force = len(args) > 1 and args[1].lower() in ("force", "-f", "强制")

            # 检查 SOP 保护窗
            now_dt = utcnow()
            if identity.sop_window_locked_until and identity.sop_window_locked_until > now_dt and not force:
                return GatewayDispatchResult(
                    is_command=True,
                    command_response=(
                        "⚠️ 当前员工正在执行关键业务规程（处于 SOP 保护窗内），暂无法直接切换。\n"
                        "如需强制中断流程并切换，请输入：`/切换 " + target + " force` 或先输入 `/重置` 退出流程。"
                    ),
                )

            # 查找目标员工
            matched = next(
                (p for p in profiles if target.lower() in p.display_name.lower() or target.lower() in p.employee_badge.lower()),
                None,
            )
            if not matched:
                return GatewayDispatchResult(
                    is_command=True,
                    command_response=f"未找到匹配的数字员工「{target}」，输入 `/员工` 可查看名单。",
                )

            identity.active_profile_id = matched.id
            identity.sop_window_locked_until = None
            identity.active_flow_id = None
            await db.commit()

            return GatewayDispatchResult(
                is_command=True,
                command_response=f"✅ 已为您切换至数字员工：【{matched.display_name}】（{matched.job_title} / {matched.employee_badge}），很高兴为您服务！",
                dispatched_profile_id=matched.id,
                dispatched_profile_name=matched.display_name,
                dispatched_profile_badge=matched.employee_badge,
            )

        elif cmd in ("/当前", "/whoami"):
            active = next((p for p in profiles if p.id == identity.active_profile_id), None)
            if not active:
                return GatewayDispatchResult(is_command=True, command_response="您当前尚未绑定指定员工，系统将根据问题自动分发。")
            is_locked = bool(identity.sop_window_locked_until and identity.sop_window_locked_until > utcnow())
            lock_info = f" [SOP 保护窗生效中，规程: {identity.active_flow_id}]" if is_locked else ""
            return GatewayDispatchResult(
                is_command=True,
                command_response=f"👤 当前为您服务的数字员工：【{active.display_name}】\n工号：{active.employee_badge}\n岗位：{active.job_title}{lock_info}",
            )

        elif cmd in ("/重置", "/退出", "/reset", "/exit"):
            identity.sop_window_locked_until = None
            identity.active_flow_id = None
            await db.commit()
            return GatewayDispatchResult(
                is_command=True,
                command_response="🔄 已重置会话并退出当前 SOP 业务流程保护窗。您可以输入新问题或输入 `/员工` 重新选择。",
            )

        elif cmd in ("/绑定", "/bind"):
            if not args:
                return GatewayDispatchResult(
                    is_command=True,
                    command_response="请提供一次性绑定验证码。用法：`/绑定 <码>`",
                )
            # AUD-08：绑定码必须是服务端签发的一次性凭据，而不是"任意 6 位字符串"。
            try:
                record = await consume_bind_token(db, raw_token=args[0], identity=identity)
            except BindTokenError as exc:
                logger.info("渠道绑定码校验失败: %s", exc.reason)
                return GatewayDispatchResult(is_command=True, command_response=f"❌ {exc.reason}")

            logger.info(
                "渠道身份绑定成功: enterprise=%s channel=%s external=%s user=%s",
                identity.enterprise_id,
                identity.channel_type,
                identity.external_user_id,
                record.internal_user_id,
            )
            return GatewayDispatchResult(
                is_command=True,
                command_response="🎉 绑定成功！您的渠道账号已与内部企业身份联通，专享知识库与流程权限已解锁。",
            )

        elif cmd in ("/帮助", "/help"):
            help_text = (
                "🤖 AutoTeams 4.0 数字员工交互指令指南：\n"
                "• `/员工` - 列出当前机器人挂载的所有数字员工\n"
                "• `/切换 <姓名/工号>` - 切换当前接待员工\n"
                "• `/当前` - 查看当前为您服务的数字员工及 SOP 状态\n"
                "• `/重置` - 重置会话与中断退出正在执行的流程\n"
                "• `/绑定 <码>` - 绑定内部员工工号与企业认证权限\n"
                "• `/帮助` - 查看此帮助说明"
            )
            return GatewayDispatchResult(is_command=True, command_response=help_text)

        return GatewayDispatchResult(
            is_command=True,
            command_response=f"未知指令「{cmd}」，输入 `/帮助` 查看可用指令。",
        )

    @classmethod
    def _route_intent(
        cls,
        text: str,
        profiles: List[WorkforceProfile],
        default_profile_id: Optional[str] = None,
    ) -> Optional[WorkforceProfile]:
        """轻量意图分类匹配。根据职责边界（allowed）与岗位关键词匹配最合适的数字员工。"""
        if not profiles:
            return None

        text_lower = text.lower()
        scored_profiles: List[Tuple[WorkforceProfile, int]] = []

        for p in profiles:
            score = 0
            # 1. 匹配岗位名称
            if p.job_title.lower() in text_lower:
                score += 5
            # 2. 匹配姓名
            if p.display_name.lower() in text_lower:
                score += 8
            # 3. 匹配职责边界中明确 allowed 的事项
            duty = p.duty_boundaries or {}
            allowed = duty.get("allowed", [])
            for item in allowed:
                if item.lower() in text_lower:
                    score += 10
            # 4. 严禁 forbidden 事项扣分
            forbidden = duty.get("forbidden", [])
            for item in forbidden:
                if item.lower() in text_lower:
                    score -= 20

            scored_profiles.append((p, score))

        scored_profiles.sort(key=lambda x: x[1], reverse=True)
        best_p, best_score = scored_profiles[0]

        if best_score > 0:
            return best_p

        # 降级回退到默认员工
        if default_profile_id:
            default_p = next((p for p in profiles if p.id == default_profile_id), None)
            if default_p:
                return default_p

        return scored_profiles[0][0]

    @classmethod
    async def lock_sop_protection_window(
        cls,
        db: AsyncSession,
        identity_id: str,
        flow_id: str,
        duration_seconds: int = 600,
    ) -> bool:
        """为正在执行关键 SOP 的会话加锁保护窗。"""
        stmt = select(ChannelIdentity).where(ChannelIdentity.id == identity_id)
        res = await db.execute(stmt)
        identity = res.scalar_one_or_none()
        if not identity:
            return False

        identity.active_flow_id = flow_id
        identity.sop_window_locked_until = utcnow() + timedelta(seconds=duration_seconds)
        await db.commit()
        logger.info(f"身份 {identity_id} 已锁定 SOP 保护窗: {flow_id}, 时长: {duration_seconds}s")
        return True

    @classmethod
    async def unlock_sop_protection_window(
        cls,
        db: AsyncSession,
        identity_id: str,
    ) -> bool:
        """解锁 SOP 保护窗。"""
        stmt = select(ChannelIdentity).where(ChannelIdentity.id == identity_id)
        res = await db.execute(stmt)
        identity = res.scalar_one_or_none()
        if not identity:
            return False

        identity.sop_window_locked_until = None
        identity.active_flow_id = None
        await db.commit()
        return True
