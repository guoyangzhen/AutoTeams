"""v3 演示向量数据初始化：向 ChromaDB 写入智链物联的演示向量。

依据：
    - docs/重构方案_v3.md §9.6 阶段 2（seed_v3_vectors.py）
    - docs/AutoTeams项目需求重新梳理产品需求文档.md §9（示例企业 智链物联）

用法：
    cd backend
    python -m scripts.seed_v3_vectors

注意：
    - 需先运行 seed_v3_demo.py 创建 Agent 记录
    - 演示企业使用固定 ID（demo-zhilian-v3），与 seed_v3_demo.py 对齐
    - 幂等：重复执行会清空旧向量后重建，避免重复

数据覆盖：
    - 知识库向量：5 个 AI 员工 Agent 的知识片段（产品规格/SOP/FAQ/价格表/审批流）
    - 长期记忆向量：7 步演示案例的对话记忆片段（询盘/报价/审批/成交/售后）
"""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text
from app.database import async_session_factory
from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.services.vector_store import VectorStoreService


DEMO_ENTERPRISE_ID = "demo-zhilian-v3"
DEMO_ENTERPRISE_NAME = "智链物联科技有限公司（v3 演示）"

# ============================================================
# 知识库片段：按 Agent 名 → 文件名 → 片段列表
# 数据来源：sample_data/example-enterprise/ 的 30 个数据文件
# ============================================================

# 销售 Agent 知识片段（来源：02-sales/ + 04-products/ + 05-crm/）
SALES_CHUNKS: dict[str, list[str]] = {
    "sales-sop-v2.md": [
        "销售 SOP v2：询盘响应 SLA 为 4 小时内首次响应，24 小时内出具初步报价。",
        "线索分配规则：按行业区域分配，新能源制造→陈思远，智慧园区→刘梦琪。",
        "报价审批 V2 金额分级：<5万经理审批 / 5-20万总监审批 / >20万 CEO 审批。",
        "丢单复盘：3 工作日内完成复盘，记录原因并改进。",
    ],
    "quotation-template.md": [
        "报价单模板：包含产品明细、单价、数量、折扣、税点（13%）、付款条款、有效期。",
        "付款条款模板：30% 预付 + 60% 发货前 + 10% 验收后 30 天（S 级客户）。",
    ],
    "price-list.md": [
        "SL-T100 温湿度传感器：标准价 980 元，年度折扣价 920 元，大客户价 850 元。",
        "SL-GW500 工业网关：标准价 5800 元，年度折扣价 5450 元，大客户价 5050 元。",
        "SL-DC600 数据采集终端：标准价 4500 元，年度折扣价 4220 元，大客户价 3910 元。",
        "客户分级折扣：S 级大客户价+3%，A 级年度折扣价+5%，B 级标准价，C 级标准价。",
    ],
    "customers.csv": [
        "C-001 华智新能源汽车制造有限公司：S 级客户，新能源汽车制造，年采购 285.6 万。",
        "C-005 深圳市智链园区运营管理有限公司：A 级客户，智慧园区，年采购 42 万。",
    ],
    "opportunities.csv": [
        "OPP-001：华智新能源电池车间二期扩建，SL-T100×80+SL-GW500×2，报价 128400 元，报价中。",
        "OPP-005：深圳智链园区 IBMS 能耗采集，SL-GW500×1+SL-DC600×2，报价 15300 元，报价中。",
    ],
}

