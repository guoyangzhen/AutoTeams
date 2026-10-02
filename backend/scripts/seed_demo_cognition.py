"""为演示企业「示例科技（智链物联）」导入企业画像与运行模型演示数据。

解决「企业认知」页面“暂无企业认知数据”的问题：
- 依据 sample_data/example-enterprise(智链物联) 的真实知识文档，
  生成完整的企业画像（EnterpriseProfile）与运行模型（EnterpriseOperatingModel）。
- 绑定到 demo@autoteams.example 所属企业（id=327e4a3f-38c1-48c9-94e8-adce03e47f4a）。

用法：
    cd backend
    python -m scripts.seed_demo_cognition

幂等：重复执行会停用旧版本并写入新版本，不报错。
"""
import asyncio
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.database import async_session_factory
from app.schemas.cognition import (
    EnterpriseProfileData,
    EnterpriseBasic,
    EnterpriseOrgSummary,
    EnterpriseMaturity,
    EnterpriseBusiness,
    EnterpriseOperatingModelData,
    RoleDefinition,
    ProcessDefinitionModel,
    CapabilityItem,
    RuntimeRules,
    GapItem,
)
from app.services.cognition.enterprise_profiler import EnterpriseProfiler
from app.services.cognition.operating_model import OperatingModelBuilder

# 演示企业（demo@autoteams.example 所属）
DEMO_ENTERPRISE_ID = "327e4a3f-38c1-48c9-94e8-adce03e47f4a"
DEMO_ENTERPRISE_NAME = "智链物联科技有限公司"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# 企业画像
# ============================================================

def build_profile() -> EnterpriseProfileData:
    basic = EnterpriseBasic(
        name=DEMO_ENTERPRISE_NAME,
        industry="B2B IoT 智能硬件制造（工业物联网）",
        scale="约 120 人",
        revenue="2025 年营收约 8000 万人民币",
        location="深圳市南山区科技园南区高新南一道 9 号",
        founded="2018 年 6 月",
    )
    org_summary = EnterpriseOrgSummary(
        department_count=8,
        headcount=120,
        key_roles=[
            "CEO / 总经理",
            "销售总监",
            "销售经理",
            "销售代表",
            "售前技术支持",
            "产品总监",
            "产品经理",
            "客服主管",
            "客服专员",
            "售后服务专员",
            "财务经理",
            "会计",
            "人事行政经理",
            "研发总监",
            "硬件工程师",
            "软件工程师",
            "生产经理",
            "质检员",
        ],
    )
    maturity = EnterpriseMaturity(
        level="L2",
        automation_coverage=0.35,
        ai_workforce_count=5,
    )
    business = EnterpriseBusiness(
        main_products=[
            "SL-T100 温湿度传感器",
            "SL-P200 压力传感器",
            "SL-G300 气体传感器",
            "SL-GW500 工业级 IoT 网关",
            "SL-GW510 小型网关",
            "SL-CT800 智能控制器",
            "SL-DC600 数据采集终端",
        ],
        target_industries=[
            "制造业",
            "智慧园区",
            "物流仓储（冷链）",
            "能源管理",
        ],
        core_processes=[
            "询盘响应与报价流程",
            "报价分级审批流程",
            "客户服务与工单处理流程",
            "售后服务流程",
            "产品参数查询协作流程",
            "订单创建与交付流程",
        ],
    )
    profile = EnterpriseProfileData(
        basic=basic,
        tags=[
            "B2B IoT 智能硬件",
            "8 部门",
            "120 人",
            "7 条产品线",
            "国家级高新技术企业",
            "专精特新",
        ],
        org_summary=org_summary,
        maturity=maturity,
        business=business,
        gaps=[
            "未发现招标采购流程文档",
            "未发现财务月结流程文档",
        ],
        version="v1.1.0",
        updated_at=_utcnow(),
        completeness_score=0.92,
    )
    return profile


# ============================================================
# 运行模型
# ============================================================

