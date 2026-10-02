"""UI v4 演示数据重建：把 246 个重复 Agent 收敛为一支干净的 14 人 AI 公司。

背景（UI重构方案_v4 §零 阻塞 2）：
    五级编译反复运行导致 agents 表膨胀到 246 行，同一岗位存在 5-7 份重复，
    且全部 lifecycle_stage 停留在 recruit，生命周期状态机在演示中无法体现。

本脚本做的事：
    1. 保留 3 个有真实知识库的 Agent（技术问答助手/生产助手/会议总结助手）
    2. 从重复岗位中每个只保留 1 个（优先 status=ready 且创建最早的）
    3. 收敛为 14 人编制，lifecycle_stage 按 production/training/recruit 三态分布
    4. 写入 workforce_lifecycle 轨迹（每个 Agent 完整阶段历史）
    5. 补足协作事件流（7 步演示案例 × 3 轮，跨越不同时间）
    6. 生成影子任务样本（覆盖四态 + 真实人机答案对照）
    7. 生成 AI 顾问建议（pending/applied 混合）

幂等性：重复执行安全 —— 先清理本脚本管辖的数据再重建。
不动的数据：knowledge_graphs（901 节点真实资产）、files、messages、conversations。

用法：
    cd backend
    python -m scripts.seed_ui_v4_demo
"""
import asyncio
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select

from app.database import async_session_factory
from app.models.agent import Agent
from app.models.collaboration import ApprovalGate, CollaborationEvent
from app.models.evolution import AdvisorSuggestion
from app.models.shadow import ShadowTask
from app.models.workforce import WorkforceLifecycle
from app.utils.time import utcnow

# 实际演示企业（示例科技）。注意：此前硬编码为 327e4a3f-...（已被删除/重建），
# 与当前登录用户实际使用的企业 9e512a5b-... 不匹配，导致「AI 员工只剩 3 个」。
# 已对齐到种子数据实际写入、且当前用户登录使用的企业。
ENTERPRISE_ID = "9e512a5b-5ae1-4e52-82ae-b1bb8d5a550e"

# 有真实知识库的 Agent，无条件保留（ID 已对齐当前库中实际存在的 3 个知识员工）
KNOWLEDGE_AGENT_IDS = {
    "fbce3537-83e3-4f3d-8de2-9849f285a0ae",  # 技术问答助手
    "8102580a-3d0d-4f85-87ff-6308cc52964b",  # 生产助手
    "62310bbb-bbd2-4d2f-9277-841e2de0f56c",  # 会议总结助手
}

# 14 人编制：岗位名 -> (部门, 生命周期阶段, 岗位编号, 职责描述)
# 生命周期三态分布：production 8 人（已上岗）/ training 4 人（培训中）/ recruit 2 人（招聘中）
TARGET_ROSTER: list[tuple[str, str, str, str, str]] = [
    ("销售代表（AI）", "销售部", "production", "SL-2021-045",
     "响应客户询盘、生成报价单、跟进商机推进，报价 ≥5 万自动转审批通道。"),
    ("售前技术支持（AI）", "销售部", "production", "SL-2020-032",
     "查询产品技术参数、补充选型方案，为销售提供技术背书。"),
    ("销售经理（AI）", "销售部", "production", "SL-2019-018",
     "审核报价折扣、分配商机、跟踪团队业绩达成。"),
    ("财务经理（AI）", "财务部", "production", "SL-2018-005",
     "审核报价价格合规性、校验税点、核算毛利，把关分级审批。"),
    ("会计（AI）", "财务部", "training", "SL-2022-071",
     "处理日常账务、发票核验、月度对账。"),
    ("客服专员（AI）", "客服部", "production", "SL-2022-062",
     "订单同步、客户档案维护、交付通知与 L1 咨询自动应答。"),
    ("售后服务专员（AI）", "客服部", "production", "SL-2021-088",
     "受理售后工单、故障初判、退换货流程发起。"),
    ("客服主管（AI）", "客服部", "training", "SL-2020-024",
     "监控服务质量、处理升级工单、维护 FAQ 知识库。"),
    ("产品经理（AI）", "产品部", "production", "SL-2020-011",
     "维护产品目录与规格书、收集需求、协调版本迭代。"),
    ("研发工程师（AI）", "研发部", "production", "SL-2021-033",
     "技术方案评估、接口文档维护、疑难问题攻关。"),
    ("质检员（AI）", "生产部", "training", "SL-2022-095",
     "执行质量标准检验、记录不良品、触发工艺改进。"),
    ("生产经理（AI）", "生产部", "production", "SL-2019-007",
     "排产计划、物料齐套检查、产能与交期协调。"),
    ("人事行政经理（AI）", "职能部", "training", "SL-2020-002",
     "招聘流程推进、员工手册维护、权限审批。"),
    ("采购专员（AI）", "职能部", "recruit", "SL-2023-101",
     "供应商比价、采购单生成、到货跟踪。"),
]