# 产品专家 Agent 知识片段（来源：04-products/spec-sheets/）
PRODUCT_CHUNKS: dict[str, list[str]] = {
    "SL-T100.md": [
        "SL-T100 温湿度传感器：测温范围 -40~+125°C，精度 ±0.3°C，湿度 0~100%RH。",
        "SL-T100 通信协议：MQTT / Modbus RTU / NB-IoT 三种版本，防护等级 IP65。",
        "SL-T100 电池寿命：CR2450 纽扣电池，续航 2 年（15 分钟上报间隔）。",
    ],
    "SL-GW500.md": [
        "SL-GW500 工业网关：4G+以太网+Wi-Fi 三网口，支持 Modbus RTU/TCP 转 MQTT。",
        "SL-GW500 接入容量：最多 32 个子设备，交付周期 3-4 周。",
        "SL-GW500 防护等级：IP67，工作温度 -40~+70°C，支持 OTA 升级。",
    ],
    "SL-DC600.md": [
        "SL-DC600 数据采集终端：16AI+8DI+4DO，支持 4-20mA/0-10V/RTD 信号输入。",
        "SL-DC600 交付周期 3-4 周，支持本地存储 30 天数据，断电续传。",
    ],
    "SL-P200.md": [
        "SL-P200 压力传感器：量程 0-1.6MPa，精度 ±0.5%FS，4-20mA 输出。",
    ],
    "SL-G300.md": [
        "SL-G300 气体传感器：支持可燃气体/有毒气体检测，Ex d IIC T6 防爆认证。",
    ],
    "SL-CT800.md": [
        "SL-CT800 智能控制器：双以太网口，支持边缘计算，响应 <100ms。",
    ],
    "SL-GW510.md": [
        "SL-GW510 小型网关：2G+Wi-Fi，支持 8 个子设备，PoE 供电。",
    ],
}

# 财务 Agent 知识片段（来源：09-finance/approval-flow.md + price-list.md）
FINANCE_CHUNKS: dict[str, list[str]] = {
    "approval-flow.md": [
        "审批流 V2 金额分级：<5万销售经理审批 / 5-20万销售总监审批 / >20万 CEO 审批。",
        "财务审核要点：1. 价格合规（标准价/折扣价/大客户价匹配客户等级）；2. 税点 13%；3. 毛利核算。",
        "财务审核 SLA：1 工作日内完成，通过后进入分级审批节点。",
        "审批拒绝场景：折扣超出权限 / 税点错误 / 毛利低于阈值（<15%）。",
    ],
    "price-list.md": [
        "S 级客户折扣规则：大客户价 = 标准价 × 0.87（约 13% 折扣）。",
        "A 级客户折扣规则：年度折扣价 = 标准价 × 0.93（约 7% 折扣）。",
        "税点统一 13%，报价金额含税。",
    ],
}

# 客服 Agent 知识片段（来源：03-customer-service/ + 06-orders/）
SERVICE_CHUNKS: dict[str, list[str]] = {
    "service-sop-v2.md": [
        "客服分级 V2：L1 自动处理（设备连接/数据异常）/ L2 人工处理（固件升级）/ L3 专家处理（硬件故障）。",
        "L1 自动处理边界：FAQ 命中率 ≥80% 的问题自动回复，无需人工介入。",
        "客服 SLA：L1 即时响应 / L2 4 小时内 / L3 24 小时内。",
    ],
    "faq.md": [
        "设备连接 FAQ：SL-T100 配对步骤为 1.安装电池 2.长按配对键 5 秒 3.网关扫描添加。",
        "数据异常 FAQ：传感器离线超 30 分钟自动告警，检查电池/信号/网关状态。",
        "电池更换 FAQ：CR2450 纽扣电池，更换后无需重新配对，自动恢复上报。",
        "固件升级 FAQ：OTA 升级需设备在线且电量 >30%，升级过程约 5 分钟。",
        "保修政策 FAQ：主机 2 年质保，配件 1 年质保，非人为损坏免费换新。",
    ],
    "after-sales-process.md": [
        "售后服务流程：1. 客户反馈记录 2. 问题分类（安装/质量/培训）3. 派单处理 4. 满意度回访。",
        "满意度评分：1-5 分，4 分以下触发复盘。",
    ],
    "orders.csv": [
        "订单状态流转：待生产 → 生产中 → 已发货 → 已完成。客服在「已发货」后通知客户。",
        "交付通知模板：包含订单号、产品清单、物流单号、预计到货日期。",
    ],
}