# KPI 定义（对齐 kpi-matrix.md）
KPIS = {
    "SL-2018-001": ["KPI-CEO-001", "KPI-CEO-002"],
    "SL-2018-002": ["KPI-SDS-001", "KPI-SDS-002", "KPI-SDS-003"],
    "SL-2019-015": ["KPI-SMG-001", "KPI-SMG-002"],
    "SL-2021-045": ["KPI-SR-001", "KPI-SR-002", "KPI-SR-003"],
    "SL-2022-058": ["KPI-SR-004", "KPI-SR-005"],
    "SL-2020-032": ["KPI-PRESALES-001", "KPI-PRESALES-002"],
    "SL-2018-003": ["KPI-PD-001", "KPI-PD-002"],
    "SL-2020-035": ["KPI-PM-001", "KPI-PM-002"],
    "SL-2021-048": ["KPI-TECH-001", "KPI-TECH-002"],
    "SL-2019-020": ["KPI-CSL-001", "KPI-CSL-002"],
    "SL-2022-062": ["KPI-CS-001", "KPI-CS-002", "KPI-CS-003"],
    "SL-2020-038": ["KPI-AFTERSALES-001", "KPI-AFTERSALES-002"],
    "SL-2018-005": ["KPI-FM-001", "KPI-FM-002", "KPI-FM-003"],
    "SL-2021-050": ["KPI-ACC-001", "KPI-ACC-002"],
    "SL-2018-006": ["KPI-HRM-001", "KPI-HRM-002"],
    "SL-2018-004": ["KPI-RDD-001", "KPI-RDD-002"],
    "SL-2019-025": ["KPI-HWE-001", "KPI-HWE-002"],
    "SL-2020-040": ["KPI-SWE-001", "KPI-SWE-002"],
    "SL-2018-007": ["KPI-PM2-001", "KPI-PM2-002"],
    "SL-2020-042": ["KPI-QA-001", "KPI-QA-002"],
}

# 权限定义（对齐 permissions.md / approval-flow.md）
# 注意：权限会随角色去重展示，前缀易读
PERMISSIONS = {
    "SL-2018-001": ["PERM-QUOTE-APPROVE-20W+", "PERM-ALL-DATA", "PERM-ALL-OPERATIONS"],
    "SL-2018-002": ["PERM-QUOTE-APPROVE-5W-20W", "PERM-SALES-DATA", "PERM-CRM-ALL"],
    "SL-2019-015": ["PERM-QUOTE-APPROVE-5W", "PERM-CRM-TEAM"],
    "SL-2021-045": ["PERM-CREATE-QUOTE", "PERM-CRM-OWN", "PERM-CREATE-OPPORTUNITY"],
    "SL-2022-058": ["PERM-CREATE-QUOTE", "PERM-CRM-OWN", "PERM-CREATE-OPPORTUNITY"],
    "SL-2020-032": ["PERM-PRODUCT-DATA", "PERM-TECH-SOLUTION"],
    "SL-2018-003": ["PERM-PRODUCT-EDIT", "PERM-PRICING-ADVICE"],
    "SL-2020-035": ["PERM-PRODUCT-EDIT", "PERM-SPEC-WRITE"],
    "SL-2021-048": ["PERM-TECH-PARAM-EDIT", "PERM-TECH-TICKET"],
    "SL-2019-020": ["PERM-CS-ALL", "PERM-L3-ESCALATION"],
    "SL-2022-062": ["PERM-CS-TICKET", "PERM-L1L2-HANDLE"],
    "SL-2020-038": ["PERM-AS-TICKET", "PERM-ONSITE-SERVICE", "PERM-SPARE-PARTS"],
    "SL-2018-005": ["PERM-QUOTE-REVIEW", "PERM-FIN-ALL", "PERM-INVOICE"],
    "SL-2021-050": ["PERM-VOUCHER", "PERM-INVOICE", "PERM-FIN-REPORT"],
    "SL-2018-006": ["PERM-HR-ALL", "PERM-ORG-MAINT"],
    "SL-2018-004": ["PERM-RD-ALL", "PERM-IP-MGMT", "PERM-TECH-REVIEW"],
    "SL-2019-025": ["PERM-HW-DESIGN", "PERM-BOM", "PERM-TECH-REVIEW"],
    "SL-2020-040": ["PERM-FW-CODE", "PERM-FW-RELEASE", "PERM-TECH-REVIEW"],
    "SL-2018-007": ["PERM-PROD-PLAN", "PERM-PROD-ORDER", "PERM-MATERIAL"],
    "SL-2020-042": ["PERM-QC-EXEC", "PERM-QC-REPORT", "PERM-NCG-REVIEW"],
}

