"""向 ChromaDB 写入演示向量数据，使 RAG 检索可演示。

用法：
    cd backend
    python -m scripts.seed_vectors

注意：
    - 需先运行 seed_demo.py 创建 Agent / File 记录
    - 首次运行会下载 embedding 模型（约 1.3GB，取决于 EMBEDDING_MODEL）
    - 如模型下载失败，将使用 ChromaDB 默认英文模型（效果略差）
"""
import asyncio
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session_factory
from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.file import File
from app.services.vector_store import VectorStoreService


DEMO_ENTERPRISE = "示例科技（演示企业）"

# ============================================================
# 演示知识片段：按文件名索引，内容覆盖 seed_demo.py 中的 QA
# ============================================================

PRODUCTION_CHUNKS: dict[str, list[str]] = {
    "生产手册.pdf": [
        "产线 A 标准日产能为 1,200 件，两班倒模式下可达 1,800 件。",
        "实际产出受设备 OEE、物料齐套率影响，需每日核对生产计划达成率。",
    ],
    "质量标准.docx": [
        "不合格品处理流程：立即隔离并贴红色标签；质量部 30 分钟内完成复判。",
        "判定结果分为返工、返修、报废三类，相关批次暂停发货。",
    ],
    "工艺流程.pdf": [
        "工艺变更流程：工艺部发起变更申请 → 质量/生产会签 → 试产验证 → 发布通知 → 现场培训 → 旧版文件回收。",
    ],
    "设备操作规范.pdf": [
        "设备维护按三级保养执行：日常点检每班次、一级保养每周、二级保养每月。",
        "关键设备需记录 MTBF/MTTR，异常停线 30 分钟未解决升级至车间主任。",
    ],
    "安全生产指南.pdf": [
        "进入车间必须佩戴安全帽、防护鞋；设备检修执行 LOTO 上锁挂牌。",
        "危化品存放于防爆柜；每月进行一次消防演练。",
    ],
    "物料清单.xlsx": [
        "物料清单（BOM）包含料号、名称、用量、供应商及替代料信息。",
        "关键物料安全库存为 3 天用量。",
    ],
    "供应商合同.pdf": [
        "付款条款：货到验收合格后 60 天付款；质量索赔可直接从货款中扣除。",
        "年度复评不合格将暂停合作。",
    ],
    "生产计划表.xlsx": [
        "换线时间（SMED）从最后一件产品下线开始，到首件合格品产出为止。",
        "同类换线目标 ≤30 分钟，跨品类换线目标 ≤90 分钟。",
    ],
    "设备维护手册.pdf": [
        "设备维护周期：日常点检每班次、一级保养每周、二级保养每月。",
    ],
    "工艺变更通知.pdf": [
        "工艺变更通知发布后需现场培训并回收旧版文件。",
    ],
}

TECH_CHUNKS: dict[str, list[str]] = {
    "API 参考文档.pdf": [
        "API 认证使用 Bearer Token。请求头需携带：Authorization: Bearer <token>。",
        "Token 有效期默认 30 天，可在设置 → API 密钥中生成。",
    ],
    "SDK 使用指南.pdf": [
        "官方提供 Python、Java、Go、Node.js、C# 五种语言 SDK。",
        "SDK 源码托管在 GitHub，支持 pip/maven/npm 安装。",
    ],
    "部署手册.pdf": [
        "生产部署推荐 Docker Compose 或 Kubernetes 高可用部署。",
        "最低配置 2 核 4G，推荐 4 核 8G。",
    ],
    "数据库设计文档.pdf": [
        "支持 PostgreSQL 12+、MySQL 8+、SQLite 3.35+。",
        "生产环境推荐 PostgreSQL，迁移使用 Alembic 管理。",
    ],
    "架构设计文档.pdf": [
        "支持多租户：基于 enterprise_id 隔离，数据层通过 Row Level Security 隔离。",
    ],
    "运维手册.pdf": [
        "备份策略：每日全量备份 + 每小时增量备份，保留 30 天。",
    ],
    "安全规范.pdf": [
        "API 错误排查：查看 X-Request-ID 响应头定位请求，服务端日志按 request_id 过滤。",
    ],
    "监控告警指南.pdf": [
        "内置 Prometheus /metrics 端点，暴露 QPS、延迟、错误率，配合 Grafana 可视化。",
    ],
    "性能基准测试.xlsx": [
        "性能优化建议：启用连接池、数据库加索引、Redis 缓存热点数据、异步处理耗时任务、启用 gzip 压缩。",
    ],
    "API 测试用例.json": [
        "API 限流策略：令牌桶算法，基础版 100 次/分钟，专业版 1,000 次/分钟，超限返回 429。",
    ],
}

