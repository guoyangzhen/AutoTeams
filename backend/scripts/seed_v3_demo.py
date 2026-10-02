"""v3 演示数据初始化：构建「智链物联」示例企业的完整 v3 演示环境。

依据：
    - docs/重构方案_v3.md §9.6 / §9.7（7 步演示案例数据准备详情）
    - docs/AutoTeams项目需求重新梳理产品需求文档.md §9（示例企业 智链物联）

用法：
    cd backend
    python -m scripts.seed_v3_demo

数据覆盖：
    - 1 个 v3 演示企业（智链物联，id 前缀 demo-zhilian）
    - 5 个用户（1 admin + 4 members，对应老板/销售/客服/财务等业务角色）
    - 5 个 AI 员工 Agent 占位（销售/产品专家/财务/客服/售后，WT3 Workforce 生成后将补全）
    - 1 个 Enterprise Runtime 记录（五级编译产物占位）
    - 5 条 compilation_jobs 记录（五级编译任务占位）
    - 7 步演示案例初始数据：
        * 2 条商机事件（OPP-001 / OPP-005，状态：报价中）
        * 2 条报价单草稿（QUO-2026-001 / QUO-2026-002）
        * 2 条审批流记录（APR-2026-001 / APR-2026-002，状态：待财务审核）
        * 1 条「新询盘」触发事件（对应 7 步案例 Step 1）
    - 不预创建订单与售后反馈（由 7 步案例运行时动态产出）

幂等性：重复执行不报错，已存在的演示数据跳过或更新。
"""
import asyncio
import sys
import os
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text
from app.database import async_session_factory
from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.utils.security import get_password_hash


# ============================================================
# 演示数据常量（与 sample_data/example-enterprise 对齐）
# ============================================================

DEMO_ENTERPRISE_ID = "demo-zhilian-v3"
DEMO_ENTERPRISE_NAME = "智链物联科技有限公司（v3 演示）"
DEMO_EMAIL = "demo-v3@smartlink-iot.com"
DEMO_PASSWORD = "demo123456"

# 5 个 AI 员工 Agent 定义（WT3 已合并，lifecycle_stage / memory_config 使用 spec §10.6 有效值）
V3_AGENTS = [
    {
        "name": "销售 Agent（陈思远）",
        "description": "智链物联销售代表 AI 员工，负责询盘响应、报价生成、客户跟进。对应岗位：销售代表（SL-2021-045）。",
        "system_prompt": (
            "你是智链物联的销售代表 AI 员工（对应人类员工陈思远 SL-2021-045）。"
            "职责：1. 4 小时内响应客户询盘；2. 24 小时内出具初步报价；"
            "3. 遇产品参数不熟悉时转产品专家 Agent；4. 报价金额 ≥5 万需走总监审批通道。"
            "依据：sales-sop-v2.md / quotation-template.md / price-list.md。"
        ),
        "position_id": "SL-2021-045",
        "lifecycle_stage": "production",
    },
    {
        "name": "产品专家 Agent（赵明阳）",
        "description": "智链物联售前技术支持 AI 员工，负责产品参数查询、技术方案补充。对应岗位：售前技术支持（SL-2020-032）。",
        "system_prompt": (
            "你是智链物联的售前技术支持 AI 员工（对应人类员工赵明阳 SL-2020-032）。"
            "职责：1. 从产品规格书查询技术参数（测温范围/精度/通信协议/防护等级）；"
            "2. 为销售 Agent 补充技术方案；3. 辅助方案选型。"
            "依据：spec-sheets/*.md / product-catalog.md。"
        ),
        "position_id": "SL-2020-032",
        "lifecycle_stage": "production",
    },
    {
        "name": "财务 Agent（何德志）",
        "description": "智链物联财务经理 AI 员工，负责价格合规审核、税点校验、毛利核算。对应岗位：财务经理（SL-2018-005）。",
        "system_prompt": (
            "你是智链物联的财务经理 AI 员工（对应人类员工何德志 SL-2018-005）。"
            "职责：1. 审核报价单价格合规性（标准价/年度折扣/大客户价）；"
            "2. 校验税点（13%）；3. 核算毛利；4. 通过后进入分级审批节点。"
            "依据：approval-flow.md V2 / price-list.md。"
        ),
        "position_id": "SL-2018-005",
        "lifecycle_stage": "production",
    },
    {
        "name": "客服 Agent（黄思琪）",
        "description": "智链物联客服专员 AI 员工，负责订单同步、客户档案创建、交付通知。对应岗位：客服专员（SL-2022-062）。",
        "system_prompt": (
            "你是智链物联的客服专员 AI 员工（对应人类员工黄思琪 SL-2022-062）。"
            "职责：1. 成交后创建订单；2. 更新客户档案（累计订单数/金额）；"
            "3. 发送交付通知；4. L1 级咨询自动处理。"
            "依据：service-sop-v2.md / orders.csv / faq.md。"
        ),
        "position_id": "SL-2022-062",
        "lifecycle_stage": "production",
    },
    {
        "name": "售后 Agent（徐建华）",
        "description": "智链物联售后服务专员 AI 员工，负责售后跟进、客户反馈记录、满意度调查。对应岗位：售后服务专员（SL-2020-038）。",
        "system_prompt": (
            "你是智链物联的售后服务专员 AI 员工（对应人类员工徐建华 SL-2020-038）。"
            "职责：1. 交付后跟进客户反馈；2. 记录满意度；3. L2/L3 级售后问题处理；"
            "4. 关联订单与反馈记录。"
            "依据：after-sales-process.md / service-sop-v2.md。"
        ),
        "position_id": "SL-2020-038",
        "lifecycle_stage": "production",
    },
]