# 角色职责（对齐 org-structure.md）
ROLE_DEFS = [
    dict(id="SL-2018-001", title="CEO / 总经理 · 张明远", department="高管层", level="L1",
         responsibilities=["战略决策", "20 万以上报价终审", "组织架构调整", "跨部门协调"],
         required_skills=["战略规划", "组织管理", "财务决策", "风险管控"]),
    dict(id="SL-2018-002", title="销售总监 · 李婉清", department="销售部", level="L2",
         responsibilities=["销售团队管理", "5-20 万报价审批", "大客户开发", "跨部门协调"],
         required_skills=["销售管理", "报价审批", "大客户谈判"]),
    dict(id="SL-2019-015", title="销售经理 · 王浩然", department="销售部", level="L3",
         responsibilities=["团队客户管理", "5 万以下报价审批", "线索分配", "提交报价"],
         required_skills=["团队管理", "报价审批", "CRM 管理"]),
    dict(id="SL-2021-045", title="销售代表 · 陈思远", department="销售部", level="L4",
         responsibilities=["客户开发与跟进", "创建商机", "提交报价", "CRM 维护"],
         required_skills=["客户开发", "商机跟进", "报价输出", "CRM 操作"]),
    dict(id="SL-2022-058", title="销售代表 · 刘梦琪", department="销售部", level="L4",
         responsibilities=["客户开发与跟进", "创建商机", "提交报价", "CRM 维护"],
         required_skills=["客户开发", "商机跟进", "报价输出", "CRM 操作"]),
    dict(id="SL-2020-032", title="售前技术支持 · 赵明阳", department="销售部", level="L3",
         responsibilities=["产品参数查询", "技术方案补充", "协助销售答复技术问题"],
         required_skills=["产品知识", "技术方案", "参数查询"]),
    dict(id="SL-2018-003", title="产品总监 · 孙志强", department="产品部", level="L2",
         responsibilities=["产品规划", "产品资料管理", "产品定价建议", "跨部门协调"],
         required_skills=["产品规划", "定价策略", "产品生命周期管理"]),
    dict(id="SL-2020-035", title="产品经理 · 周雨桐", department="产品部", level="L3",
         responsibilities=["产品需求管理", "撰写产品规格书", "产品资料维护"],
         required_skills=["需求分析", "规格书编写", "产品资料管理"]),
    dict(id="SL-2021-048", title="技术支持工程师 · 吴俊豪", department="产品部", level="L4",
         responsibilities=["技术工单处理", "技术参数维护", "技术方案评审"],
         required_skills=["技术解答", "参数维护", "问题分析"]),
    dict(id="SL-2019-020", title="客服主管 · 郑晓琳", department="客服部", level="L3",
         responsibilities=["客服团队管理", "L3 投诉升级处理", "流程变更审批"],
         required_skills=["客服管理", "投诉处理", "流程规范"]),
    dict(id="SL-2022-062", title="客服专员 · 黄思琪", department="客服部", level="L4",
         responsibilities=["L1/L2 客户咨询处理", "创建工单", "转交升级工单"],
         required_skills=["客户沟通", "FAQ 应答", "工单管理"]),
    dict(id="SL-2020-038", title="售后服务专员 · 徐建华", department="客服部", level="L4",
         responsibilities=["售后工单处理", "安排上门服务", "提交维修报告", "备件协调"],
         required_skills=["售后处理", "上门服务", "维修报告", "备件管理"]),
    dict(id="SL-2018-005", title="财务经理 · 何德志", department="财务部", level="L2",
         responsibilities=["报价审核", "账务管理", "发票开具", "费用审批"],
         required_skills=["财务审核", "价格合规", "税点校验", "成本测算"]),
    dict(id="SL-2021-050", title="会计 · 罗敏", department="财务部", level="L4",
         responsibilities=["账务凭证录入", "发票开具", "财务报表", "应收应付登记"],
         required_skills=["账务处理", "报表编制", "发票管理"]),
    dict(id="SL-2018-006", title="人事行政经理 · 杨雪梅", department="人事行政部", level="L2",
         responsibilities=["入离职审批", "考勤绩效", "组织架构维护", "行政预算"],
         required_skills=["人事管理", "绩效考核", "组织管理"]),
    dict(id="SL-2018-004", title="研发总监 · 陈博远", department="研发部", level="L2",
         responsibilities=["研发立项审批", "技术评审", "研发资源调配", "知识产权管理"],
         required_skills=["研发管理", "技术评审", "项目管理"]),
    dict(id="SL-2019-025", title="硬件工程师 · 刘天宇", department="研发部", level="L4",
         responsibilities=["硬件设计", "BOM 提交", "参与技术评审"],
         required_skills=["硬件设计", "PCB 设计", "BOM 管理"]),
    dict(id="SL-2020-040", title="软件工程师 · 赵欣怡", department="研发部", level="L4",
         responsibilities=["固件代码开发", "协议栈实现", "固件版本发布"],
         required_skills=["嵌入式开发", "通信协议", "固件发布"]),
    dict(id="SL-2018-007", title="生产经理 · 吴国栋", department="生产部", level="L2",
         responsibilities=["生产计划", "下达生产工单", "物料协调", "生产异常处理"],
         required_skills=["生产计划", "SMT 生产", "品质管控"]),
    dict(id="SL-2020-042", title="质检员 · 孙丽华", department="生产部", level="L4",
         responsibilities=["来料/制程/成品检验", "录入质检报告", "不合格品评审"],
         required_skills=["质量检验", "质检报告", "缺陷分析"]),
]


