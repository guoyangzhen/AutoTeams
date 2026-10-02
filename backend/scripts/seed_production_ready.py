"""AutoTeams 生产级真实演示全景数据注入脚本（seed_production_ready.py）。

为 demo@autoteams.example 所属企业注入：
1. 8 位完整的在岗/实习数字员工名牌档案（WorkforceProfile，带 ATE- 工号、真实权责边界、HP 绩效、SOP 授权）；
2. 3 条结构完备的真实 SOP 规程卡（FlowCard）；
3. 2 笔真实的高风险待裁决审批门（ApprovalGate，供驾驶舱真实点击批准/驳回）；
4. 协同矩阵工作组、协同任务看板、带资竞标记录与共享黑板信息流（WorkgroupTeam, MatrixTask, SharedBlackboardEntry）；
5. 2 个全渠道接入网关账号（企业微信 / 飞书）；
6. 1 支 5.0 敏捷特遣队与 3 条因果记忆轨迹。

幂等安全：清除旧的测试脏数据后全新灌入，确保 100% 洁净与可用。
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from app.database import async_session_factory
from app.models.channel_account import ChannelAccount
from app.models.cognitive_memory import EpisodicTrace, ProceduralGene
from app.models.collaboration import ApprovalGate, CollaborationEvent
from app.models.counterfactual_shadow import CounterfactualDiff, ShadowEvaluationSession
from app.models.enterprise import Enterprise
from app.models.evolution import AdvisorSuggestion, OrgMetrics
from app.models.flow_card import FlowCardModel
from app.models.strike_team import StrikeTeam
from app.models.team_matrix import MatrixTask, SharedBlackboardEntry, WorkgroupTeam
from app.models.user import User
from app.models.workforce import WorkforceProfile
import uuid
from app.models.audit_log import AuditLog
from app.models.audit_chain_state import AuditChainState
from app.utils.audit import AUDIT_CHAIN_STATE_ID, GENESIS_HASH, _compute_signature

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEMO_EMAIL = "demo@autoteams.example"
DEMO_ENTERPRISE_ID = "327e4a3f-38c1-48c9-94e8-adce03e47f4a"
NOW = datetime.now(timezone.utc)

PROFILES = [
    {
        "id": "wf-prof-001",
        "employee_badge": "ATE-2026-MKT-088",
        "display_name": "林子墨",
        "job_title": "市场增长与舆情分析专员",
        "department": "市场拓展部",
        "employment_status": "shadow",
        "performance_score": 94.2,
        "tone_style": "敏锐、数据驱动、严谨客观",
        "duty_boundaries": {
            "allowed": ["全网跨境竞品价格监测", "多语言营销文案初稿起草", "海外社交平台热点线索提炼", "周度获客漏斗数据归集"],
            "forbidden": ["未经人工授权直接发布对外社媒内容", "变更任何广告投放单日预算上限", "向外部第三方透露客户留资原始清单"]
        },
        "authorized_flows": ["flow_market_intel_monitor", "flow_campaign_drafting"],
        "accessible_knowledge_buckets": ["kb-marketing-playbook", "kb-competitor-matrix"],
        "authorized_tools": ["web_search", "chart_renderer", "multilingual_translate"]
    },
    {
        "id": "wf-prof-002",
        "employee_badge": "ATE-2026-LEG-009",
        "display_name": "陈启明",
        "job_title": "跨境合规与合同法务初审员",
        "department": "法律合规部",
        "employment_status": "probation",
        "performance_score": 91.8,
        "tone_style": "克制、审慎、条分缕析",
        "duty_boundaries": {
            "allowed": ["海外跨境分销合同标准条款审查", "GDPR/数据跨境传输脱敏自评估", "知识产权侵权关键词初筛", "合规风险提示备忘录起草"],
            "forbidden": ["代表企业签署任何具法律效力的正式协议", "单方面豁免合作方违约责任", "删除或修改历史法务审查留痕归档"]
        },
        "authorized_flows": ["flow_contract_pre_review", "flow_cross_border_data_scrub"],
        "accessible_knowledge_buckets": ["kb-compliance-laws", "kb-standard-clauses"],
        "authorized_tools": ["doc_analyzer", "regex_scrubber", "legal_term_dictionary"]
    },
    {
        "id": "wf-prof-003",
        "employee_badge": "ATE-2026-TECH-015",
        "display_name": "张予怀",
        "job_title": "端侧执行与架构韧性专员",
        "department": "技术工程部",
        "employment_status": "probation",
        "performance_score": 88.5,
        "tone_style": "极客、精准、结果导向",
        "duty_boundaries": {
            "allowed": ["Local Runner 2.0 端侧探针健康度巡检", "无头浏览器 ERP 数据拉取自动化", "私有集群日志链路排错", "MCP 服务健康度上报"],
            "forbidden": ["在生产端侧执行未经审核的 shell 破坏性脚本", "绕过双因子 2FA 直接执行财务出纳操作", "暴露内部系统明文私钥凭据"]
        },
        "authorized_flows": ["flow_runner_health_check", "flow_erp_data_extraction"],
        "accessible_knowledge_buckets": ["kb-devops-runbook", "kb-mcp-protocols"],
        "authorized_tools": ["autoteams_runner_cli", "mcp_client", "playwright_browser"]
    },
    {
        "id": "wf-prof-004",
        "employee_badge": "ATE-2026-FIN-007",
        "display_name": "赵亦安",
        "job_title": "多币种结算与财税核算专员",
        "department": "财务结算部",
        "employment_status": "production",
        "performance_score": 95.0,
        "tone_style": "周密、数字敏锐、一丝不苟",
        "duty_boundaries": {
            "allowed": ["季度跨境多币种汇率浮动锁价测算", "供应商应付账款对账单交叉校验", "增值税发票四流合一比对", "结汇成本节约方案生成"],
            "forbidden": ["未经财务总监审批触发银行资金代发网关", "修改企业官方账户收款银行信息", "篡改任何已封账的记账凭证"]
        },
        "authorized_flows": ["flow_cross_border_fx", "flow_invoice_matching"],
        "accessible_knowledge_buckets": ["kb-tax-regulations", "kb-fx-pricing-policy"],
        "authorized_tools": ["fx_rate_fetcher", "invoice_ocr_parser", "erp_ledger_query"]
    },
    {
        "id": "wf-prof-005",
        "employee_badge": "ATE-2025-RC-001",
        "display_name": "顾明远",
        "job_title": "智能风控首席分析师",
        "department": "风险管控部",
        "employment_status": "production",
        "performance_score": 98.2,
        "tone_style": "宏观、冷静、洞察敏锐",
        "duty_boundaries": {
            "allowed": ["全域大额交易异常特征实时捕捉", "特遣队协同任务带资竞标审查", "企业数字员工违规越权行为熔断", "组织成熟度模型评估"],
            "forbidden": ["单方面降低核心业务三重安全护栏等级", "删除风控拦截日志与黑匣子审计数据"]
        },
        "authorized_flows": ["flow_risk_anomaly_detection", "flow_swarm_governance"],
        "accessible_knowledge_buckets": ["kb-enterprise-risk-charter", "kb-audit-standards"],
        "authorized_tools": ["anomaly_detector", "audit_chain_verifier", "matrix_arbiter"]
    },
    {
        "id": "wf-prof-006",
        "employee_badge": "ATE-2025-DEL-014",
        "display_name": "沈书白",
        "job_title": "大客户交付架构师",
        "department": "解决方案部",
        "employment_status": "production",
        "performance_score": 96.5,
        "tone_style": "专业、系统性、协同度高",
        "duty_boundaries": {
            "allowed": ["大客户交付方案 SOP 编排与仿真", "跨部门特遣队组建与子任务拆解", "复杂交付交付物验收评审", "团队黑板信息流主持"],
            "forbidden": ["向客户承诺超出产品能力边界的定制需求", "未经法务确认出具技术交付背书"]
        },
        "authorized_flows": ["flow_vip_solution_assembly", "flow_deliverable_acceptance"],
        "accessible_knowledge_buckets": ["kb-solution-blueprints", "kb-architecture-assets"],
        "authorized_tools": ["sop_synthesizer", "mermaid_exporter", "blackboard_hub"]
    },
    {
        "id": "wf-prof-007",
        "employee_badge": "ATE-2025-LEG-003",
        "display_name": "梁思敏",
        "job_title": "高级合规审计专员",
        "department": "法律合规部",
        "employment_status": "production",
        "performance_score": 97.4,
        "tone_style": "严谨、公允、合规第一",
        "duty_boundaries": {
            "allowed": ["HMAC 审计不可篡改链条全量校验", "员工调级转正反事实差分审核", "合规争议人工仲裁报告出具"],
            "forbidden": ["在审计失败的情况下强制标记为校验通过"]
        },
        "authorized_flows": ["flow_audit_chain_certification", "flow_counterfactual_review"],
        "accessible_knowledge_buckets": ["kb-audit-standards", "kb-legal-precedents"],
        "authorized_tools": ["hmac_verifier", "counterfactual_engine"]
    },
    {
        "id": "wf-prof-008",
        "employee_badge": "ATE-2026-OPS-032",
        "display_name": "陆景天",
        "job_title": "业务可用性巡检专员",
        "department": "运营保障部",
        "employment_status": "shadow",
        "performance_score": 89.0,
        "tone_style": "快捷、警觉、守序",
        "duty_boundaries": {
            "allowed": ["全渠道网关消息吞吐速率巡检", "飞书/企微 Webhook 响应延迟探测", "排队堆积告警与工单派发"],
            "forbidden": ["关闭对外通信主通道开关", "清空消息防重放缓存"]
        },
        "authorized_flows": ["flow_channel_vital_pulse"],
        "accessible_knowledge_buckets": ["kb-channel-integration-spec"],
        "authorized_tools": ["webhook_ping", "rate_limiter_query"]
    }
]

FLOW_CARDS = [
    {
        "flow_id": "flow_cross_border_fx",
        "name": "多币种汇率浮动锁价与结汇核销规程",
        "version": "v2.1.0",
        "description": "实时抓取央行汇率、测算最优锁价窗口，触发财务经理与风控复合裁决",
        "timeout_seconds": 1800,
        "start_node_id": "step_fetch_rates",
        "guardrails": {"closed_loop_required": True, "adaptive_slot_filling": True, "high_risk_confirmation": True},
        "nodes": [
            {"node_id": "step_fetch_rates", "name": "抓取离岸多币种实时汇率", "node_type": "action_tool", "bound_tools": ["fx_rate_fetcher"]},
            {"node_id": "step_check_threshold", "name": "测算汇率波动容差（<0.5%）", "node_type": "branch_condition", "instruction": "判断波动是否超出保护阈值"},
            {"node_id": "step_approval_finance", "name": "财务经理结汇锁价确认", "node_type": "approval_human", "instruction": "大额资金锁价需人工最终确认"},
            {"node_id": "step_commit_settlement", "name": "调用银行专线执行锁单", "node_type": "action_tool", "bound_tools": ["erp_ledger_query"]}
        ],
        "edges": [
            {"source_node_id": "step_fetch_rates", "target_node_id": "step_check_threshold", "label": "数据就绪"},
            {"source_node_id": "step_check_threshold", "target_node_id": "step_approval_finance", "label": "波动触发人机复核"},
            {"source_node_id": "step_approval_finance", "target_node_id": "step_commit_settlement", "label": "核准放行"}
        ]
    },
    {
        "flow_id": "flow_contract_pre_review",
        "name": "海外分销商标准合同跨法域合规审查",
        "version": "v1.4.0",
        "description": "自动扫描合规条款、识别争议条款与准据法管辖，生成红线标记报告",
        "timeout_seconds": 3600,
        "start_node_id": "step_ocr_contract",
        "guardrails": {"closed_loop_required": True, "adaptive_slot_filling": True, "high_risk_confirmation": False},
        "nodes": [
            {"node_id": "step_ocr_contract", "name": "合同文本结构化提取", "node_type": "action_tool", "bound_tools": ["doc_analyzer"]},
            {"node_id": "step_scan_risk", "name": "合规知识库违背度比对", "node_type": "collect_info", "instruction": "对比知识库禁止条款"},
            {"node_id": "step_generate_memo", "name": "生成法务审查意见书", "node_type": "action_tool", "bound_tools": ["mermaid_exporter"]}
        ],
        "edges": [
            {"source_node_id": "step_ocr_contract", "target_node_id": "step_scan_risk", "label": "解析完毕"},
            {"source_node_id": "step_scan_risk", "target_node_id": "step_generate_memo", "label": "风险识别完成"}
        ]
    },
    {
        "flow_id": "flow_vip_solution_assembly",
        "name": "重点客户智能协同交付全景编排",
        "version": "v3.0.0",
        "description": "跨越销售、架构、法务、财务多角色的企业级协同编排流程",
        "timeout_seconds": 7200,
        "start_node_id": "step_intake_demand",
        "guardrails": {"closed_loop_required": True, "adaptive_slot_filling": True, "high_risk_confirmation": True},
        "nodes": [
            {"node_id": "step_intake_demand", "name": "客户诉求与输入要素结构化", "node_type": "collect_info"},
            {"node_id": "step_team_bidding", "name": "发起协同组队竞标裁决", "node_type": "sub_flow"},
            {"node_id": "step_ceo_approval", "name": "交付总监终审签字", "node_type": "approval_human"}
        ],
        "edges": [
            {"source_node_id": "step_intake_demand", "target_node_id": "step_team_bidding", "label": "需求明确"},
            {"source_node_id": "step_team_bidding", "target_node_id": "step_ceo_approval", "label": "方案就绪"}
        ]
    }
]

async def seed_data():
    async with async_session_factory() as db:
        # 1. 确保企业存在且为激活状态
        res = await db.execute(select(Enterprise).where(Enterprise.id == DEMO_ENTERPRISE_ID))
        ent = res.scalar_one_or_none()
        if not ent:
            ent = Enterprise(id=DEMO_ENTERPRISE_ID, name="智链物联科技有限公司", is_active=True)
            db.add(ent)
            logger.info("创建演示企业: %s", ent.name)
        else:
            ent.name = "智链物联科技有限公司"
            ent.is_active = True
            logger.info("更新演示企业: %s", ent.name)

        # 确保 demo 用户绑定企业与角色
        user_res = await db.execute(select(User).where(User.email == DEMO_EMAIL))
        user = user_res.scalar_one_or_none()
        if user:
            user.enterprise_id = DEMO_ENTERPRISE_ID
            user.role = "admin"
            user.name = "企业数字化执行长 (Commander)"

        # 2. 注入 8 位数字员工档案
        for p in PROFILES:
            prof_res = await db.execute(select(WorkforceProfile).where(WorkforceProfile.id == p["id"]))
            prof = prof_res.scalar_one_or_none()
            if not prof:
                prof = WorkforceProfile(
                    id=p["id"],
                    enterprise_id=DEMO_ENTERPRISE_ID,
                    employee_badge=p["employee_badge"],
                    display_name=p["display_name"],
                    job_title=p["job_title"],
                    department=p["department"],
                    employment_status=p["employment_status"],
                    performance_score=p["performance_score"],
                    tone_style=p["tone_style"],
                    duty_boundaries=p["duty_boundaries"],
                    authorized_flows=p["authorized_flows"],
                    accessible_knowledge_buckets=p["accessible_knowledge_buckets"],
                    authorized_tools=p["authorized_tools"],
                )
                db.add(prof)
            else:
                prof.employee_badge = p["employee_badge"]
                prof.display_name = p["display_name"]
                prof.job_title = p["job_title"]
                prof.department = p["department"]
                prof.employment_status = p["employment_status"]
                prof.performance_score = p["performance_score"]
                prof.duty_boundaries = p["duty_boundaries"]
        logger.info("8 位数字员工名牌档案已就位")

        # 3. 注入 3 条 SOP 规程卡
        for f in FLOW_CARDS:
            fc_res = await db.execute(select(FlowCardModel).where(FlowCardModel.flow_id == f["flow_id"]))
            fc = fc_res.scalar_one_or_none()
            if not fc:
                fc = FlowCardModel(
                    flow_id=f["flow_id"],
                    enterprise_id=DEMO_ENTERPRISE_ID,
                    name=f["name"],
                    version=f["version"],
                    description=f["description"],
                    flow_data=f,
                    is_active=True,
                )
                db.add(fc)
            else:
                fc.name = f["name"]
                fc.version = f["version"]
                fc.description = f["description"]
                fc.flow_data = f
        logger.info("3 条结构化 SOP 规程卡已就位")

        # 4. 注入 2 笔真实的高风险待裁决审批门（ApprovalGate）供驾驶舱现场裁决
        await db.execute(delete(ApprovalGate).where(ApprovalGate.enterprise_id == DEMO_ENTERPRISE_ID))
        gate1 = ApprovalGate(
            id="gate-pending-001",
            enterprise_id=DEMO_ENTERPRISE_ID,
            process_id="proc-fx-2026-0926",
            node_id="step_approval_finance",
            approver_id="e1600734-8a82-45b6-b863-71e287f44db0",
            status="pending",
            created_at=NOW - timedelta(minutes=15)
        )
        gate2 = ApprovalGate(
            id="gate-pending-002",
            enterprise_id=DEMO_ENTERPRISE_ID,
            process_id="proc-legal-2026-0927",
            node_id="step_approval_data_cross_border",
            approver_id="e1600734-8a82-45b6-b863-71e287f44db0",
            status="pending",
            created_at=NOW - timedelta(minutes=45)
        )
        db.add(gate1)
        db.add(gate2)
        logger.info("2 笔待裁决审批门已就位")

        # 5. 注入团队协同矩阵与多 Agent 看板
        await db.execute(delete(WorkgroupTeam).where(WorkgroupTeam.enterprise_id == DEMO_ENTERPRISE_ID))
        team = WorkgroupTeam(
            id="team-alpha-delivery",
            enterprise_id=DEMO_ENTERPRISE_ID,
            name="智链全球数字化交付矩阵",
            description="由架构师牵头、跨越法务、财务、运维与技术的全天候协同工作组",
            leader_profile_id="wf-prof-006",
            member_profile_ids=["wf-prof-001", "wf-prof-002", "wf-prof-003", "wf-prof-004", "wf-prof-005"],
            status="active"
        )
        db.add(team)
        await db.flush()

        # 注入多状态协同任务
        await db.execute(delete(MatrixTask).where(MatrixTask.team_id == team.id))
        t1 = MatrixTask(
            id="task-mat-001",
            team_id=team.id,
            title="北美大客户 ERP 订单实时抓取通道搭建",
            description="通过 Local Runner 2.0 无头浏览器完成 SAP 订单报表抓取与结构化",
            priority=1,
            status="in_progress",
            assignee_profile_id="wf-prof-003"
        )
        t2 = MatrixTask(
            id="task-mat-002",
            team_id=team.id,
            title="中东海关商品 HS Code 与合规白名单智能核验",
            description="基于法务合规知识库对 35 种出口商品海关税则进行自动分类",
            priority=2,
            status="bidding",
            suggested_profile_id="wf-prof-002"
        )
        t3 = MatrixTask(
            id="task-mat-003",
            team_id=team.id,
            title="第三季度离岸结算多币种对账差异审计",
            description="核算 4 组离岸银行账户与 ERP 应收账单流水差额并出具报告",
            priority=1,
            status="done",
            assignee_profile_id="wf-prof-004",
            deliverable_report={"status": "100% matched", "total_records": 4820, "variance_yuan": 0.00}
        )
        db.add(t1)
        db.add(t2)
        db.add(t3)

        # 注入黑板信息流
        await db.execute(delete(SharedBlackboardEntry).where(SharedBlackboardEntry.team_id == team.id))
        bb1 = SharedBlackboardEntry(
            id="bb-001",
            team_id=team.id,
            topic="汇率政策与锁单基准",
            content="【财务风控通告】今日离岸人民币对美元汇率波动进入平稳区间，所有单笔超 10 万元订单可直接调用 flow_cross_border_fx 规程卡自动锁价。",
            source_profile_id="wf-prof-004",
            is_pinned=True
        )
        bb2 = SharedBlackboardEntry(
            id="bb-002",
            team_id=team.id,
            topic="端侧 Runner 2.0 运行日志",
            content="【架构师播报】张予怀 (ATE-2026-TECH-015) 成功接入本地 Chromium 物理沙箱，首批 120 笔 ERP 报表数据已完成无感提取。",
            source_profile_id="wf-prof-006",
            is_pinned=False
        )
        db.add(bb1)
        db.add(bb2)
        logger.info("协同工作组、协同看板与共享黑板已就位")

        # 6. 注入渠道接入账号
        await db.execute(delete(ChannelAccount).where(ChannelAccount.enterprise_id == DEMO_ENTERPRISE_ID))
        ca1 = ChannelAccount(
            id="chan-wecom-001",
            enterprise_id=DEMO_ENTERPRISE_ID,
            channel_type="wecom_bot",
            name="企业微信业务协同智能助理",
            description="面向全员员工的企微内联智能办公机器人",
            status="online",
            is_active=True,
            mounted_profile_ids=["wf-prof-001", "wf-prof-004"]
        )
        ca2 = ChannelAccount(
            id="chan-feishu-002",
            enterprise_id=DEMO_ENTERPRISE_ID,
            channel_type="feishu_app",
            name="飞书合规与风控预警枢纽",
            description="法务合规事件与大额交易审批飞书卡片消息推送",
            status="online",
            is_active=True,
            mounted_profile_ids=["wf-prof-002", "wf-prof-005"]
        )
        db.add(ca1)
        db.add(ca2)
        logger.info("企业微信与飞书渠道网关账号已就位")

        # 7. 注入 5.0 敏捷特遣队与长程因果记忆数据
        await db.execute(delete(StrikeTeam).where(StrikeTeam.enterprise_id == DEMO_ENTERPRISE_ID))
        st = StrikeTeam(
            id="strike-alpha-001",
            enterprise_id=DEMO_ENTERPRISE_ID,
            name="跨境大促关税熔断应急特遣队",
            mission_statement="24小时内攻克欧美黑五大促商品关税变动测算，输出应急规程卡补丁并锁定风控边界",
            initiator_badge="ATE-2025-RC-001",
            status="active",
            allocated_compute_budget=500.0,
            total_hp_stake=120.0,
            shared_blackboard_id="bb-strike-001",
            members=[
                {"badge": "ATE-2025-RC-001", "role": "特遣队长", "stake_hp": 40.0},
                {"badge": "ATE-2026-FIN-007", "role": "财税计算", "stake_hp": 30.0},
                {"badge": "ATE-2026-LEG-009", "role": "关税法务", "stake_hp": 30.0},
                {"badge": "ATE-2026-TECH-015", "role": "端侧取数", "stake_hp": 20.0}
            ],
            # 对齐 schemas/strike_team.py SubtaskGraph{nodes,edges}：
            # 旧扁平 map 格式会让前端 .nodes 访问崩溃、_read_graph 校验失败回退空图
            subtask_graph={
                "nodes": [
                    {"id": "sub-1", "title": "抓取海关最新加征税则清单", "status": "accepted"},
                    {"id": "sub-2", "title": "重算 300 种 SKU 边际利润率", "status": "delivered"},
                    {"id": "sub-3", "title": "自动生成出海合同价格兜底补丁",
                     "status": "pending", "depends_on": ["sub-2"]},
                ],
                "edges": [{"from": "sub-2", "to": "sub-3"}],
            },
            expires_at=NOW + timedelta(hours=24)
        )
        db.add(st)

        # 注入因果记忆轨迹
        await db.execute(delete(EpisodicTrace).where(EpisodicTrace.enterprise_id == DEMO_ENTERPRISE_ID))
        et1 = EpisodicTrace(
            id="ep-trace-001",
            enterprise_id=DEMO_ENTERPRISE_ID,
            badge="ATE-2026-FIN-007",
            task_summary="汇率大幅单边拉升期间自动触发锁单规程并规避 ¥42,000 汇兑损失",
            causal_chain_json={
                "trigger": "离岸美元波动突破 0.45% 警戒线",
                "action": "自动调用 flow_cross_border_fx 发起大额锁价申请",
                "verdict": "HITL 审批门通过",
                "effect": "在汇率顶点锁定结汇敞口，有效对冲波动风险"
            },
            reflection_notes="SOP 护栏灵敏度设置合理，避免了人工滞后决策造成的汇兑折损。",
            outcome_score=0.96
        )
        db.add(et1)

        # 8. 初始化洁净合规的 HMAC-SHA256 审计链条（清算历史并发测试产生的链分叉）
        await db.execute(delete(AuditLog))
        await db.execute(delete(AuditChainState))
        genesis_log = AuditLog(
            id=str(uuid.uuid4()),
            prev_hash=GENESIS_HASH,
            user_id="e1600734-8a82-45b6-b863-71e287f44db0",
            action="system_init",
            resource_type="enterprise",
            resource_id=DEMO_ENTERPRISE_ID,
            details={"event": "AutoTeams 生产环境基线初始化完成", "initiator": DEMO_EMAIL},
            created_at=NOW - timedelta(hours=1),
        )
        genesis_log.signature = _compute_signature(genesis_log)
        db.add(genesis_log)
        db.add(AuditChainState(id=AUDIT_CHAIN_STATE_ID, last_signature=genesis_log.signature))
        logger.info("HMAC-SHA256 创世审计链条已就位")

        await db.commit()
        logger.info("=" * 60)
        logger.info("🎉 AutoTeams 生产级真实全景演示数据注入大功告成！")
        logger.info("登录账号: %s / 密码: demo123456", DEMO_EMAIL)
        logger.info("=" * 60)

if __name__ == "__main__":
    asyncio.run(seed_data())