MEETING_CHUNKS: dict[str, list[str]] = {
    "会议纪要_产能规划.md": [
        "Q3 目标产能提升 12%，需新增 1 条柔性产线；瓶颈工序为 SMT 贴片，计划增加 2 台设备。",
    ],
    "会议纪要_质量复盘.md": [
        "来料不良率从 1.2% 降至 0.8%；增加首件检验频次；对关键尺寸实施 SPC 控制图。",
    ],
    "会议纪要_安全培训.md": [
        "新进员工必须完成三级安全教育；叉车作业需持证；化学品泄漏应急预案每季度演练一次。",
    ],
    "周会_生产进度.md": [
        "生产进度周会核心指标：计划达成率、OEE、一次合格率、工时利用率、在制品库存天数、安全事故数。",
    ],
    "项目启动会.md": [
        "里程碑：M1 需求冻结（T+2 周）、M2 样机试制（T+6 周）、M3 小批量验证（T+10 周）、M4 量产移交（T+14 周）。",
    ],
    "月度经营分析.xlsx": [
        "月度经营分析：营收达成率 98%，毛利环比下降 1.5%；决策优化 BOM 成本、压缩物流费用、推进自动化降本。",
    ],
    "培训材料_新员工.md": [
        "新员工培训包括安全三级教育、质量意识、岗位 SOP、设备点检、5S 要求、MES 系统操作。",
    ],
    "改善提案汇总.pdf": [
        "改善提案汇总收录 18 条提案，「快速换模工装」预计年节省 36 万元，「视觉检测替代人工」预计减少漏检 60%。",
    ],
}

AGENT_CHUNK_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "生产助手": PRODUCTION_CHUNKS,
    "技术问答助手": TECH_CHUNKS,
    "会议总结助手": MEETING_CHUNKS,
}


def _fallback_chunks(agent_name: str, file_name: str, file_type: str) -> list[str]:
    """为没有预定义片段的文件生成通用占位片段。"""
    return [
        f"【{agent_name}】文件《{file_name}》摘要：该文件用于支撑 {agent_name} 的知识库问答。",
        f"文件类型：{file_type}，是 {agent_name} 知识体系的重要组成部分。",
    ]


async def seed_vectors():
    """主入口：为演示企业的所有 Agent 生成向量数据。"""
    async with async_session_factory() as db:
        enterprise_result = await db.execute(
            select(Enterprise).where(Enterprise.name == DEMO_ENTERPRISE)
        )
        enterprise = enterprise_result.scalar_one_or_none()
        if not enterprise:
            print(f"演示企业不存在：{DEMO_ENTERPRISE}，请先运行 seed_demo.py。")
            return

        agents_result = await db.execute(
            select(Agent)
            .where(Agent.enterprise_id == enterprise.id)
            .options(selectinload(Agent.files))
        )
        agents = agents_result.scalars().all()
        if not agents:
            print("未找到任何演示 Agent，请先运行 seed_demo.py。")
            return

        print("=" * 60)
        print("开始向 ChromaDB 写入演示向量数据...")
        print("=" * 60)

        total_vectors = 0
        for agent in agents:
            collection_name = f"agent_{agent.id}"
            vector_store = await VectorStoreService.create(collection_name)

            # 幂等：若已有数据则清空后重建，避免重复
            existing = await vector_store.count()
            if existing > 0:
                print(f"  [{agent.name}] 集合已存在 {existing} 条向量，清空后重建...")
                await vector_store.delete_collection()
                vector_store = await VectorStoreService.create(collection_name)

            templates = AGENT_CHUNK_TEMPLATES.get(agent.name, {})
            documents: list[str] = []
            metadatas: list[dict] = []
            ids: list[str] = []

            for file in agent.files:
                if file.status != "completed":
                    continue
                chunks = templates.get(file.original_name)
                if not chunks:
                    chunks = _fallback_chunks(agent.name, file.original_name, file.file_type)

                # 以 File 记录中的 chunk_count 为准，如预定义片段不足则循环补齐
                target_count = file.chunk_count or len(chunks)
                for i in range(target_count):
                    chunk_text = chunks[i % len(chunks)]
                    chunk_id = f"{agent.id}_{file.id}_{i}"
                    documents.append(chunk_text)
                    metadatas.append(
                        {
                            "source": file.original_name,
                            "file_path": file.file_path,
                            "file_type": file.file_type,
                            "chunk_index": i,
                        }
                    )
                    ids.append(chunk_id)

            if not documents:
                print(f"  [{agent.name}] 没有可写入的向量片段，跳过。")
                continue

            await vector_store.add_documents(documents, metadatas, ids)
            total_vectors += len(documents)
            print(f"  [{agent.name}] 已写入 {len(documents)} 条向量")

        print()
        print("=" * 60)
        print(f"演示向量数据写入完成！共 {len(agents)} 个 Agent，{total_vectors} 条向量。")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(seed_vectors())