def build_roles() -> list[RoleDefinition]:
    roles = []
    for d in ROLE_DEFS:
        roles.append(RoleDefinition(
            id=d["id"],
            title=d["title"],
            department=d["department"],
            level=d["level"],
            responsibilities=d["responsibilities"],
            required_skills=d["required_skills"],
            kpi_ids=KPIS.get(d["id"], []),
            permission_ids=PERMISSIONS.get(d["id"], []),
        ))
    return roles


# 流程定义（对齐 approval-flow.md / sales-sop / service-sop）
def build_processes() -> list[ProcessDefinitionModel]:
    return [
        ProcessDefinitionModel(
            id="PROC-QUOTATION",
            name="询盘响应与报价流程",
            type="sop",
            steps=[
                {"step": 1, "action": "接收客户询盘", "owner": "SL-2021-045"},
                {"step": 2, "action": "4 小时内响应并确认需求", "owner": "SL-2021-045"},
                {"step": 3, "action": "24 小时内出具初步报价", "owner": "SL-2021-045"},
                {"step": 4, "action": "产品参数不熟悉时转售前技术支持", "owner": "SL-2020-032"},
                {"step": 5, "action": "提交财务/分级审批", "owner": "SL-2018-005"},
            ],
            owner_role_id="SL-2021-045",
            participants=["SL-2021-045", "SL-2020-032", "SL-2018-005"],
            trigger_event="新询盘",
            system_ids=["CRM", "ERP"],
        ),
        ProcessDefinitionModel(
            id="PROC-APPROVAL",
            name="报价分级审批流程",
            type="approval",
            steps=[
                {"step": 1, "action": "财务经理审核价格/折扣/税点/毛利", "owner": "SL-2018-005"},
                {"step": 2, "action": "按金额分级：<5 万销售经理审批", "owner": "SL-2019-015"},
                {"step": 3, "action": "5-20 万销售总监审批", "owner": "SL-2018-002"},
                {"step": 4, "action": ">20 万 CEO 终审", "owner": "SL-2018-001"},
                {"step": 5, "action": "审批通过后发送客户", "owner": "SL-2021-045"},
            ],
            owner_role_id="SL-2018-005",
            participants=["SL-2018-005", "SL-2019-015", "SL-2018-002", "SL-2018-001"],
            trigger_event="报价单提交",
            system_ids=["ERP"],
        ),
        ProcessDefinitionModel(
            id="PROC-CUSTOMER-SERVICE",
            name="客户服务与工单处理流程",
            type="sop",
            steps=[
                {"step": 1, "action": "受理客户咨询（15 分钟内首次响应）", "owner": "SL-2022-062"},
                {"step": 2, "action": "L1 咨询自动处理（FAQ）", "owner": "SL-2022-062"},
                {"step": 3, "action": "创建工单并分级", "owner": "SL-2022-062"},
                {"step": 4, "action": "L3 投诉升级至客服主管", "owner": "SL-2019-020"},
            ],
            owner_role_id="SL-2022-062",
            participants=["SL-2022-062", "SL-2019-020"],
            trigger_event="客户咨询",
            system_ids=["客服系统"],
        ),
        ProcessDefinitionModel(
            id="PROC-AFTERSALES",
            name="售后服务流程",
            type="sop",
            steps=[
                {"step": 1, "action": "受理售后工单", "owner": "SL-2020-038"},
                {"step": 2, "action": "24 小时内上门响应（重点区域）", "owner": "SL-2020-038"},
                {"step": 3, "action": "维修并提交报告", "owner": "SL-2020-038"},
                {"step": 4, "action": "备件协调与满意度回访", "owner": "SL-2020-038"},
            ],
            owner_role_id="SL-2020-038",
            participants=["SL-2020-038", "SL-2019-020"],
            trigger_event="售后工单创建",
            system_ids=["客服系统", "备件系统"],
        ),
        ProcessDefinitionModel(
            id="PROC-PRODUCT-QUERY",
            name="产品参数查询协作流程",
            type="sop",
            steps=[
                {"step": 1, "action": "销售转产品参数查询", "owner": "SL-2021-045"},
                {"step": 2, "action": "售前技术支持/产品经理提供技术参数", "owner": "SL-2020-032"},
                {"step": 3, "action": "补充技术方案", "owner": "SL-2020-035"},
            ],
            owner_role_id="SL-2020-032",
            participants=["SL-2021-045", "SL-2020-032", "SL-2020-035"],
            trigger_event="产品参数咨询",
            system_ids=["产品库"],
        ),
        ProcessDefinitionModel(
            id="PROC-ORDER",
            name="订单创建与交付流程",
            type="sop",
            steps=[
                {"step": 1, "action": "成交后创建订单", "owner": "SL-2022-062"},
                {"step": 2, "action": "更新客户档案与累计订单", "owner": "SL-2022-062"},
                {"step": 3, "action": "生产计划与排产", "owner": "SL-2018-007"},
                {"step": 4, "action": "发货并发送交付通知", "owner": "SL-2022-062"},
            ],
            owner_role_id="SL-2022-062",
            participants=["SL-2022-062", "SL-2018-007"],
            trigger_event="订单成交",
            system_ids=["ERP", "CRM"],
        ),
    ]