# 5 个演示用户（业务角色映射到 admin/member，因 user.role CHECK 约束仅允许这两值）
V3_USERS = [
    (DEMO_EMAIL, "张明远（CEO）", "admin", "SL-2018-001"),
    ("chen.sy@smartlink-iot.com", "陈思远（销售）", "member", "SL-2021-045"),
    ("huang.sq@smartlink-iot.com", "黄思琪（客服）", "member", "SL-2022-062"),
    ("he.dz@smartlink-iot.com", "何德志（财务）", "member", "SL-2018-005"),
    ("xu.jh@smartlink-iot.com", "徐建华（售后）", "member", "SL-2020-038"),
]

# 7 步演示案例初始数据（OPP-001 主案例 / OPP-005 对比案例）
DEMO_OPPORTUNITIES = [
    {
        "opp_id": "OPP-001",
        "customer_id": "C-001",
        "customer_name": "华智新能源汽车制造有限公司",
        "product_interest": "SL-T100×80+SL-GW500×2",
        "estimated_amount": 128400,
        "stage": "报价中",
        "probability": 60,
        "owner": "SL-2021-045",
        "owner_name": "陈思远",
    },
    {
        "opp_id": "OPP-005",
        "customer_id": "C-005",
        "customer_name": "深圳市智链园区运营管理有限公司",
        "product_interest": "SL-GW500×1+SL-DC600×2",
        "estimated_amount": 15300,
        "stage": "报价中",
        "probability": 55,
        "owner": "SL-2022-058",
        "owner_name": "刘梦琪",
    },
]

DEMO_QUOTATIONS = [
    {
        "quotation_id": "QUO-2026-001",
        "opp_id": "OPP-001",
        "customer_id": "C-001",
        "sales_person": "SL-2021-045",
        "line_items": [
            {"product": "SL-T100-MQTT", "qty": 80, "unit_price": 850, "price_type": "S级大客户价"},
            {"product": "SL-GW500-STD", "qty": 2, "unit_price": 5050, "price_type": "S级大客户价"},
        ],
        "total_amount": 128400,
        "discount_applied": "S级：大客户价 + 3%",
        "payment_terms": "30% 预付 + 60% 发货前 + 10% 验收后 30 天",
        "valid_until_days": 30,
        "status": "草稿",
    },
    {
        "quotation_id": "QUO-2026-002",
        "opp_id": "OPP-005",
        "customer_id": "C-005",
        "sales_person": "SL-2022-058",
        "line_items": [
            {"product": "SL-GW500-STD", "qty": 1, "unit_price": 5450, "price_type": "A级年度折扣价"},
            {"product": "SL-DC600-STD", "qty": 2, "unit_price": 4220, "price_type": "A级年度折扣价"},
        ],
        "total_amount": 15300,
        "discount_applied": "A级：年度折扣价 + 5%",
        "payment_terms": "30% 预付 + 70% 发货前",
        "valid_until_days": 30,
        "status": "草稿",
    },
]