# 售后 Agent 知识片段（来源：03-customer-service/after-sales-process.md）
AFTER_SALES_CHUNKS: dict[str, list[str]] = {
    "after-sales-process.md": [
        "售后接管触发：交付后 7 天内主动回访，或客户反馈触发。",
        "售后记录字段：feedback_id / order_id / feedback_type / content / satisfaction_score / status / handler。",
        "反馈分类：安装咨询 / 质量问题 / 培训需求 / 投诉建议。安装咨询由售后 Agent 直接处理。",
        "满意度回访：交付后 30 天内完成满意度调查，记录 1-5 分评分及文字反馈。",
    ],
    "service-sop-v2.md": [
        "L3 售后问题处理流程：1. 现场勘查 2. 根因分析 3. 方案制定 4. 实施修复 5. 验证关闭。",
        "售后问题升级：L3 超 48 小时未解决升级至技术总监。",
    ],
}

# Agent 名 → 知识片段映射
AGENT_CHUNK_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "销售 Agent（陈思远）": SALES_CHUNKS,
    "产品专家 Agent（赵明阳）": PRODUCT_CHUNKS,
    "财务 Agent（何德志）": FINANCE_CHUNKS,
    "客服 Agent（黄思琪）": SERVICE_CHUNKS,
    "售后 Agent（徐建华）": AFTER_SALES_CHUNKS,
}

# ============================================================
# 长期记忆片段：7 步演示案例的对话记忆
# ============================================================

LONG_TERM_MEMORY_CHUNKS: list[str] = [
    # Step 1: 询盘
    "2026-07-29 询盘事件：华智新能源（C-001）咨询 SL-T100×80+SL-GW500×2 温湿度监测项目，"
    "预算约 12.8 万。销售 Agent 陈思远在 2 小时内完成首次响应。",
    # Step 2: 产品参数查询
    "2026-07-29 产品参数查询：销售 Agent 请求产品专家补充 SL-T100 参数，"
    "返回测温范围 -40~+125°C，精度 ±0.3°C，适合电池车间高湿环境。",
    # Step 3: 报价
    "2026-07-29 报价生成：销售 Agent 生成报价单 QUO-2026-001，"
    "S 级大客户价 SL-T100-MQTT×80@850 + SL-GW500-STD×2@5050 = 128400 元，"
    "付款条款 30%+60%+10%，有效期 30 天。",
    # Step 4: 财务审核
    "2026-07-29 财务审核：财务 Agent 何德志审核 QUO-2026-001 通过，"
    "价格合规（S 级大客户价 13% 折扣）、税点 13% 正确、毛利 22% 高于阈值 15%。",
    # Step 5: 总监审批
    "2026-07-29 总监审批：销售总监李婉清审批 QUO-2026-001 通过（金额 12.8 万，5-20 万区间），"
    "商机 OPP-001 状态变更为「已成交」。",
    # Step 6: 客服同步
    "2026-07-29 客服同步：客服 Agent 黄思琪创建订单 ORD-2026-009，"
    "更新华智客户档案（累计订单 9 笔），发送交付通知。",
    # Step 7: 售后接管
    "2026-07-29 售后接管：售后 Agent 徐建华记录客户反馈 FB-2026-001，"
    "类型：安装咨询，内容：SL-T100 在电池车间高湿环境下的防潮措施，满意度：5 分。",
]


def _fallback_chunks(agent_name: str, file_name: str) -> list[str]:
    """为没有预定义片段的文件生成通用占位片段。"""
    return [
        f"【{agent_name}】文件《{file_name}》摘要：该文件支撑 {agent_name} 的知识库问答能力。",
    ]