def build_capabilities() -> list[CapabilityItem]:
    caps = []
    for d in ROLE_DEFS:
        caps.append(CapabilityItem(
            role_id=d["id"],
            required_capabilities=d["required_skills"],
            knowledge_sources=[
                "example-enterprise/07-hr/permissions.md",
                "example-enterprise/08-kpi/kpi-matrix.md",
            ],
            tools=["CRM", "ERP", "知识库检索"],
        ))
    return caps


def build_runtime_rules() -> RuntimeRules:
    return RuntimeRules(
        collaboration_rules=[
            {"from": "销售代表", "to": "售前技术支持", "condition": "产品参数不熟悉时转交", "sla_hours": 2},
            {"from": "销售代表", "to": "财务经理", "condition": "报价提交后必经财务审核", "sla_hours": 24},
            {"from": "客服专员", "to": "客服主管", "condition": "L3 投诉升级", "sla_hours": 1},
            {"from": "客服专员", "to": "售后服务专员", "condition": "售后工单转交", "sla_hours": 4},
        ],
        data_flow_rules=[
            {"from": "CRM", "to": "销售代表", "description": "客户与商机数据"},
            {"from": "ERP", "to": "财务经理", "description": "报价与订单数据"},
            {"from": "客服系统", "to": "客服专员", "description": "工单与服务数据"},
        ],
        escalation_rules=[
            {"from": "销售经理", "to": "销售总监", "condition": "报价 5-20 万", "sla_days": 2},
            {"from": "销售总监", "to": "CEO", "condition": "报价 >20 万", "sla_days": 3},
            {"from": "客服专员", "to": "客服主管", "condition": "L3 投诉", "sla_hours": 1},
        ],
    )


