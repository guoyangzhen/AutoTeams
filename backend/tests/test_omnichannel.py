"""AutoTeams 4.0 Omnichannel 全渠道网关、意图路由与 SOP 保护窗单元测试。"""
import pytest
import uuid
from app.models.enterprise import Enterprise
from app.models.channel_account import ChannelAccount, ChannelIdentity
from app.models.workforce import WorkforceProfile
from app.services.connectors.gateway import (
    DeduplicationCache,
    InboundMessage,
    OmnichannelGateway,
)
from app.models.user import User
from app.services.connectors.bind_tokens import issue_bind_token
from sqlalchemy import select

from app.services.connectors.wecom_adapter import WeComAdapter
from app.services.connectors.feishu_adapter import FeishuAdapter


def test_deduplication_cache():
    """测试入站消息幂等去重。"""
    cache = DeduplicationCache(ttl_seconds=5)
    msg_key = "wecom:msg_unique_123"
    assert cache.is_duplicate(msg_key) is False
    assert cache.is_duplicate(msg_key) is True
    assert cache.is_duplicate("wecom:msg_other_456") is False


def test_adapters_parsing_and_replies():
    """测试企微与飞书协议适配解析与响应报文构建。"""
    # 1. 企微文本解析
    wecom_raw = {
        "MsgType": "text",
        "MsgId": "wecom_test_001",
        "FromUserName": "wx_user_zhangsan",
        "FromNick": "张三",
        "Text": {"Content": "请帮我查一下上月报销进度"},
    }
    inbound_wecom = WeComAdapter.parse_webhook_payload("acc-1", wecom_raw)
    assert inbound_wecom.message_id == "wecom_test_001"
    assert inbound_wecom.external_user_id == "wx_user_zhangsan"
    assert inbound_wecom.content == "请帮我查一下上月报销进度"

    wecom_reply = WeComAdapter.build_text_response("处理完毕")
    assert wecom_reply["msgtype"] == "text"
    assert wecom_reply["text"]["content"] == "处理完毕"

    # 2. 飞书 Challenge 挑战应答
    challenge_payload = {"challenge": "feishu_token_check_xyz"}
    challenge_res = FeishuAdapter.handle_challenge(challenge_payload)
    assert challenge_res == {"challenge": "feishu_token_check_xyz"}

    # 3. 飞书文本事件解析
    feishu_raw = {
        "header": {"event_id": "feishu_evt_999"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_lisi_123"}},
            "message": {
                "message_type": "text",
                "content": '{"text": "系统支持多Agent协作吗？"}',
            },
        },
    }
    inbound_feishu = FeishuAdapter.parse_event_payload("acc-2", feishu_raw)
    assert inbound_feishu is not None
    assert inbound_feishu.external_user_id == "ou_lisi_123"
    assert inbound_feishu.content == "系统支持多Agent协作吗？"


@pytest.mark.asyncio
async def test_omnichannel_commands_and_routing(db_session):
    """测试渠道内置交互指令与智能意图路由分发。"""
    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    ent = Enterprise(id=ent_id, name="全渠道科技")
    db_session.add(ent)

    # 1. 创建两名具备不同职责的数字员工
    p_sales = WorkforceProfile(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        employee_badge="ATE-2026-SALES-001",
        display_name="苏策",
        job_title="售前解决方案顾问",
        duty_boundaries={"allowed": ["产品报价咨询", "架构方案设计", "价格谈判"], "forbidden": []},
    )
    p_tech = WorkforceProfile(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        employee_badge="ATE-2026-TECH-002",
        display_name="林工",
        job_title="技术支持工程师",
        duty_boundaries={"allowed": ["API接口排障", "服务器运维", "错误日志排查"], "forbidden": []},
    )
    db_session.add_all([p_sales, p_tech])

    # 2. 创建企业微信渠道账号，挂载上述两名员工
    account = ChannelAccount(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        channel_type="wecom_bot",
        name="办公助手企微群机器人",
        mounted_profile_ids=[p_sales.id, p_tech.id],
        default_profile_id=p_sales.id,
        status="connected",
        is_active=True,
    )
    db_session.add(account)
    await db_session.commit()

    # 3. 测试指令：/员工 (查看挂载名单)
    msg_list = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_wx_001",
        content="/员工",
    )
    res_list = await OmnichannelGateway.process_inbound_message(db_session, msg_list)
    assert res_list.is_command is True
    assert "苏策" in res_list.command_response
    assert "林工" in res_list.command_response

    # 4. 测试意图自动分发：技术问题自动分发给「林工」
    msg_tech = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_wx_001",
        content="生产环境出现500错误日志排查需求",
    )
    res_tech = await OmnichannelGateway.process_inbound_message(db_session, msg_tech)
    assert res_tech.is_command is False
    assert res_tech.dispatched_profile_id == p_tech.id
    assert res_tech.dispatched_profile_name == "林工"

    # 5. 测试指令切换：/切换 苏策
    msg_switch = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_wx_001",
        content="/切换 苏策",
    )
    res_switch = await OmnichannelGateway.process_inbound_message(db_session, msg_switch)
    assert res_switch.is_command is True
    assert "已为您切换至数字员工：【苏策】" in res_switch.command_response
    assert res_switch.dispatched_profile_id == p_sales.id

    # 6. 测试当前状态指令：/当前
    msg_current = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_wx_001",
        content="/当前",
    )
    res_cur = await OmnichannelGateway.process_inbound_message(db_session, msg_current)
    assert res_cur.is_command is True
    assert "苏策" in res_cur.command_response

    # 7. 伪造绑定码必须失败（AUD-08）：服务端只认自己签发的一次性绑定码
    msg_forge = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_wx_001",
        content="/绑定 A1B2C3",
    )
    res_forge = await OmnichannelGateway.process_inbound_message(db_session, msg_forge)
    assert res_forge.is_command is True
    assert "绑定成功" not in res_forge.command_response

    identity = (
        await db_session.execute(
            select(ChannelIdentity).where(ChannelIdentity.external_user_id == "user_wx_001")
        )
    ).scalar_one()
    assert identity.is_bound is False
    assert identity.internal_user_id is None

    # 8. 使用服务端真实签发的绑定码完成绑定
    internal_user = User(
        id=str(uuid.uuid4()),
        email=f"bind-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        name="渠道用户",
        role="member",
        enterprise_id=ent_id,
    )
    db_session.add(internal_user)
    await db_session.commit()
    issued = await issue_bind_token(
        db_session, enterprise_id=ent_id, internal_user_id=internal_user.id
    )

    msg_bind = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_wx_001",
        content=f"/绑定 {issued.token}",
    )
    res_bind = await OmnichannelGateway.process_inbound_message(db_session, msg_bind)
    assert res_bind.is_command is True
    assert "绑定成功" in res_bind.command_response

    await db_session.refresh(identity)
    assert identity.is_bound is True
    assert identity.internal_user_id == internal_user.id

    # 9. 绑定码一次性：同一个码不能被第二个外部用户重复使用
    msg_replay = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_wx_002",
        content=f"/绑定 {issued.token}",
    )
    res_replay = await OmnichannelGateway.process_inbound_message(db_session, msg_replay)
    assert "绑定成功" not in res_replay.command_response