DEMO_APPROVALS = [
    {
        "approval_id": "APR-2026-001",
        "quotation_id": "QUO-2026-001",
        "opp_id": "OPP-001",
        "amount": 128400,
        "approval_tier": "5-20万 → 销售总监审批",
        "step_1_financial_reviewer": "何德志（SL-2018-005）",
        "step_1_status": "待审核",
        "step_2_approver": "李婉清（销售总监，SL-2018-002）",
        "step_2_status": "待审批",
        "step_2_type": "人类审批介入",
        "sla": "财务 1 工作日 + 总监 2 工作日",
    },
    {
        "approval_id": "APR-2026-002",
        "quotation_id": "QUO-2026-002",
        "opp_id": "OPP-005",
        "amount": 15300,
        "approval_tier": "<5万 → 销售经理审批",
        "step_1_financial_reviewer": "何德志（SL-2018-005）",
        "step_1_status": "待审核",
        "step_2_approver": "王浩然（销售经理，SL-2019-015）",
        "step_2_status": "待审批",
        "step_2_type": "人类审批介入",
        "sla": "财务 1 工作日 + 经理 1 工作日",
    },
]

# 五级编译阶段
COMPILATION_STAGES = [
    "information",  # L1 信息编译
    "knowledge",    # L2 知识编译
    "process",      # L3 流程编译
    "capability",   # L4 能力编译
    "runtime",      # L5 运行时编译
]


# ============================================================
# 辅助函数
# ============================================================


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(days: int, hour: int = 10) -> datetime:
    now = _utc_now()
    target = now - timedelta(days=days)
    return target.replace(hour=hour, minute=0, second=0, microsecond=0)


# ============================================================
# 主初始化函数
# ============================================================


async def seed():
    """主初始化入口（幂等）。"""
    async with async_session_factory() as db:
        # 幂等检查：演示企业是否已存在
        existing = await db.execute(
            select(Enterprise).where(Enterprise.id == DEMO_ENTERPRISE_ID)
        )
        enterprise = existing.scalar_one_or_none()

        if enterprise:
            print(f"v3 演示企业已存在: {DEMO_ENTERPRISE_NAME}（id={DEMO_ENTERPRISE_ID}）")
            print("如需重新初始化，请先清空演示数据。")
            await _print_summary(db)
            return

        print("=" * 70)
        print("开始初始化 v3 演示数据（智链物联）...")
        print("=" * 70)

        # 1. 创建企业
        enterprise = await _create_enterprise(db)
        # 2. 创建用户
        users = await _create_users(db, enterprise)
        # 3. 创建 5 个 AI 员工 Agent 占位
        agents = await _create_v3_agents(db, enterprise)
        # 4. 创建 Enterprise Runtime + 五级编译任务
        await _create_runtime_and_compilation(db, enterprise)
        # 5. 初始化 7 步演示案例数据
        await _create_demo_case_data(db, enterprise, agents, users)

        await db.commit()

        print()
        print("=" * 70)
        print("v3 演示数据初始化完成！")
        print(f"  登录邮箱: {DEMO_EMAIL}")
        print(f"  登录密码: {DEMO_PASSWORD}")
        print(f"  企业: {DEMO_ENTERPRISE_NAME}（id={DEMO_ENTERPRISE_ID}）")
        print("=" * 70)
        await _print_summary(db)


# ============================================================
# 各实体创建函数
# ============================================================


async def _create_enterprise(db) -> Enterprise:
    """创建智链物联演示企业（使用固定 ID 便于幂等检查）。"""
    enterprise = Enterprise(
        id=DEMO_ENTERPRISE_ID,
        name=DEMO_ENTERPRISE_NAME,
        is_active=True,
    )
    db.add(enterprise)
    await db.flush()
    print(f"[1/5] 演示企业已创建: {DEMO_ENTERPRISE_NAME}")
    return enterprise


async def _create_users(db, enterprise: Enterprise) -> list[User]:
    """创建 5 个演示用户。

    使用原生 SQL 插入，绕过 ORM 中 refresh_token_family_id 字段在数据库中缺失的问题
    （v2 遗留：ORM 模型有此字段但缺少对应迁移）。
    """
    import uuid as _uuid
    users = []
    now = _utc_now()
    login_at = _days_ago(0, 9)
    for email, name, role, _emp_id in V3_USERS:
        user_id = str(_uuid.uuid4())
        await db.execute(
            text(
                "INSERT INTO users "
                "(id, email, password_hash, name, role, enterprise_id, "
                "last_login_at, is_active, created_at, updated_at) "
                "VALUES (:id, :email, :ph, :name, :role, :eid, :ll, 1, :now, :now)"
            ),
            {
                "id": user_id,
                "email": email,
                "ph": get_password_hash(DEMO_PASSWORD),
                "name": name,
                "role": role,
                "eid": enterprise.id,
                "ll": login_at,
                "now": now,
            },
        )
        # 构造 User 对象用于后续引用（不通过 ORM 持久化）
        user = User(
            id=user_id,
            email=email,
            password_hash="",
            name=name,
            role=role,
            enterprise_id=enterprise.id,
            is_active=True,
            last_login_at=login_at,
        )
        users.append(user)
    print(f"[2/5] 用户已创建: {len(users)} 个（含 admin {DEMO_EMAIL}）")
    return users