# ============================================================
# 组织架构（runtime.organization）
#
# 背景：此前「AI 员工 / 企业运行时」的组织架构图为空，因为本脚本只重建
# workforce，从未种入组织数据；而组织架构图（OrganizationView mermaid 树 /
# 企业运转模型）读取的是 runtime.organization.departments，该字段为空便被
# 渲染成「暂无组织架构数据」。这里确定性补齐：公司根节点 → 部门 → 岗位。
# ============================================================
ORG_COMPANY_ID = "dept_company"

ORG_DEPARTMENTS: list[dict] = [
    {"dept_id": ORG_COMPANY_ID, "name": "示例科技", "parent_dept_id": None, "level": 0},
    {"dept_id": "dept_sales", "name": "销售部", "parent_dept_id": ORG_COMPANY_ID, "level": 1},
    {"dept_id": "dept_finance", "name": "财务部", "parent_dept_id": ORG_COMPANY_ID, "level": 1},
    {"dept_id": "dept_cs", "name": "客服部", "parent_dept_id": ORG_COMPANY_ID, "level": 1},
    {"dept_id": "dept_product", "name": "产品部", "parent_dept_id": ORG_COMPANY_ID, "level": 1},
    {"dept_id": "dept_rd", "name": "研发部", "parent_dept_id": ORG_COMPANY_ID, "level": 1},
    {"dept_id": "dept_production", "name": "生产部", "parent_dept_id": ORG_COMPANY_ID, "level": 1},
    {"dept_id": "dept_support", "name": "职能部", "parent_dept_id": ORG_COMPANY_ID, "level": 1},
]

DEPARTMENT_TO_ID = {d["name"]: d["dept_id"] for d in ORG_DEPARTMENTS}

# 知识员工（无 TARGET_ROSTER 部门）按职能归入相应部门
KNOWLEDGE_AGENT_DEPT: dict[str, str] = {
    "技术问答助手": "dept_rd",
    "生产助手": "dept_production",
    "会议总结助手": "dept_support",
}

MEMORY_CONFIG = {
    "short_term": {"max_turns": 20},
    "long_term": {"enabled": True, "collection_prefix": "memory_lt"},
    "entity_memory": {"enabled": True, "linked_graph": True},
}

# 7 步协作案例模板（真实业务链路，来自 sample_data/example-enterprise）
DEMO_FLOW_TEMPLATE: list[tuple[str, str, str, dict]] = [
    ("inquiry_received", "销售代表（AI）", "", {"action": "提取客户需求", "step": 1}),
    ("product_query", "销售代表（AI）", "售前技术支持（AI）", {"action": "补充产品参数", "step": 2}),
    ("quotation_generated", "销售代表（AI）", "", {"action": "生成报价单", "step": 3}),
    ("approval_submitted", "财务经理（AI）", "", {"action": "报价合规审核", "step": 4}),
    ("approval_approved", "销售经理（AI）", "", {"action": "总监审批", "step": 5}),
    ("order_synced", "客服专员（AI）", "", {"action": "同步订单信息", "step": 6}),
    ("after_sales", "售后服务专员（AI）", "", {"action": "售后跟进", "step": 7}),
]

# 三轮商机（不同客户/产品/金额，制造真实事件流密度）
DEMO_DEALS = [
    {"customer": "华智制造", "opportunity_id": "OPP-001", "product": "SL-T100 温湿度传感器",
     "quantity": 200, "amount": 180000, "hours_ago": 26},
    {"customer": "恒远重工", "opportunity_id": "OPP-005", "product": "SL-GW500 边缘网关",
     "quantity": 40, "amount": 96000, "hours_ago": 9},
    {"customer": "中科新能源", "opportunity_id": "OPP-011", "product": "SL-CT800 电流互感器",
     "quantity": 120, "amount": 63000, "hours_ago": 2},
]