def build_operating_model() -> EnterpriseOperatingModelData:
    organization = {
        "departments": [
            {"id": "D-CEO", "name": "高管层", "parent_id": None, "responsibilities": ["战略决策"]},
            {"id": "D-SALES", "name": "销售部", "parent_id": "D-CEO", "responsibilities": ["客户开发", "报价签约", "售前支持"]},
            {"id": "D-PROD", "name": "产品部", "parent_id": "D-CEO", "responsibilities": ["产品规划", "资料维护", "技术方案"]},
            {"id": "D-CS", "name": "客服部", "parent_id": "D-CEO", "responsibilities": ["客户咨询", "工单处理", "售后服务"]},
            {"id": "D-FIN", "name": "财务部", "parent_id": "D-CEO", "responsibilities": ["报价审核", "账务管理", "发票"]},
            {"id": "D-HR", "name": "人事行政部", "parent_id": "D-CEO", "responsibilities": ["入离职", "考勤绩效", "行政"]},
            {"id": "D-RD", "name": "研发部", "parent_id": "D-CEO", "responsibilities": ["硬件设计", "固件开发", "测试认证"]},
            {"id": "D-MFG", "name": "生产部", "parent_id": "D-CEO", "responsibilities": ["生产计划", "SMT 生产", "品质管控"]},
        ],
        "hierarchy_tree": {
            "D-CEO": {
                "name": "高管层",
                "children": {
                    "D-SALES": {"name": "销售部", "children": {}},
                    "D-PROD": {"name": "产品部", "children": {}},
                    "D-CS": {"name": "客服部", "children": {}},
                    "D-FIN": {"name": "财务部", "children": {}},
                    "D-HR": {"name": "人事行政部", "children": {}},
                    "D-RD": {"name": "研发部", "children": {}},
                    "D-MFG": {"name": "生产部", "children": {}},
                },
            }
        },
        "reporting_lines": [],
    }
    return EnterpriseOperatingModelData(
        version="v1.1.0",
        completeness=0.88,
        organization=organization,
        roles=build_roles(),
        processes=build_processes(),
        capabilities=build_capabilities(),
        runtime_rules=build_runtime_rules(),
        gaps=[
            GapItem(area="招标采购流程", severity="medium", suggestion="建议补充招标采购流程文档以提升完成度"),
            GapItem(area="财务月结流程", severity="low", suggestion="建议补充财务月结流程文档"),
        ],
    )


# ============================================================
# 主流程
# ============================================================

async def seed():
    async with async_session_factory() as db:
        # 校验企业存在
        from app.models.enterprise import Enterprise
        ent = await db.execute(select(Enterprise).where(Enterprise.id == DEMO_ENTERPRISE_ID))
        enterprise = ent.scalar_one_or_none()
        if enterprise is None:
            print(f"未找到企业 {DEMO_ENTERPRISE_ID}，请先运行 seed_demo / seed_v3_demo")
            return

        # 1. 企业画像
        profile = build_profile()
        profiler = EnterpriseProfiler(db, DEMO_ENTERPRISE_ID)
        await profiler.save_profile(profile)
        print(f"[1/2] 企业画像已写入（version={profile.version}, 完整度={profile.completeness_score}）")

        # 2. 运行模型
        model = build_operating_model()
        builder = OperatingModelBuilder(db, DEMO_ENTERPRISE_ID)
        await builder.save_model(model)
        print(f"[2/2] 运行模型已写入（version={model.version}, 完整度={model.completeness}）")

        print(f"\n已为演示企业「{enterprise.name}」导入企业认知数据。")
        print("可在「知识与技能 → 文件管理 → 企业认知」查看企业画像与运行模型。")


if __name__ == "__main__":
    asyncio.run(seed())