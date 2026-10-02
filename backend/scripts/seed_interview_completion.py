"""为指定企业播种高访谈完成度（#14 独立入口）。

用法：
    cd backend
    python -m scripts.seed_interview_completion <enterprise_id> [user_id]

说明：
- 为指定企业创建一个新的访谈会话（InterviewEngine.start_session），
  实例化全部问题库问题记录。
- 对每条问题直接更新 answer 字段（写入真实中文答案）并更新会话 answered_count，
  使 interview_completion（0-100）达到接近 100。
- 直接写库（不经 submit_answer，避免每次触发耗时增量重编译）。
- 完成后打印会话完成度，供 seed_demo_compile 读取（max over sessions）。
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory
from app.models.interview import InterviewQuestion, InterviewSession
from app.services.interview.interview_engine import InterviewEngine
from app.services.interview.question_bank import question_bank


def _default_answer(question: str) -> str:
    """基于问题文本关键词生成真实可靠的中文业务答案。"""
    if "订单" in question and "销售" in question:
        return "销售成功后，订单由销售代表转交至客服部的订单专员处理，签订合同后移交生产部排产。"
    if "报价审批" in question:
        return "报价审批按金额分级：10万以下由销售经理审批，10-50万由销售总监审批，50万以上由CEO审批。"
    if "线索" in question and "分配" in question:
        return "客户线索来自官网、展会、转介绍等渠道，按区域和客户等级自动分配至对应销售代表，大型客户由销售经理跟进。"
    if "复盘" in question:
        return "丢单后进行复盘，由销售代表在CRM中记录丢单原因，销售主管组织复盘会议，输出改进措施并落实跟踪。"
    if "满意度" in question:
        return "客服满意度目标达到90%以上，每月统计一次，低于目标时启动专项改进。"
    if "投诉" in question:
        return "客户投诉由客服专员受理，30分钟内响应，普通投诉24小时内解决，重大投诉升级至客服主管并48小时内出具处理方案。"
    if "自动处理" in question:
        return "常见FAQ、产品参数查询等标准化问题可自动处理；涉及故障、投诉、定制需求的复杂问题一律转人工服务。"
    if "客户分级" in question:
        return "客户按年度采购额分为S/A/B/C四级，S级客户提供专属服务与优先响应，C级客户提供标准服务。"
    if "采购审批" in question:
        return "采购审批按金额分级：5万以下由采购经理审批，5-20万由部门总监审批，20万以上由总经理审批。"
    if "供应商准入" in question:
        return "供应商需通过资质审核、样品测试、商务谈判三步准入，合格后录入供应商库，每年复审一次。"
    if "库存预警" in question:
        return "库存预警阈值为安全库存的20%，触发后由仓管发起补货申请，采购部在48小时内完成补货下单。"
    if "费用报销" in question:
        return "费用报销由报销人填写单据，直属上级初审，财务审核，按金额分级审批：1万以下财务经理审批，1万以上总经理审批。"
    if "付款条件" in question:
        return "主要供应商付款账期为月结30天，战略供应商月结60天，付款前需财务审核对账确认。"
    if "发票" in question:
        return "发票审核校验：抬头与合同一致、金额与订单一致、税率正确、重复性校验，任一不符即驳回。"
    if "入职" in question:
        return "新员工入职流程：发放offer、入职登记、开通ERP/CRM/邮箱权限、部门导师带教、试用期考核。"
    if "离职" in question:
        return "离职流程：提交离职申请、直属上级面谈、工作交接、归还资产、人事归档、财务结算。"
    if "绩效" in question:
        return "绩效考核周期为季度考核，考核标准由部门KPI、个人目标、工作态度三部分组成，优秀/合格/待改进三档。"
    if "组织架构" in question:
        return "公司设销售部、客服部、产品部、研发部、生产部、财务部、人事行政部，由CEO统一管理，实行扁平化汇报。"
    if "CRM" in question and "必填" in question:
        return "CRM必填字段包括客户名称、联系人、电话、客户等级、跟进状态、负责人，分别代表客户基本信息和业务进度。"
    if "数据访问权限" in question:
        return "数据访问权限矩阵：销售可查看客户与订单，财务可查看成本与账目，人事可查看员工档案，跨部门数据需申请授权。"
    if "敏感数据" in question:
        return "敏感数据包括薪资、客户财务信息、技术图纸、合同条款，仅限授权人员访问，处理需脱敏并留审计日志。"
    if "KPI" in question:
        return "各部门核心KPI：销售部销售额、客服部满意度、生产部交付率、财务部回款率、研发部项目达成率，目标值按季度设定。"
    if "考核周期" in question:
        return "KPI考核周期为季度，年度进行综合评定，考核结果与绩效奖金和晋升挂钩。"
    if "领先指标" in question:
        return "领先指标包括线索量、询盘量、报价通过率；滞后指标包括销售额、回款额、客户留存率。"
    return "已按企业实际业务规范填写，具体数据见对应业务文档。"


async def main(enterprise_id: str, user_id: str) -> None:
    engine = InterviewEngine()
    from app.config import settings  # 确保配置加载

    async with async_session_factory() as db:
        # 启动一个全新会话（实例化全部问题记录）
        created = await engine.start_session(db, enterprise_id, user_id)
        session_id = created["session_id"]
        print(f"已创建访谈会话: {session_id}, 问题总数={created['total_count']}")

        # 查询会话的所有问题，逐条写入回答
        result = await db.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.session_id == session_id
            )
        )
        questions = result.scalars().all()
        now = datetime.now(timezone.utc)
        answered = 0
        for q in questions:
            if q.answer is not None:
                continue
            q.answer = _default_answer(q.question)
            q.answered_at = now
            answered += 1

        # 更新会话计数
        session = await db.execute(
            select(InterviewSession).where(InterviewSession.id == session_id)
        )
        s = session.scalar_one()
        s.answered_count = (s.answered_count or 0) + answered
        s.updated_at = now
        await db.commit()

        # 计算完成度
        comp = await engine.compute_completeness(db, s)
        print(f"已回答 {answered} 条问题，interview_completion={comp:.2f}/100")

        # 汇总该企业全部会话的完成度（max）
        sessions = (await db.execute(
            select(InterviewSession).where(InterviewSession.enterprise_id == enterprise_id)
        )).scalars().all()
        comps = [await engine.compute_completeness(db, ss) for ss in sessions]
        best = max(comps, default=0.0)
        print(f"该企业全部会话 interview_completion（max）={best:.2f}/100")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python -m scripts.seed_interview_completion <enterprise_id> [user_id]")
        sys.exit(1)
    eid = sys.argv[1]
    # 默认使用 demo 账号所属用户（demo@autoteams.example）
    uid = sys.argv[2] if len(sys.argv) > 2 else "e1600734-8a82-45b6-b863-71e287f44db0"
    asyncio.run(main(eid, uid))