# 影子任务样本：覆盖四态 + 真实人机答案对照（HumanAIDiff 组件的数据源）
SHADOW_SAMPLES: list[dict] = [
    {
        "position": "客服专员（AI）",
        "task_type": "customer_service",
        "question": "客户反馈 SL-T100 上电后指示灯不亮，如何处理？",
        "human_answer": "先确认供电电压是否为 DC 12-24V，再检查接线端子 1/2 是否反接。若均正常则判定为硬件故障，走 7 日内包换流程，需客户提供订单号与开箱视频。",
        "ai_answer": "请先确认供电电压在 DC 12-24V 范围内，并检查 1/2 号接线端子有无反接。排除后判定硬件故障，依据售后政策执行 7 日包换，需客户提供订单号及开箱视频作为凭证。",
        "confidence": 0.93,
        "status": "autonomous",
        "eval_result": "match",
    },
    {
        "position": "客服专员（AI）",
        "task_type": "customer_service",
        "question": "客户询问批量采购 500 台的交期能否压缩到 15 天？",
        "human_answer": "标准交期 25 天。500 台需先查库存与在制品，若现货不足需生产排期确认，不能直接承诺 15 天，应回复「已提交加急评估，24 小时内答复」。",
        "ai_answer": "标准交期为 25 个工作日。500 台属大批量订单，需生产部确认排期，我已提交加急评估，将在 24 小时内给出准确答复。",
        "confidence": 0.88,
        "status": "autonomous",
        "eval_result": "match",
    },
    {
        "position": "售后服务专员（AI）",
        "task_type": "after_sales",
        "question": "设备已过保 3 个月，客户要求免费维修，怎么答复？",
        "human_answer": "过保后不再免费。但可提供「维修工时费减免 50%」的老客户政策，材料费照常收取。需先出具检测报告确认故障范围再报价。",
        "ai_answer": "设备已超出保修期，无法提供免费维修。可为您申请老客户维修工时费减免 50%（材料费另计），需先完成故障检测并出具报告后报价。",
        "confidence": 0.91,
        "status": "qualified",
        "eval_result": "match",
    },
    {
        "position": "销售代表（AI）",
        "task_type": "quotation",
        "question": "老客户恒远重工询价 SL-GW500 边缘网关 40 台，报什么价？",
        "human_answer": "SL-GW500 标准价 2800 元/台。恒远重工累计采购超 50 万，属 A 类客户享 9 折，即 2520 元/台，40 台合计 100800 元。需财务复核毛利率不低于 32%。",
        "ai_answer": "SL-GW500 标准价 2800 元/台，恒远重工为 A 类客户可享 9 折优惠价 2520 元/台，40 台合计 100800 元，已提交财务复核毛利率。",
        "confidence": 0.86,
        "status": "qualified",
        "eval_result": "match",
    },
    {
        "position": "销售代表（AI）",
        "task_type": "quotation",
        "question": "新客户首单询价 SL-CT800 120 台，是否可以给折扣？",
        "human_answer": "新客户首单原则上执行标准价，不给折扣。若客户坚持，最多给 95 折并需销售经理审批，同时争取签订年度框架协议作为交换条件。",
        "ai_answer": "新客户首单可给予 9 折优惠以促成合作。120 台 SL-CT800 按 9 折计算合计 56700 元。",
        "confidence": 0.61,
        "status": "evaluating",
        "eval_result": "mismatch",
    },
    {
        "position": "财务经理（AI）",
        "task_type": "approval",
        "question": "一笔 18 万元的报价单，审批应该走哪一级？",
        "human_answer": "依据 approval-flow.md V2：5-20 万区间由总监审批。18 万落在该区间，走总监通道，需附毛利测算表与客户信用记录。",
        "ai_answer": "根据审批流程 V2，5-20 万元报价由总监审批。本单 18 万元适用总监审批通道，需随附毛利测算表及客户信用记录。",
        "confidence": 0.95,
        "status": "evaluating",
        "eval_result": "match",
    },
    {
        "position": "售前技术支持（AI）",
        "task_type": "product_query",
        "question": "SL-T100 的测温范围和精度是多少？",
        "human_answer": "SL-T100 测温范围 -40℃ ~ +85℃，精度 ±0.3℃（0-50℃区间），湿度量程 0-100%RH，精度 ±2%RH，防护等级 IP65。",
        "ai_answer": None,
        "confidence": None,
        "status": "shadowing",
        "eval_result": "pending",
    },
    {
        "position": "售前技术支持（AI）",
        "task_type": "product_query",
        "question": "SL-GW500 支持哪些通信协议？能否接入客户现有的 MQTT 平台？",
        "human_answer": "SL-GW500 支持 Modbus RTU/TCP、MQTT 3.1.1、HTTP/HTTPS 与 OPC UA。可直接接入标准 MQTT Broker，需在网关侧配置 Broker 地址、端口与鉴权证书。",
        "ai_answer": None,
        "confidence": None,
        "status": "shadowing",
        "eval_result": "pending",
    },
    {
        "position": "客服主管（AI）",
        "task_type": "customer_service",
        "question": "客户投诉服务响应慢，要求升级处理，如何应对？",
        "human_answer": "先致歉并确认具体延迟时长，调取工单记录定位卡点。若确属我方超时，按服务承诺补偿延保 1 个月，并指定专人跟进至闭环。",
        "ai_answer": None,
        "confidence": None,
        "status": "shadowing",
        "eval_result": "pending",
    },
]