async def _create_v3_agents(db, enterprise: Enterprise) -> list[Agent]:
    """创建 5 个 AI 员工 Agent 记录（含 WT3 生命周期 + 记忆配置）。

    WT3 已合并，Agent ORM 模型已包含 lifecycle_stage / memory_config / kpi_ids /
    position_id 字段（由 a7b8c9d0f3d7_extend_agent_fields 迁移提供），
    可直接通过 ORM 赋值，无需原生 SQL。
    """
    agents = []
    for data in V3_AGENTS:
        agent = Agent(
            enterprise_id=enterprise.id,
            name=data["name"],
            description=data["description"],
            system_prompt=data["system_prompt"],
            status="ready",
            file_count=0,
            knowledge_count=0,
            version="3.0.0",
            folder_path=f"./uploads/demo-v3/{data['position_id']}",
            config={
                "temperature": 0.3,
                "top_k": 5,
                "max_tokens": 2000,
                "rerank_enabled": True,
            },
            # WT3 字段（spec §10.6 生命周期 + PRD §5.12 记忆配置）
            lifecycle_stage=data["lifecycle_stage"],
            memory_config={
                "short_term": {"max_turns": 20},
                "long_term": {"enabled": True},
                "entity_memory": {"enabled": True},
            },
            kpi_ids=[f"KPI-{data['position_id']}"],
            position_id=data["position_id"],
        )
        db.add(agent)
        agents.append(agent)
    await db.flush()
    print(f"[3/5] AI 员工 Agent 已创建: {len(agents)} 个（lifecycle_stage=production）")
    return agents


async def _create_runtime_and_compilation(db, enterprise: Enterprise):
    """创建 Enterprise Runtime 记录与五级编译任务（原生 SQL）。

    表由 a3b4c5d6e9f3_add_runtime_tables 和 a2b3c4d5e8f2_add_compiler_tables 迁移提供。
    """
    now = _utc_now()
    runtime_id = f"{DEMO_ENTERPRISE_ID}-runtime-v3.0.0"

    # 1. Enterprise Runtime（五级编译产物占位）
    await db.execute(
        text(
            "INSERT INTO enterprise_runtimes "
            "(id, enterprise_id, version, model_version, compiled_at, "
            "completeness, runtime_data, is_active, created_at, updated_at) "
            "VALUES (:id, :eid, :ver, :mv, :cat, :comp, :rdata, :active, :now, :now)"
        ),
        {
            "id": runtime_id,
            "eid": enterprise.id,
            "ver": "v3.0.0",
            "mv": "autoteams-v3-compiler-1.0",
            "cat": now,
            "comp": 0.85,  # 完成度 85%（≥80% 满足 MVP 要求）
            "rdata": json.dumps({
                "enterprise_name": "智链物联科技有限公司",
                "departments": 7,
                "positions": 24,
                "products": 7,
                "customers": 20,
                "sop_version": "v2",
                "compilation_summary": "五级编译完成，生成 5 个 AI 员工岗位映射",
            }, ensure_ascii=False),
            "active": 1,
            "now": now,
        },
    )

    # 关联到企业表的 current_runtime_version_id（a4b5c6d7f0a4 迁移添加的字段）
    await db.execute(
        text(
            "UPDATE enterprises SET current_runtime_version_id = :rid "
            "WHERE id = :eid"
        ),
        {"rid": runtime_id, "eid": enterprise.id},
    )

    # 2. 五级编译任务记录
    for i, stage in enumerate(COMPILATION_STAGES):
        job_id = f"{DEMO_ENTERPRISE_ID}-compile-{stage}"
        await db.execute(
            text(
                "INSERT INTO compilation_jobs "
                "(id, enterprise_id, stage, status, input_ref, output_ref, "
                "confidence, completeness, started_at, completed_at, created_at, updated_at) "
                "VALUES (:id, :eid, :stage, :status, :iref, :oref, :conf, :comp, "
                ":start, :end, :now, :now)"
            ),
            {
                "id": job_id,
                "eid": enterprise.id,
                "stage": stage,
                "status": "completed",
                "iref": f"sample_data/example-enterprise/ ({stage} input)",
                "oref": f"enterprise_runtimes/{runtime_id} ({stage} output)",
                "conf": round(0.85 + i * 0.02, 2),
                "comp": round(0.80 + i * 0.03, 2),
                "start": _days_ago(1, 10 + i),
                "end": _days_ago(1, 10 + i + 1),
                "now": now,
            },
        )
    print(f"[4/5] Enterprise Runtime + 五级编译任务已创建（完成度 85%）")