async def seed_vectors():
    """主入口：为智链物联演示企业的所有 Agent 生成向量数据。"""
    async with async_session_factory() as db:
        enterprise_result = await db.execute(
            select(Enterprise).where(Enterprise.id == DEMO_ENTERPRISE_ID)
        )
        enterprise = enterprise_result.scalar_one_or_none()
        if not enterprise:
            print(f"演示企业不存在：{DEMO_ENTERPRISE_NAME}（id={DEMO_ENTERPRISE_ID}）")
            print("请先运行：python -m scripts.seed_v3_demo")
            return

        agents_result = await db.execute(
            select(Agent).where(Agent.enterprise_id == enterprise.id)
        )
        agents = agents_result.scalars().all()
        if not agents:
            print("未找到任何演示 Agent，请先运行 seed_v3_demo.py。")
            return

        print("=" * 70)
        print("开始向 ChromaDB 写入 v3 演示向量数据（智链物联）...")
        print("=" * 70)

        total_vectors = 0

        # 1. 知识库向量：每个 Agent 一个 collection
        for agent in agents:
            collection_name = f"agent_{agent.id}"
            vector_store = await VectorStoreService.create(collection_name)

            # 幂等：若已有数据则清空后重建
            existing = await vector_store.count()
            if existing > 0:
                print(f"  [{agent.name}] 集合已存在 {existing} 条，清空后重建...")
                await vector_store.delete_collection()
                vector_store = await VectorStoreService.create(collection_name)

            templates = AGENT_CHUNK_TEMPLATES.get(agent.name, {})
            documents: list[str] = []
            metadatas: list[dict] = []
            ids: list[str] = []

            for file_name, chunks in templates.items():
                for i, chunk_text in enumerate(chunks):
                    chunk_id = f"{agent.id}_{file_name}_{i}"
                    documents.append(chunk_text)
                    metadatas.append({
                        "source": file_name,
                        "file_type": "md",
                        "chunk_index": i,
                        "enterprise_id": DEMO_ENTERPRISE_ID,
                    })
                    ids.append(chunk_id)

            if not documents:
                print(f"  [{agent.name}] 没有预定义知识片段，跳过。")
                continue

            await vector_store.add_documents(documents, metadatas, ids)
            total_vectors += len(documents)
            print(f"  [{agent.name}] 知识库向量已写入 {len(documents)} 条")

        # 2. 长期记忆向量：按 7 步案例角色分配到各 Agent 的 collection
        # VectorStoreService 要求 collection name 格式为 agent_<uuid>
        # 长期记忆分布到各步骤涉及的 Agent（step → agent index 映射）
        # Agent 顺序：[0]销售 [1]产品专家 [2]财务 [3]客服 [4]售后
        STEP_AGENT_MAP = [0, 1, 0, 2, 0, 3, 4]  # 7 步 → Agent 索引
        memory_count = 0
        for step_idx, chunk in enumerate(LONG_TERM_MEMORY_CHUNKS):
            agent = agents[STEP_AGENT_MAP[step_idx]]
            collection_name = f"agent_{agent.id}"
            vector_store = await VectorStoreService.create(collection_name)

            chunk_id = f"{agent.id}_memory_step{step_idx + 1}"
            await vector_store.add_documents(
                [chunk],
                [{
                    "source": "7-step-demo-case",
                    "chunk_index": step_idx,
                    "enterprise_id": DEMO_ENTERPRISE_ID,
                    "memory_type": "long_term",
                    "step": step_idx + 1,
                }],
                [chunk_id],
            )
            memory_count += 1
        total_vectors += memory_count
        print(f"  [长期记忆] 向量已写入 {memory_count} 条（7 步演示案例，分布到各 Agent）")

        print()
        print("=" * 70)
        print(f"v3 演示向量数据写入完成！")
        print(f"  企业: {enterprise.name}")
        print(f"  Agent 数: {len(agents)}")
        print(f"  总向量数: {total_vectors}")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(seed_vectors())