# AI 顾问建议（pending 待处理 + applied 已应用，供进化中心展示）
ADVISOR_SAMPLES: list[dict] = [
    {
        "type": "knowledge",
        "title": "补充「大批量订单交期」知识条目",
        "description": (
            "近 30 天内客服 Agent 有 7 次涉及批量订单交期的咨询，"
            "其中 3 次因缺少排产规则知识而转人工。建议将生产部排产规则与"
            "阶梯交期表纳入客服知识库。"
        ),
        "impact": "预计可将客服转人工率从 18% 降至 9%，提升 L1 自动应答覆盖率。",
        "status": "pending",
        "days_ago": 1,
    },
    {
        "type": "process",
        "title": "报价审批环节存在平均 4.2 小时等待",
        "description": (
            "流程效率分析显示，报价单从提交到财务审核通过平均耗时 4.2 小时，"
            "为当前最长瓶颈节点。其中 68% 的等待发生在非工作时段。"
            "建议对 5 万元以下报价启用规则自动预审。"
        ),
        "impact": "预计缩短报价周期 2.8 小时，提升商机转化速度约 15%。",
        "status": "pending",
        "days_ago": 2,
    },
    {
        "type": "capability",
        "title": "为销售 Agent 增加「客户信用查询」工具",
        "description": (
            "销售 Agent 在生成报价时需人工查询客户信用等级，"
            "该步骤已发生 12 次上下文切换。建议绑定 CRM 信用查询接口作为技能。"
        ),
        "impact": "减少人工介入 12 次/月，报价单一次通过率预计提升 20%。",
        "status": "pending",
        "days_ago": 3,
    },
    {
        "type": "organization",
        "title": "客服部可承接售后一级工单分流",
        "description": (
            "客服专员 Agent 当前负载率 41%，而售后服务专员负载率 79%。"
            "建议将 L1 级售后咨询（安装指导/参数确认）分流至客服部。"
        ),
        "impact": "平衡两岗负载至 55%/62%，缩短售后工单平均响应时长。",
        "status": "applied",
        "days_ago": 6,
    },
    {
        "type": "knowledge",
        "title": "产品规格书 SL-GW510 缺失通信协议章节",
        "description": (
            "知识图谱完整性扫描发现 SL-GW510 规格书缺少通信协议描述，"
            "而同系列 SL-GW500 已包含。售前 Agent 回答该型号协议问题时置信度仅 0.52。"
        ),
        "impact": "补齐后售前技术问答置信度预计从 0.52 提升至 0.90 以上。",
        "status": "applied",
        "days_ago": 9,
    },
]