async def _create_demo_case_data(db, enterprise: Enterprise, agents: list[Agent], users: list[User]):
    """初始化 7 步演示案例数据（商机事件 / 报价单草稿 / 审批流 / 触发事件）。

    使用 collaboration_events 和 approval_gates 表（a0b1c2d3f6f0 / a8b9c0d1f4e8 迁移）。
    不预创建订单与售后反馈（由 7 步案例运行时动态产出）。
    """
    now = _utc_now()
    # Agent 映射：销售/产品专家/财务/客服/售后
    sales_agent = agents[0]
    product_agent = agents[1]
    finance_agent = agents[2]
    service_agent = agents[3]
    after_sales_agent = agents[4]

    # 1. 创建商机事件（OPP-001 / OPP-005，状态：报价中）
    for opp in DEMO_OPPORTUNITIES:
        await db.execute(
            text(
                "INSERT INTO collaboration_events "
                "(id, enterprise_id, event_type, payload, source_agent_id, "
                "target_agent_id, status, created_at) "
                "VALUES (:id, :eid, :etype, :payload, :src, :tgt, :status, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-opp-{opp['opp_id']}",
                "eid": enterprise.id,
                "etype": "opportunity_created",
                "payload": json.dumps(opp, ensure_ascii=False),
                "src": None,
                "tgt": sales_agent.id,
                "status": "pending",
                "now": _days_ago(2, 10),
            },
        )

    # 2. 创建报价单草稿（作为 collaboration_events 中的 quotation_generated 事件）
    for quo in DEMO_QUOTATIONS:
        valid_until = now + timedelta(days=quo["valid_until_days"])
        payload = {
            **quo,
            "valid_until": valid_until.isoformat(),
        }
        await db.execute(
            text(
                "INSERT INTO collaboration_events "
                "(id, enterprise_id, event_type, payload, source_agent_id, "
                "target_agent_id, status, created_at) "
                "VALUES (:id, :eid, :etype, :payload, :src, :tgt, :status, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-quo-{quo['quotation_id']}",
                "eid": enterprise.id,
                "etype": "quotation_generated",
                "payload": json.dumps(payload, ensure_ascii=False),
                "src": sales_agent.id,
                "tgt": finance_agent.id,
                "status": "pending",
                "now": _days_ago(1, 14),
            },
        )

    # 3. 创建审批流记录（approval_gates 表，schema: process_id/node_id/agent_id/approver_id）
    # 审批流 V2：财务审核 → 分级审批（人类介入）
    # users 映射：[0]CEO [1]销售 [2]客服 [3]财务 [4]售后
    finance_user = users[3]  # 何德志（财务）
    ceo_user = users[0]  # 张明远（CEO，审批占位）
    for appr in DEMO_APPROVALS:
        # 财务审核节点（Step 4：财务 Agent 审核）
        await db.execute(
            text(
                "INSERT INTO approval_gates "
                "(id, enterprise_id, process_id, node_id, agent_id, status, approver_id, created_at) "
                "VALUES (:id, :eid, :pid, :nid, :aid, :status, :approver, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-appr-{appr['approval_id']}-fin",
                "eid": enterprise.id,
                "pid": appr["approval_id"],
                "nid": "financial_review",
                "aid": finance_agent.id,
                "status": "pending",
                "approver": finance_user.id,
                "now": now,
            },
        )
        # 分级审批节点（Step 5：人类审批介入）
        await db.execute(
            text(
                "INSERT INTO approval_gates "
                "(id, enterprise_id, process_id, node_id, agent_id, status, approver_id, created_at) "
                "VALUES (:id, :eid, :pid, :nid, :aid, :status, :approver, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-appr-{appr['approval_id']}-tier",
                "eid": enterprise.id,
                "pid": appr["approval_id"],
                "nid": f"tier_approval_{appr['approval_tier'].split(' → ')[0]}",
                "aid": None,  # 人类审批，无 Agent
                "status": "pending",
                "approver": ceo_user.id,  # CEO 占位审批
                "now": now,
            },
        )
        # 审批流详情作为 collaboration_event 存档
        await db.execute(
            text(
                "INSERT INTO collaboration_events "
                "(id, enterprise_id, event_type, payload, source_agent_id, "
                "target_agent_id, status, created_at) "
                "VALUES (:id, :eid, :etype, :payload, :src, :tgt, :status, :now)"
            ),
            {
                "id": f"{DEMO_ENTERPRISE_ID}-appr-{appr['approval_id']}",
                "eid": enterprise.id,
                "etype": "approval_flow_created",
                "payload": json.dumps(appr, ensure_ascii=False),
                "src": finance_agent.id,
                "tgt": None,
                "status": "pending",
                "now": now,
            },
        )

    # 4. 注入「新询盘」触发事件（对应 7 步案例 Step 1）
    trigger_payload = {
        "step": 1,
        "event": "new_inquiry",
        "opp_id": "OPP-001",
        "customer_id": "C-001",
        "customer_name": "华智新能源汽车制造有限公司",
        "inquiry": "电池车间二期扩建温湿度监测项目，需 SL-T100 温湿度传感器 80 只 + SL-GW500 工业网关 2 台",
        "required_response_sla": "4 小时内首次响应",
        "quotation_sla": "24 小时内初步报价",
    }
    await db.execute(
        text(
            "INSERT INTO collaboration_events "
            "(id, enterprise_id, event_type, payload, source_agent_id, "
            "target_agent_id, status, created_at) "
            "VALUES (:id, :eid, :etype, :payload, :src, :tgt, :status, :now)"
        ),
        {
            "id": f"{DEMO_ENTERPRISE_ID}-trigger-inquiry-001",
            "eid": enterprise.id,
            "etype": "inquiry_received",
            "payload": json.dumps(trigger_payload, ensure_ascii=False),
            "src": None,
            "tgt": sales_agent.id,
            "status": "pending",
            "now": now,
        },
    )
    print(f"[5/5] 7 步演示案例数据已创建（2 商机 / 2 报价单 / 2 审批流 / 1 触发事件）")


