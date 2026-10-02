"""补齐 demo@autoteams.example 账号的完整演示数据（一键可复现）。

修复目标（让任何登录 demo 账号的用户开箱即用）：
1. 为 5 个角色 Agent（销售/售前/财务/客服/售后）设置 position_id，
   使「今日 AI 公司 → 运行协作流程」能正确挑选完整团队。
2. 清理重复的 processing 状态角色 Agent（无子引用的安全删除）。
3. 补充「组织进化」数据：advisor_suggestions（4 类建议）。
4. 补充「影子模式」数据：shadow_tasks（跨 shadowing/evaluating/qualified 状态样本）。

用法（容器内只读，通过 stdin 运行）：
    cd backend && python -m scripts.seed_demo_full
    # 或容器内： docker exec -i backend python - < seed_demo_full.py

幂等：position 重复设置无害；建议/影子任务按「无则插入」原则。
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")  # 容器内后端根目录；本地运行请注释或改为仓库根

from sqlalchemy import select, func
from app.database import async_session_factory
from app.models.user import User
from app.models.agent import Agent
from app.models.file import File
from app.models.skill import Skill
from app.models.conversation import Conversation
from app.models.shadow import ShadowTask
from app.models.evolution import AdvisorSuggestion

DEMO_EMAIL = "demo@autoteams.example"

# 角色岗位 -> position_id（与前端 AICompanyView 的 pick 逻辑一致）
ROLE_POSITIONS = {
    "销售代表（AI）": "pos_sales",
    "售前技术支持（AI）": "pos_pre_sales",
    "财务经理（AI）": "pos_finance",
    "客服专员（AI）": "pos_customer_service",
    "售后服务专员（AI）": "pos_after_sales",
}

# 组织进化建议（4 类：knowledge / process / capability / organization）
SUGGESTIONS = [
    {
        "type": "knowledge",
        "title": "补充销售话术与竞品对比知识",
        "description": "当前销售/售前 Agent 的知识库缺少标准话术与竞品对比文档，影响报价与沟通质量。",
        "impact": "预计提升售前沟通质量与报价一致性约 15%",
    },
    {
        "type": "process",
        "title": "优化报价审批流为并行会签",
        "description": "当前报价审批为串行流转，跨部门时耗时较长；建议改为财务+销售并行会签，缩短响应时间。",
        "impact": "报价审批平均时长预计缩短 40%",
    },
    {
        "type": "capability",
        "title": "为客服 Agent 增加工单自动分拣能力",
        "description": "客服 Agent 缺少工单自动分级/分拣技能，建议接入 FAQ 检索+工单路由能力。",
        "impact": "一线客服处理效率预计提升 20%",
    },
    {
        "type": "organization",
        "title": "增设区域服务中心作为一线层",
        "description": "当前组织为单层部门结构，建议增设华东/华北/西南区域服务中心，贴近客户缩短响应链路。",
        "impact": "售后响应链路预计缩短一个层级",
    },
    {
        "type": "capability",
        "title": "为财务 Agent 配置报表生成与对账能力",
        "description": "财务 Agent 目前仅能查询，建议增加经营报表自动生成与月结对账能力。",
        "impact": "月结与报表产出周期预计缩短 50%",
    },
]

# 影子任务样本（task_type / 场景）
SHADOW_SAMPLES = [
    ("inquiry", "客户询问温湿度传感器的量程与精度参数", "回复量程 0~100%RH，精度 ±2%RH，并提供数据手册链接", "shadowing"),
    ("quotation", "客户要求对 500 台 SL-T100 传感器快速报价", "按标准价 8 折 + 阶梯折扣给出 3 天有效报价", "evaluating"),
    ("customer_service", "客户投诉到货数量与订单不符，要求核查", "登记工单，通知仓储复核实际发货数并安排补发", "qualified"),
    ("after_sales", "客户反馈设备读数漂移，询问售后流程", "引导提供序列号，创建 RMA 并安排返厂校准", "evaluating"),
    ("approval", "采购 10 台贴片机需总监审批，金额超 50 万", "检查预算与供应商资质，提交总监审批", "shadowing"),
    ("inquiry", "新客户咨询账期与开票方式", "回复默认 60 天账期，支持增值税专票，需完成授信审核", "evaluating"),
    ("after_sales", "客户申请更换故障传感器并索赔运费", "核实保修期与故障责任，生成退换货工单并评估运费补偿", "shadowing"),
    ("customer_service", "客户查询订单物流状态与预计到货时间", "关联物流单号，返回承运商轨迹与预计到货日期", "qualified"),
]


async def main() -> None:
    async with async_session_factory() as db:
        user = (await db.execute(
            select(User).where(User.email == DEMO_EMAIL)
        )).scalar_one_or_none()
        if user is None or not user.enterprise_id:
            raise SystemExit(f"未找到 demo 账号 {DEMO_EMAIL}")
        eid = user.enterprise_id

        agents = (await db.execute(
            select(Agent).where(Agent.enterprise_id == eid)
        )).scalars().all()

        # ---------- 1) 清理 processing 重复角色 Agent（无子引用才删） ----------
        name_to_role = {name: pid for name, pid in ROLE_POSITIONS.items()}
        deleted = 0
        for a in agents:
            if a.status == "processing" and a.name in name_to_role:
                for model, fk in ((File, "agent_id"), (Skill, "agent_id"),
                                  (Conversation, "agent_id"), (ShadowTask, "agent_id")):
                    cnt = (await db.execute(
                        select(func.count()).select_from(model).where(getattr(model, fk) == a.id)
                    )).scalar()
                    if cnt and cnt > 0:
                        break
                else:
                    await db.delete(a)
                    deleted += 1
        if deleted:
            print(f"[1] 已清理 {deleted} 个无引用的 processing 重复角色 Agent")

        # ---------- 2) 为角色 Agent 设置 position_id ----------
        set_cnt = 0
        for a in agents:
            if a.name in name_to_role and a.status == "ready":
                if a.position_id != name_to_role[a.name]:
                    a.position_id = name_to_role[a.name]
                    set_cnt += 1
        await db.flush()
        print(f"[2] 已为 {set_cnt} 个角色 Agent 设置 position_id")

        # ---------- 3) 补充组织进化建议 ----------
        existing_sug = (await db.execute(
            select(func.count()).select_from(AdvisorSuggestion).where(
                AdvisorSuggestion.enterprise_id == eid)
        )).scalar()
        if existing_sug == 0:
            now = datetime.now(timezone.utc)
            for i, s in enumerate(SUGGESTIONS):
                db.add(AdvisorSuggestion(
                    enterprise_id=eid,
                    type=s["type"],
                    title=s["title"],
                    description=s["description"],
                    impact=s["impact"],
                    status="pending",
                    created_at=now - timedelta(days=i),
                ))
            await db.flush()
            print(f"[3] 已写入 {len(SUGGESTIONS)} 条组织进化建议")
        else:
            print(f"[3] 跳过：已存在 {existing_sug} 条建议")

        # ---------- 4) 补充影子任务样本 ----------
        existing_shadow = (await db.execute(
            select(func.count()).select_from(ShadowTask).where(
                ShadowTask.enterprise_id == eid)
        )).scalar()
        if existing_shadow == 0:
            ready_agents = [a for a in agents if a.status == "ready"]
            now = datetime.now(timezone.utc)
            added = 0
            for i, (ttype, question, human_answer, status) in enumerate(SHADOW_SAMPLES):
                a = ready_agents[i % len(ready_agents)] if ready_agents else None
                eval_result = "pending"
                confidence = None
                ai_answer = None
                if status in ("evaluating", "qualified"):
                    ai_answer = "（AI 建议）" + human_answer
                    confidence = 0.9 if status == "qualified" else 0.75
                db.add(ShadowTask(
                    enterprise_id=eid,
                    agent_id=a.id if a else None,
                    task_type=ttype,
                    question=question,
                    human_answer=human_answer,
                    ai_answer=ai_answer,
                    confidence=confidence,
                    status=status,
                    eval_result=eval_result,
                    created_at=now - timedelta(hours=i * 3),
                    updated_at=now - timedelta(hours=i * 3),
                ))
                added += 1
            await db.flush()
            print(f"[4] 已写入 {added} 条影子任务样本")
        else:
            print(f"[4] 跳过：已存在 {existing_shadow} 条影子任务")

        await db.commit()
        print("=" * 60)
        print("演示数据补齐完成！demo@autoteams.example")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