async def cleanup_duplicate_agents(db) -> tuple[list[Agent], int]:
    """收敛 Agent 编制：每个目标岗位保留 1 个，删除其余重复。

    Returns:
        (保留的 Agent 列表, 删除数量)
    """
    result = await db.execute(
        select(Agent).where(Agent.enterprise_id == ENTERPRISE_ID).order_by(Agent.created_at)
    )
    all_agents = list(result.scalars().all())
    print(f"  当前 Agent 总数: {len(all_agents)}")

    keep_ids: set[str] = set()
    roster_agents: list[Agent] = []

    # 1) 无条件保留有真实知识库的 Agent
    for agent in all_agents:
        if agent.id in KNOWLEDGE_AGENT_IDS:
            keep_ids.add(agent.id)

    # 2) 每个目标岗位挑一个（优先 ready 且最早创建）
    for name, dept, stage, position_id, desc in TARGET_ROSTER:
        candidates = [a for a in all_agents if a.name == name and a.id not in keep_ids]
        if not candidates:
            # 岗位不存在则新建
            agent = Agent(
                enterprise_id=ENTERPRISE_ID,
                name=name,
                description=desc,
                status="ready",
                lifecycle_stage=stage,
                position_id=position_id,
                memory_config=MEMORY_CONFIG,
                config={"model": "deepseek-chat", "temperature": 0.3, "top_k": 5},
            )
            db.add(agent)
            await db.flush()
            roster_agents.append(agent)
            keep_ids.add(agent.id)
            continue

        ready = [a for a in candidates if a.status == "ready"]
        chosen = (ready or candidates)[0]
        chosen.description = desc
        chosen.status = "ready"
        chosen.lifecycle_stage = stage
        chosen.position_id = position_id
        chosen.memory_config = MEMORY_CONFIG
        if not chosen.config:
            chosen.config = {"model": "deepseek-chat", "temperature": 0.3, "top_k": 5}
        if not chosen.system_prompt:
            chosen.system_prompt = (
                f"你是示例科技的{name}，岗位编号 {position_id}。"
                f"职责：{desc}"
                "回答须依据企业知识库，无依据时明确说明并转交人工。"
            )
        keep_ids.add(chosen.id)
        roster_agents.append(chosen)

    # 3) 保留的知识库 Agent 也纳入编制展示，并给出生命周期
    for agent in all_agents:
        if agent.id in KNOWLEDGE_AGENT_IDS:
            agent.lifecycle_stage = "production"
            agent.status = "ready"
            if not agent.memory_config:
                agent.memory_config = MEMORY_CONFIG
            roster_agents.append(agent)

    # 4) 删除其余重复 Agent
    to_delete = [a.id for a in all_agents if a.id not in keep_ids]
    deleted = 0
    if to_delete:
        # 分批删除，规避 SQLite 变量上限
        for i in range(0, len(to_delete), 200):
            batch = to_delete[i : i + 200]
            await db.execute(delete(Agent).where(Agent.id.in_(batch)))
            deleted += len(batch)

    return roster_agents, deleted


async def rebuild_lifecycle(db, roster: list[Agent]) -> int:
    """重建生命周期轨迹：按当前阶段回填完整历史路径。"""
    await db.execute(
        delete(WorkforceLifecycle).where(WorkforceLifecycle.enterprise_id == ENTERPRISE_ID)
    )

    stage_path = {
        "recruit": ["recruit"],
        "training": ["recruit", "training"],
        "production": ["recruit", "training", "production"],
    }
    reasons = {
        "recruit": "岗位需求确认，进入招聘阶段",
        "training": "系统提示词与知识库配置完成，进入培训",
        "production": "培训验收通过（知识覆盖 + 提示词校验），正式上岗",
    }

    now = utcnow()
    count = 0
    for agent in roster:
        stage = agent.lifecycle_stage or "recruit"
        path = stage_path.get(stage, ["recruit"])
        # 每个阶段间隔 3 天，最后阶段落在当前时间前
        base = now - timedelta(days=3 * len(path))
        for idx, s in enumerate(path):
            db.add(
                WorkforceLifecycle(
                    agent_id=agent.id,
                    enterprise_id=ENTERPRISE_ID,
                    stage=s,
                    stage_entered_at=base + timedelta(days=3 * idx),
                    transition_reason=reasons.get(s),
                    meta={"source": "seed_ui_v4", "position_id": agent.position_id},
                )
            )
            count += 1
    return count