async def _print_summary(db):
    """输出验证点：5 个 AI 员工 ID + 2 商机 ID + 2 报价单 ID + 2 审批流 ID。"""
    print()
    print("-" * 70)
    print("验证点（供 test_demo_case.py 引用）：")
    print("-" * 70)

    # 5 个 AI 员工
    agents_result = await db.execute(
        select(Agent).where(Agent.enterprise_id == DEMO_ENTERPRISE_ID)
    )
    agents = agents_result.scalars().all()
    print(f"  AI 员工（{len(agents)} 个）:")
    for a in agents:
        pos_id = ""
        try:
            pos_id_result = await db.execute(
                text("SELECT position_id FROM agents WHERE id = :id"),
                {"id": a.id},
            )
            pos_id = pos_id_result.scalar() or ""
        except Exception:
            pass
        print(f"    - {a.id}  {a.name}  (position_id={pos_id})")

    # 2 商机 + 2 报价单 + 2 审批流 + 1 触发事件
    for prefix, label in [
        ("opp", "商机"),
        ("quo", "报价单"),
        ("appr", "审批流"),
        ("trigger", "触发事件"),
    ]:
        result = await db.execute(
            text(
                "SELECT id FROM collaboration_events "
                "WHERE enterprise_id = :eid AND id LIKE :pattern "
                "UNION ALL "
                "SELECT id FROM approval_gates "
                "WHERE enterprise_id = :eid2 AND id LIKE :pattern2"
            ),
            {
                "eid": DEMO_ENTERPRISE_ID,
                "pattern": f"{DEMO_ENTERPRISE_ID}-{prefix}-%",
                "eid2": DEMO_ENTERPRISE_ID,
                "pattern2": f"{DEMO_ENTERPRISE_ID}-{prefix}-%",
            },
        )
        ids = [r[0] for r in result.fetchall()]
        if ids:
            print(f"  {label}（{len(ids)} 个）:")
            for i in ids:
                print(f"    - {i}")


if __name__ == "__main__":
    asyncio.run(seed())