@pytest.mark.asyncio
async def test_sop_protection_window(db_session):
    """测试 SOP 保护窗生效时会话粘性防抢单与切换拦截。"""
    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    ent = Enterprise(id=ent_id, name="规程保护科技")
    db_session.add(ent)

    p_finance = WorkforceProfile(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        employee_badge="ATE-2026-FIN-001",
        display_name="赵财务",
        job_title="财务主管",
        duty_boundaries={"allowed": ["对公转账复核", "开票报销审批"], "forbidden": []},
    )
    p_tech = WorkforceProfile(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        employee_badge="ATE-2026-TECH-003",
        display_name="林工",
        job_title="技术支持",
        duty_boundaries={"allowed": ["API排障"], "forbidden": []},
    )
    db_session.add_all([p_finance, p_tech])

    account = ChannelAccount(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        channel_type="wecom_bot",
        name="财务服务台",
        mounted_profile_ids=[p_finance.id, p_tech.id],
        default_profile_id=p_finance.id,
        status="connected",
        is_active=True,
    )
    db_session.add(account)
    await db_session.commit()

    # 初始化身份并锁定 SOP 保护窗（执行开票流程）
    identity = await OmnichannelGateway.get_or_create_identity(
        db=db_session,
        enterprise_id=ent_id,
        channel_type="wecom_bot",
        external_user_id="user_finance_vip",
    )
    identity.active_profile_id = p_finance.id
    await db_session.commit()

    await OmnichannelGateway.lock_sop_protection_window(
        db=db_session,
        identity_id=identity.id,
        flow_id="flow-invoice-approval-v1",
        duration_seconds=300,
    )

    # 1. 用户即使输入包含技术关键词的文本，仍被 SOP 保护窗牢牢锁定在「赵财务」
    msg_input = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_finance_vip",
        content="税号是 91110000XXXXXX，顺便帮看下 API 是否正常",
    )
    res_input = await OmnichannelGateway.process_inbound_message(db_session, msg_input)
    assert res_input.is_sop_protected is True
    assert res_input.dispatched_profile_id == p_finance.id
    assert res_input.active_flow_id == "flow-invoice-approval-v1"

    # 2. 用户在保护窗生效期尝试普通切换，被安全拦截并提示
    msg_switch = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_finance_vip",
        content="/切换 林工",
    )
    res_switch = await OmnichannelGateway.process_inbound_message(db_session, msg_switch)
    assert res_switch.is_command is True
    assert "SOP 保护窗" in res_switch.command_response
    assert "无法直接切换" in res_switch.command_response

    # 3. 用户使用 force 强制切换
    msg_force_switch = InboundMessage(
        channel_type="wecom_bot",
        account_id=account.id,
        message_id=str(uuid.uuid4()),
        external_user_id="user_finance_vip",
        content="/切换 林工 force",
    )
    res_force = await OmnichannelGateway.process_inbound_message(db_session, msg_force_switch)
    assert res_force.is_command is True
    assert "已为您切换至数字员工：【林工】" in res_force.command_response
    assert res_force.dispatched_profile_id == p_tech.id