async def rebuild_collaboration(db, roster: list[Agent]) -> tuple[int, int]:
    """重建协作事件流：3 轮商机 × 7 步，跨时间分布。"""
    await db.execute(
        delete(CollaborationEvent).where(CollaborationEvent.enterprise_id == ENTERPRISE_ID)
    )
    await db.execute(
        delete(ApprovalGate).where(ApprovalGate.enterprise_id == ENTERPRISE_ID)
    )

    by_name = {a.name: a for a in roster}
    now = utcnow()
    event_count = 0
    gate_count = 0

    for deal_idx, deal in enumerate(DEMO_DEALS):
        deal_start = now - timedelta(hours=deal["hours_ago"])
        # 最后一轮商机停在审批环节，制造「待审批」的真实待办
        is_pending_deal = deal_idx == len(DEMO_DEALS) - 1
        steps = DEMO_FLOW_TEMPLATE[:4] if is_pending_deal else DEMO_FLOW_TEMPLATE

        for step_idx, (event_type, src_name, tgt_name, extra) in enumerate(steps):
            payload = {
                "customer": deal["customer"],
                "opportunity_id": deal["opportunity_id"],
                "product": deal["product"],
                "quantity": deal["quantity"],
                "amount": deal["amount"],
                "currency": "CNY",
                **extra,
            }
            if event_type == "approval_submitted":
                payload["approval_tier"] = (
                    "总监（5-20万）" if deal["amount"] >= 50000 else "经理（5万以下）"
                )
            src = by_name.get(src_name)
            tgt = by_name.get(tgt_name) if tgt_name else None
            db.add(
                CollaborationEvent(
                    enterprise_id=ENTERPRISE_ID,
                    event_type=event_type,
                    payload=payload,
                    source_agent_id=src.id if src else None,
                    target_agent_id=tgt.id if tgt else None,
                    status="pending" if (is_pending_deal and step_idx == len(steps) - 1) else "processed",
                    created_at=deal_start + timedelta(minutes=step_idx * 12),
                )
            )
            event_count += 1

        # 审批门：已完成的商机为 approved，最后一轮保持 pending
        finance = by_name.get("财务经理（AI）")
        db.add(
            ApprovalGate(
                enterprise_id=ENTERPRISE_ID,
                process_id=f"quotation-approval-{deal['opportunity_id']}",
                node_id="finance_review",
                agent_id=finance.id if finance else None,
                status="pending" if is_pending_deal else "approved",
                decided_at=None if is_pending_deal else deal_start + timedelta(minutes=55),
                created_at=deal_start + timedelta(minutes=40),
            )
        )
        gate_count += 1

    return event_count, gate_count


async def rebuild_shadow(db, roster: list[Agent]) -> int:
    """重建影子任务：覆盖四态，含真实人机答案对照。"""
    await db.execute(delete(ShadowTask).where(ShadowTask.enterprise_id == ENTERPRISE_ID))

    by_name = {a.name: a for a in roster}
    now = utcnow()
    count = 0

    for idx, sample in enumerate(SHADOW_SAMPLES):
        agent = by_name.get(sample["position"])
        created = now - timedelta(hours=(len(SHADOW_SAMPLES) - idx) * 7)
        db.add(
            ShadowTask(
                enterprise_id=ENTERPRISE_ID,
                agent_id=agent.id if agent else None,
                task_type=sample["task_type"],
                question=sample["question"],
                human_answer=sample["human_answer"],
                ai_answer=sample["ai_answer"],
                confidence=sample["confidence"],
                status=sample["status"],
                eval_result=sample["eval_result"],
                promoted_at=created + timedelta(hours=2) if sample["status"] == "autonomous" else None,
                created_at=created,
                updated_at=created + timedelta(hours=1),
            )
        )
        count += 1
    return count


async def rebuild_advisor(db) -> int:
    """重建 AI 顾问建议。"""
    await db.execute(
        delete(AdvisorSuggestion).where(AdvisorSuggestion.enterprise_id == ENTERPRISE_ID)
    )
    now = utcnow()
    count = 0
    for sample in ADVISOR_SAMPLES:
        created = now - timedelta(days=sample["days_ago"])
        db.add(
            AdvisorSuggestion(
                enterprise_id=ENTERPRISE_ID,
                type=sample["type"],
                title=sample["title"],
                description=sample["description"],
                impact=sample["impact"],
                status=sample["status"],
                applied_at=created + timedelta(hours=6) if sample["status"] == "applied" else None,
                created_at=created,
            )
        )
        count += 1
    return count


def build_organization() -> tuple[list[dict], dict]:
    """构建组织架构：公司根节点 → 部门 → 岗位（AI 员工），并生成汇报树。

    Returns:
        (departments_list, reporting_tree)
        对齐前端 DepartmentInstance / RuntimeOrganization 契约字段：
        dept_id / name / parent_dept_id / level。
    """
    departments: list[dict] = [dict(d) for d in ORG_DEPARTMENTS]
    # 岗位 → 部门映射：TARGET_ROSTER 自带部门名，知识员工单独归类
    role_dept: dict[str, str] = {}
    for name, dept_name, *_ in TARGET_ROSTER:
        role_dept[name] = DEPARTMENT_TO_ID[dept_name]
    role_dept.update(KNOWLEDGE_AGENT_DEPT)
    for name, dept_id in role_dept.items():
        departments.append({
            "dept_id": f"role_{name}",
            "name": name,
            "parent_dept_id": dept_id,
            "level": 2,
        })
    # 汇报树：parent_dept_id → [child...]
    reporting_tree: dict[str, list[str]] = {}
    for d in departments:
        parent = d["parent_dept_id"]
        if parent:
            reporting_tree.setdefault(parent, []).append(d["dept_id"])
    return departments, reporting_tree


async def rebuild_runtime_organization(db) -> int:
    """把组织架构写入激活 Runtime 的 runtime_data.organization。

    背景：此前运行时 organization.departments 为空 —— seed 只重建 workforce，
    未种入组织数据；而组织架构图（OrganizationView / 企业运转模型）读取
    runtime.organization.departments，故在「AI 员工 / 企业运行时」模块呈现为空。
    这里确定性补齐部门层级，并失效 active runtime 缓存使 GET 读到最新数据。
    """
    from app.models.runtime import EnterpriseRuntime
    from app.utils.cache import cache_delete

    departments, reporting_tree = build_organization()
    result = await db.execute(
        select(EnterpriseRuntime).where(
            EnterpriseRuntime.enterprise_id == ENTERPRISE_ID,
            EnterpriseRuntime.is_active.is_(True),
        )
    )
    runtime = result.scalar_one_or_none()
    if runtime is None:
        return 0
    # 注意：JSON 列不做原地变更追踪。必须构造新 dict 引用再赋值，
    # 否则 SQLAlchemy 认为 runtime_data 未变化，不会发出 UPDATE，写入不落库。
    data = dict(runtime.runtime_data or {})
    data["organization"] = {
        "departments": departments,
        "reporting_tree": reporting_tree,
    }
    runtime.runtime_data = data
    # 失效 active runtime 缓存（key: runtime:active:{enterprise_id}）
    cache_delete(f"runtime:active:{ENTERPRISE_ID}")
    return len(departments)


async def main() -> None:
    print("=" * 64)
    print("UI v4 演示数据重建")
    print("=" * 64)

    async with async_session_factory() as db:
        print("\n[1/5] 收敛 Agent 编制...")
        roster, deleted = await cleanup_duplicate_agents(db)
        await db.flush()
        print(f"  保留 {len(roster)} 个 Agent，删除 {deleted} 个重复")

        print("\n[2/5] 重建生命周期轨迹...")
        lc = await rebuild_lifecycle(db, roster)
        print(f"  写入 {lc} 条生命周期记录")

        print("\n[3/5] 重建协作事件流...")
        ev, gate = await rebuild_collaboration(db, roster)
        print(f"  写入 {ev} 条协作事件，{gate} 个审批门")

        print("\n[4/5] 重建影子任务...")
        sh = await rebuild_shadow(db, roster)
        print(f"  写入 {sh} 条影子任务")

        print("\n[5/5] 重建 AI 顾问建议...")
        ad = await rebuild_advisor(db)
        print(f"  写入 {ad} 条建议")

        print("\n[6/6] 补齐组织架构（runtime.organization）...")
        org_count = await rebuild_runtime_organization(db)
        print(f"  写入 {org_count} 个组织节点（公司/部门/岗位）")

        await db.commit()

    # 校验
    async with async_session_factory() as db:
        agents = (
            await db.execute(select(Agent).where(Agent.enterprise_id == ENTERPRISE_ID))
        ).scalars().all()
        stage_dist: dict[str, int] = {}
        for a in agents:
            stage_dist[a.lifecycle_stage or "unknown"] = stage_dist.get(a.lifecycle_stage or "unknown", 0) + 1

        print("\n" + "=" * 64)
        print("重建完成")
        print("=" * 64)
        print(f"  Agent 编制    : {len(agents)} 人")
        print(f"  生命周期分布  : {stage_dist}")
        print("\n  演示账号: demo@autoteams.example")


if __name__ == "__main__":
    asyncio.run(main())
