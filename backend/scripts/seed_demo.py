"""初始化演示数据：构建制造业场景「示例科技」的完整演示环境。

用法：
    cd backend
    python -m scripts.seed_demo

或通过 Docker：
    docker compose exec backend python -m scripts.seed_demo

数据覆盖：
    - 1 个演示企业（示例科技）
    - 4 个用户（1 admin + 3 members）
    - 3 个智能体（生产助手 / 技术问答助手 / 会议总结助手）
    - 每个 Agent 关联 File、Skill、AgentVersion、OptimizationHistory
    - 63 条对话历史（跨 7 天，每个 Agent 21 条），含 user + assistant 消息
    - 4 条 ProcessingTask（含 1 条失败）
    - 10 条 AuditLog
"""
import asyncio
import sys
import os
import uuid
import random
import hashlib
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.database import async_session_factory
from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.models.file import File
from app.models.skill import Skill
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.agent_version import AgentVersion
from app.models.optimization_history import OptimizationHistory
from app.models.processing_task import ProcessingTask
from app.models.audit_log import AuditLog
from app.utils.security import get_password_hash


DEMO_EMAIL = "demo@autoteams.example"
DEMO_ENTERPRISE = "示例科技（演示企业）"

# P2 安全加固：演示超管口令不再硬编码默认值。
# 原先固定为 demo123456 并在 README/产品说明书中明文公示，等同于对公网暴露的
# 固定弱口令超管账号。现改为必须由部署方通过环境变量显式提供，未提供即拒绝执行，
# 避免脚本被随手 `python -m scripts.seed_demo` 跑出弱口令账号。
_MIN_DEMO_PASSWORD_LEN = 12


def _resolve_demo_password() -> str:
    """从环境变量读取演示超管口令；缺失或过弱则直接终止。"""
    raw = os.environ.get("DEMO_PASSWORD", "").strip()
    if not raw:
        raise SystemExit(
            "[seed_demo] 已拒绝执行：未设置 DEMO_PASSWORD 环境变量。\n"
            "  演示超管账号不得使用固定默认弱口令，请先设置一个强口令：\n"
            "    export DEMO_PASSWORD='<至少 %d 位的随机口令>'\n"
            "  Windows PowerShell：\n"
            "    $env:DEMO_PASSWORD='<至少 %d 位的随机口令>'"
            % (_MIN_DEMO_PASSWORD_LEN, _MIN_DEMO_PASSWORD_LEN)
        )
    if len(raw) < _MIN_DEMO_PASSWORD_LEN:
        raise SystemExit(
            "[seed_demo] 已拒绝执行：DEMO_PASSWORD 长度不足 %d 位（当前 %d 位）。"
            % (_MIN_DEMO_PASSWORD_LEN, len(raw))
        )
    return raw


DEMO_PASSWORD = _resolve_demo_password()

# ============================================================
# 数据生成辅助函数
# ============================================================


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(days: int, hour: int = 10, minute: int = 0) -> datetime:
    """返回 N 天前的指定时间（UTC）。"""
    now = _utc_now()
    target = now - timedelta(days=days)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _content_hash(*parts: str) -> str:
    """生成 64 字符 SHA256 哈希，满足 File.content_hash 长度约束。"""
    data = "|".join(parts).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ============================================================
# 业务问答内容模板（制造业场景）
# ============================================================

PRODUCTION_QA_PAIRS = [
    ("产线 A 的日产能是多少？", "根据《生产手册》第 2 章，产线 A 标准日产能为 1,200 件，两班倒模式下可达 1,800 件。实际产出受设备 OEE、物料齐套率影响。", 210, "satisfied"),
    ("质检不合格品怎么处理？", "不合格品处理流程：1. 立即隔离并贴红色标签；2. 质量部 30 分钟内完成复判；3. 判定为返工/返修/报废；4. 在 MES 系统中登记原因；5. 相关批次暂停发货。", 240, "satisfied"),
    ("安全生产有哪些硬性规定？", "《安全生产指南》要求：进入车间必须佩戴安全帽、防护鞋；设备检修执行 LOTO 上锁挂牌；危化品存放于防爆柜；每月进行一次消防演练。", 220, "satisfied"),
    ("物料清单在哪里查看？", "物料清单（BOM）可在《物料清单.xlsx》中查看，包含料号、名称、用量、供应商及替代料信息。关键物料安全库存为 3 天用量。", 190, "neutral"),
    ("设备维护周期是多久？", "设备维护按三级保养执行：日常点检每班次、一级保养每周、二级保养每月。关键设备需记录 MTBF/MTTR。", 205, "satisfied"),
    ("换线时间如何计算？", "换线时间（SMED）从最后一件产品下线开始，到首件合格品产出为止。目标值：同类换线 ≤30 分钟，跨品类换线 ≤90 分钟。", 215, "satisfied"),
    ("供应商合同的主要付款条款？", "根据《供应商合同》第 5 条：货到验收合格后 60 天付款；质量索赔可直接从货款中扣除；年度复评不合格将暂停合作。", 200, "neutral"),
    ("工艺变更通知如何下发？", "工艺变更流程：工艺部发起变更申请 → 质量/生产会签 → 试产验证 → 发布《工艺变更通知》→ 现场培训 → 旧版文件回收。", 225, "satisfied"),
    ("产线出现异常停线怎么办？", "异常停线处理：1. 操作员 5 分钟内呼叫班组长；2. 记录异常代码；3. 维修/质量/工艺 10 分钟内到场；4. 30 分钟未解决升级至车间主任。", 235, "satisfied"),
    ("新员工上岗培训包括哪些内容？", "新员工培训：安全三级教育、质量意识、岗位 SOP、设备点检、5S 要求、MES 系统操作。培训合格后签署上岗证。", 230, "satisfied"),
]

TECH_QA_PAIRS = [
    ("API 的认证方式是什么？", "API 认证使用 Bearer Token。获取方式：登录后在「设置 → API 密钥」生成。Token 有效期默认 30 天，请求头需携带：Authorization: Bearer <token>。", 240, "satisfied"),
    ("支持哪些编程语言 SDK？", "官方提供 Python、Java、Go、Node.js、C# 五种语言 SDK。社区维护 PHP、Ruby、Rust 版本。SDK 源码托管在 GitHub，支持 pip/maven/npm 安装。", 260, "satisfied"),
    ("如何处理 API 限流？", "API 限流策略：令牌桶算法，基础版 100 次/分钟，专业版 1,000 次/分钟。超限返回 429，响应头携带 X-RateLimit-Reset。建议客户端实现指数退避。", 230, "satisfied"),
    ("Webhook 如何配置？", "Webhook 配置：管理后台 → 开发者设置 → Webhook → 添加。支持事件：创建、更新、删除。系统向指定 URL 发送 POST 请求，需在 3 秒内返回 200。", 210, "satisfied"),
    ("数据库支持哪些类型？", "支持 PostgreSQL 12+、MySQL 8+、SQLite 3.35+。生产环境推荐 PostgreSQL，开发环境可用 SQLite。迁移使用 Alembic 管理。", 250, "satisfied"),
    ("如何部署到生产环境？", "生产部署推荐：1. Docker Compose 快速部署；2. Kubernetes 高可用部署。最低配置 2 核 4G，推荐 4 核 8G。详见《部署手册》。", 270, "satisfied"),
    ("监控告警如何配置？", "监控方案：内置 Prometheus /metrics 端点，暴露 QPS、延迟、错误率。配合 Grafana 可视化。健康检查端点 /health 返回各组件状态。", 220, "neutral"),
    ("支持多租户吗？", "支持多租户：基于 enterprise_id 隔离，数据层通过 Row Level Security 隔离。支持单库多租户和多库多租户两种模式。", 240, "satisfied"),
    ("备份策略是什么？", "备份策略：每日全量备份 + 每小时增量备份。备份保留 30 天，支持恢复到任意时间点。建议生产环境配置异地备份，RPO 不超过 1 小时。", 215, "neutral"),
    ("如何排查 API 错误？", "API 错误排查：1. 查看 X-Request-ID 响应头定位请求；2. 服务端日志按 request_id 过滤；3. 常见错误码见文档。500 错误请提交 issue 并附 request_id。", 230, "unsatisfied"),
    ("性能优化有哪些建议？", "性能优化建议：1. 启用连接池；2. 数据库加索引；3. 使用 Redis 缓存热点数据；4. 异步处理耗时任务；5. 启用 gzip 压缩。压测工具推荐 wrk 或 k6。", 245, "satisfied"),
    ("CI/CD 如何集成？", "CI/CD 集成：提供 GitHub Actions、GitLab CI 模板。流水线建议：lint → test → build → deploy。支持蓝绿部署和金丝雀发布。", 255, "satisfied"),
]

MEETING_QA_PAIRS = [
    ("上周产能规划会议得出什么结论？", "根据《会议纪要_产能规划.md》：Q3 目标产能提升 12%，需新增 1 条柔性产线；瓶颈工序为 SMT 贴片，计划增加 2 台设备。", 220, "satisfied"),
    ("质量复盘会议提出了哪些改善措施？", "《会议纪要_质量复盘.md》提出：1. 来料不良率从 1.2% 降至 0.8%；2. 增加首件检验频次；3. 对关键尺寸实施 SPC 控制图；4. 两周内完成供应商现场审核。", 250, "satisfied"),
    ("安全培训会议强调了哪些要点？", "《会议纪要_安全培训.md》强调：新进员工必须完成三级安全教育；叉车作业需持证；化学品泄漏应急预案每季度演练一次。", 210, "satisfied"),
    ("生产进度周会的核心指标有哪些？", "生产进度周会核心指标：计划达成率、OEE、一次合格率、工时利用率、在制品库存天数、安全事故数。", 190, "neutral"),
    ("项目启动会确定了哪些里程碑？", "《项目启动会.md》确定里程碑：M1 需求冻结（T+2 周）、M2 样机试制（T+6 周）、M3 小批量验证（T+10 周）、M4 量产移交（T+14 周）。", 230, "satisfied"),
    ("月度经营分析会议的主要决策？", "《月度经营分析.xlsx》显示：营收达成率 98%，毛利环比下降 1.5%。决策：优化 BOM 成本、压缩物流费用、推进自动化降本。", 205, "satisfied"),
    ("改善提案汇总里有哪些优秀提案？", "《改善提案汇总.pdf》收录 18 条提案，其中「快速换模工装」预计年节省 36 万元，「视觉检测替代人工」预计减少漏检 60%。", 215, "satisfied"),
    ("会议纪要的模板格式是什么？", "会议纪要标准格式：会议主题、时间地点、主持人、参会人、决议事项、责任人、完成期限、待跟进问题。决议事项需 48 小时内录入 OA。", 180, "satisfied"),
]

ALL_QA_SETS = [
    ("生产助手", PRODUCTION_QA_PAIRS),
    ("技术问答助手", TECH_QA_PAIRS),
    ("会议总结助手", MEETING_QA_PAIRS),
]

# ============================================================
# 主初始化函数
# ============================================================


async def seed():
    """主初始化入口。"""
    async with async_session_factory() as db:
        # 幂等检查：如已存在则跳过
        existing = await db.execute(select(User).where(User.email == DEMO_EMAIL))
        if existing.scalar_one_or_none():
            print(f"演示账号已存在: {DEMO_EMAIL}，如需重新初始化请先清空数据库。")
            return

        print("=" * 60)
        print("开始初始化演示数据...")
        print("=" * 60)

        enterprise = await _create_enterprise(db)
        users = await _create_users(db, enterprise)
        agents = await _create_agents(db, enterprise)
        file_map = await _create_files(db, agents)
        await _create_skills(db, agents)
        await _create_versions(db, agents, users[0])
        await _create_optimization_history(db, agents, users[0])
        await _create_processing_tasks(db, enterprise, users, agents)
        await _create_conversations_and_messages(db, users, agents, file_map)
        await _create_audit_logs(db, users, enterprise)

        await db.commit()

        print()
        print("=" * 60)
        print("演示数据初始化完成！")
        print(f"  登录邮箱: {DEMO_EMAIL}")
        # 不回显明文口令：该输出常进 CI 日志与终端录屏，回显等于二次泄露
        print(f"  登录密码: 已由 DEMO_PASSWORD 环境变量设置（不回显，{len(DEMO_PASSWORD)} 位）")
        print(f"  企业: {DEMO_ENTERPRISE}")
        print(f"  智能体数: {len(agents)}")
        print(f"  用户数: {len(users)}")
        print("=" * 60)

        # #9 触发一次样例编译：基于 example-enterprise 真实五级编译，
        # 灌入知识图谱/向量库并产出 Enterprise Runtime，使 demo 账号完成度提升、五级全通。
        # 可通过环境变量 AUTO_DEMO_COMPILE=0 关闭（LLM 未配置时可跳过）。
        if os.environ.get("AUTO_DEMO_COMPILE", "1") != "0":
            await trigger_sample_compile(db, enterprise.id)
        else:
            print("\n[提示] 已跳过样例编译（AUTO_DEMO_COMPILE=0）。")
            print("  如需手动触发，可运行：python -m scripts.seed_demo_compile <enterprise_id>")


async def trigger_sample_compile(db, enterprise_id: str) -> dict:
    """基于 example-enterprise 触发一次样例五级编译（#9）。

    编译需要 LLM 与向量库就绪；失败时记录日志而不中断演示数据初始化。
    返回 run_full 的结果字典；失败返回 None。
    """
    import os
    from app.config import settings
    from app.services.compiler.pipeline import CompilationPipeline

    default_path = os.path.join(settings.SAMPLE_DATA_DIR, "example-enterprise")
    if not os.path.isdir(default_path):
        print(f"\n[警告] 未找到示例企业数据目录，跳过样例编译：{default_path}")
        return None

    print()
    print("=" * 60)
    print("开始样例编译（基于 example-enterprise）...")
    print(f"  数据源: {default_path}")
    print("=" * 60)

    try:
        pipeline = CompilationPipeline(db, enterprise_id)
        result = await pipeline.run_full(default_path)
        completeness = result.get("completeness")
        runtime = result.get("runtime")
        print("\n[编译完成] 样例编译成功产出 Enterprise Runtime：")
        print(f"  完成度: {completeness.overall if completeness else 'N/A'}")
        print(f"  等级: {completeness.level if completeness else 'N/A'}")
        if runtime is not None:
            print(f"  岗位数: {len(runtime.agents)}")
            print(f"  流程引擎数: {len(runtime.process_engines)}")
        return result
    except Exception as e:
        print(f"\n[警告] 样例编译失败（不影响演示账号初始化）：{e}")
        return None


# ============================================================
# 各实体创建函数
# ============================================================


async def _create_enterprise(db) -> Enterprise:
    enterprise = Enterprise(name=DEMO_ENTERPRISE, is_active=True)
    db.add(enterprise)
    await db.flush()
    print(f"[1/9] 企业已创建: {DEMO_ENTERPRISE} (id={enterprise.id})")
    return enterprise


async def _create_users(db, enterprise: Enterprise) -> list[User]:
    users_data = [
        (DEMO_EMAIL, "演示管理员", "admin", 0),
        ("zhangwei@autoteams.example", "张维", "member", 1),
        ("lina@autoteams.example", "李娜", "member", 2),
        ("wangfang@autoteams.example", "王芳", "member", 3),
    ]
    users = []
    for email, name, role, days_ago in users_data:
        user = User(
            email=email,
            password_hash=get_password_hash(DEMO_PASSWORD),
            name=name,
            enterprise_id=enterprise.id,
            role=role,
            is_active=True,
            last_login_at=_days_ago(days_ago, hour=14),
        )
        db.add(user)
        users.append(user)
    await db.flush()
    print(f"[2/9] 用户已创建: {len(users)} 个（含管理员 {DEMO_EMAIL}）")
    return users


async def _create_agents(db, enterprise: Enterprise) -> list[Agent]:
    agents_data = [
        {
            "name": "生产助手",
            "description": "基于生产手册、质量标准与工艺流程，回答产能、质检、安全、设备维护等问题。",
            "system_prompt": "你是制造业生产助手，基于生产手册、质量标准、工艺流程、设备维护等知识回答问题。回答要求：1. 优先引用知识库内容，标注来源；2. 涉及产能、质检标准、安全规范时务必准确；3. 回答结构化，步骤清晰。",
            "file_count": 12,
            "knowledge_count": 156,
            "version": "1.2.0",
        },
        {
            "name": "技术问答助手",
            "description": "基于 API 文档、部署手册与运维指南，回答开发者关于认证、SDK、部署、监控等问题。",
            "system_prompt": "你是技术文档助手，基于 API 文档、SDK 指南、部署手册、运维规范回答开发者问题。回答要求：1. 提供代码示例；2. 标注文档版本；3. 如遇版本差异请说明。",
            "file_count": 15,
            "knowledge_count": 203,
            "version": "1.0.0",
        },
        {
            "name": "会议总结助手",
            "description": "基于会议纪要、经营分析与改善提案，回答决策追踪、待办事项、历史结论等问题。",
            "system_prompt": "你是会议总结助手，基于会议纪要、经营分析、改善提案回答用户关于决策、待办、历史结论的问题。回答要求：1. 引用具体会议纪要；2. 列出决策事项、责任人、期限；3. 对未决议问题明确标注。",
            "file_count": 8,
            "knowledge_count": 98,
            "version": "1.1.0",
        },
    ]
    agents = []
    for data in agents_data:
        agent = Agent(
            enterprise_id=enterprise.id,
            name=data["name"],
            description=data["description"],
            system_prompt=data["system_prompt"],
            status="ready",
            file_count=data["file_count"],
            knowledge_count=data["knowledge_count"],
            version=data["version"],
            folder_path=f"./uploads/demo/{data['name']}",
            config={"temperature": 0.3, "top_k": 5, "max_tokens": 2000, "rerank_enabled": True},
        )
        db.add(agent)
        agents.append(agent)
    await db.flush()
    print(f"[3/9] 智能体已创建: {len(agents)} 个（{', '.join(a.name for a in agents)}）")
    return agents


async def _create_files(db, agents: list[Agent]) -> dict[str, list[str]]:
    file_templates = {
        "生产助手": [
            ("生产手册.pdf", "document", 2456789, 32),
            ("质量标准.docx", "document", 456789, 8),
            ("工艺流程.pdf", "document", 678901, 12),
            ("设备操作规范.pdf", "document", 345678, 10),
            ("安全生产指南.pdf", "document", 890123, 15),
            ("物料清单.xlsx", "data", 234567, 6),
            ("供应商合同.pdf", "document", 567890, 9),
            ("生产计划表.xlsx", "data", 345678, 8),
            ("质检记录.jpg", "image", 1234567, 3),
            ("车间培训视频.mp4", "video", 5678901, 5),
            ("设备维护手册.pdf", "document", 478901, 11),
            ("工艺变更通知.pdf", "document", 234567, 7),
        ],
        "技术问答助手": [
            ("API 参考文档.pdf", "document", 2345678, 35),
            ("SDK 使用指南.pdf", "document", 1234567, 18),
            ("部署手册.pdf", "document", 890123, 14),
            ("数据库设计文档.pdf", "document", 1567890, 22),
            ("架构设计文档.pdf", "document", 1890123, 28),
            ("运维手册.pdf", "document", 567890, 9),
            ("安全规范.pdf", "document", 345678, 7),
            ("监控告警指南.pdf", "document", 456789, 8),
            ("错误码列表.xlsx", "data", 234567, 6),
            ("性能基准测试.xlsx", "data", 345678, 5),
            ("API 测试用例.json", "code", 123456, 4),
            ("部署脚本示例.py", "code", 45678, 3),
            ("配置文件模板.yaml", "code", 34567, 2),
            ("架构示意图.png", "image", 678901, 2),
            ("技术分享视频.mp4", "video", 4567890, 4),
        ],
        "会议总结助手": [
            ("会议纪要_产能规划.md", "document", 245678, 8),
            ("会议纪要_质量复盘.md", "document", 234567, 7),
            ("会议纪要_安全培训.md", "document", 189012, 6),
            ("周会_生产进度.md", "document", 156789, 5),
            ("项目启动会.md", "document", 123456, 4),
            ("月度经营分析.xlsx", "data", 345678, 8),
            ("培训材料_新员工.md", "document", 234567, 6),
            ("改善提案汇总.pdf", "document", 567890, 10),
        ],
    }

    file_map: dict[str, list[str]] = {}
    total = 0
    for agent in agents:
        templates = file_templates.get(agent.name, [])
        names = []
        for name, ftype, size, chunks in templates:
            f = File(
                agent_id=agent.id,
                original_name=name,
                file_path=f"{agent.folder_path}/{name}",
                file_size=size,
                file_type=ftype,
                status="completed",
                chunk_count=chunks,
                vector_count=chunks,
                content_hash=_content_hash(agent.name, name, str(size)),
            )
            db.add(f)
            names.append(name)
            total += 1
        file_map[agent.name] = names
    await db.flush()
    print(f"[4/9] 文件已创建: {total} 个")
    return file_map


async def _create_skills(db, agents: list[Agent]):
    total = 0
    for agent in agents:
        skills_config = [
            {
                "name": "文档摘要",
                "description": f"生成文档摘要，提取关键要点。已处理 {agent.file_count} 个文档。",
                "skill_type": "text_summarization",
                "input_type": "text",
                "output_type": "text",
                "config": {"prompt_template": "请将以下文档内容总结为简洁的摘要：\n{input}", "file_count": agent.file_count},
            },
            {
                "name": "数据提取",
                "description": f"从文档中提取结构化数据。已处理 {agent.file_count} 个文档。",
                "skill_type": "data_extraction",
                "input_type": "text",
                "output_type": "text",
                "config": {"prompt_template": "请从以下文本中提取关键信息：\n{input}", "fields": ["关键信息", "数据点", "结论"], "file_count": agent.file_count},
            },
        ]
        if agent.name in ("生产助手", "技术问答助手"):
            skills_config.append({
                "name": "图片分析",
                "description": "分析图片内容，识别其中的对象、文字和关键信息。",
                "skill_type": "custom",
                "input_type": "image",
                "output_type": "text",
                "config": {"prompt_template": "请分析这张图片的内容，识别其中的对象、文字和关键信息。", "file_count": 1},
            })
        skills_config.append({
            "name": "视频问答",
            "description": "基于视频内容回答问题，支持搜索知识点。",
            "skill_type": "custom",
            "input_type": "text",
            "output_type": "text",
            "config": {"prompt_template": "根据已处理的视频内容，回答以下问题：\n{input}", "file_count": 1},
        })
        for sc in skills_config:
            skill = Skill(
                agent_id=agent.id,
                name=sc["name"],
                description=sc["description"],
                skill_type=sc["skill_type"],
                input_type=sc["input_type"],
                output_type=sc["output_type"],
                config=sc["config"],
            )
            db.add(skill)
            total += 1
    await db.flush()
    print(f"[5/9] 技能已创建: {total} 个")


async def _create_versions(db, agents: list[Agent], admin: User):
    versions_data = {
        "生产助手": [
            ("1.0.0", "初始版本：基础问答能力", False),
            ("1.1.0", "新增质量标准和安全规范知识", False),
            ("1.2.0", "启用重排序，提升检索准确率至 91%", True),
        ],
        "技术问答助手": [
            ("1.0.0", "初始版本：API 文档与运维指南", True),
        ],
        "会议总结助手": [
            ("1.0.0", "初始版本", False),
            ("1.1.0", "补充经营分析与改善提案知识", True),
        ],
    }
    total = 0
    for agent in agents:
        versions = versions_data.get(agent.name, [])
        for ver, changelog, is_active in versions:
            v = AgentVersion(
                agent_id=agent.id,
                version=ver,
                config_snapshot={
                    "system_prompt": agent.system_prompt[:100] + "...",
                    "skills": ["文档摘要", "数据提取"],
                    "params": {"temperature": 0.3, "top_k": 5},
                },
                changelog=changelog,
                is_active=is_active,
                user_id=admin.id,
            )
            db.add(v)
            total += 1
    await db.flush()
    print(f"[6/9] 版本快照已创建: {total} 个")


async def _create_optimization_history(db, agents: list[Agent], admin: User):
    history_data = {
        "生产助手": [
            ("feedback", {"负面反馈数": 5, "高频问题": ["产能计算", "不合格品处理"]}, {"建议": "补充产能计算示例与不合格品处理流程图"}, True),
            ("gap", {"查询无结果": ["换线时间", "设备 MTBF"]}, {"建议": "新增 SMED 换线文档与设备 MTBF 统计表"}, True),
            ("optimization", {"检索准确率": 0.82, "平均响应时间": 1200}, {"调整": "启用重排序，top_k 调整为 5，响应时间降低至 850ms"}, True),
        ],
        "技术问答助手": [
            ("feedback", {"负面反馈数": 3, "高频问题": ["Webhook 重试策略"]}, {"建议": "补充 Webhook 重试与签名验证文档"}, False),
            ("gap", {"查询无结果": ["错误码完整列表"]}, {"建议": "补充错误码文档并关联 SDK 示例"}, False),
        ],
        "会议总结助手": [
            ("feedback", {"负面反馈数": 2, "高频问题": ["待办追踪"]}, {"建议": "优化会议纪要中的待办项结构化输出"}, False),
        ],
    }
    total = 0
    for agent in agents:
        histories = history_data.get(agent.name, [])
        for i, (type_, input_data, output_data, applied) in enumerate(histories):
            h = OptimizationHistory(
                agent_id=agent.id,
                user_id=admin.id,
                type=type_,
                input_data=input_data,
                output_data=output_data,
                applied=applied,
                applied_at=_days_ago(5 - i * 2) if applied else None,
            )
            db.add(h)
            total += 1
    await db.flush()
    print(f"[7/9] 优化历史已创建: {total} 个")


async def _create_processing_tasks(db, enterprise: Enterprise, users: list[User], agents: list[Agent]):
    tasks_data = [
        (agents[0], users[0], "completed", 1.0, 12, 0, 156, _days_ago(6, 10), _days_ago(6, 11), None),
        (agents[1], users[0], "completed", 1.0, 15, 0, 203, _days_ago(6, 10), _days_ago(6, 12), None),
        (agents[2], users[0], "completed", 1.0, 8, 0, 98, _days_ago(6, 10), _days_ago(6, 10, 45), None),
        (agents[0], users[1], "failed", 0.6, 10, 4, 0, _days_ago(3, 14), None, [
            {"file": "损坏文档.pdf", "error": "PDF 解析失败：文件已加密", "timestamp": _days_ago(3, 14, 30).isoformat()},
            {"file": "大文件.xlsx", "error": "文件超过最大处理限制(50MB)", "timestamp": _days_ago(3, 14, 35).isoformat()},
        ]),
    ]
    total = 0
    for agent, user, status, progress, total_files, failed, knowledge, started, completed, error_log in tasks_data:
        t = ProcessingTask(
            user_id=user.id,
            enterprise_id=enterprise.id,
            agent_id=agent.id if status == "completed" else None,
            folder_path=agent.folder_path,
            agent_name=agent.name,
            agent_description=agent.description,
            status=status,
            progress=progress,
            message="处理完成" if status == "completed" else "处理失败：部分文件无法解析",
            total_files=total_files,
            processed_files=total_files - failed,
            failed_files=failed,
            knowledge_count=knowledge,
            processing_time_seconds=3600 if status == "completed" else 1800,
            started_at=started,
            completed_at=completed,
            error_log=error_log,
        )
        db.add(t)
        total += 1
    await db.flush()
    print(f"[8/9] 处理任务已创建: {total} 个（含 1 条失败）")


async def _create_conversations_and_messages(
    db,
    users: list[User],
    agents: list[Agent],
    file_map: dict[str, list[str]],
):
    """创建 7 天的对话历史，每个 Agent 21 条会话，共 63 条。"""
    # 每天每 agent 的会话数，合计 21
    daily_distribution = [2, 2, 3, 3, 4, 4, 3]
    satisfaction_distribution = ["satisfied", "satisfied", "satisfied", "neutral", "unsatisfied"]
    token_buckets = [40, 80, 120, 180, 250, 350, 450, 600, 700]

    total_conversations = 0
    total_messages = 0

    for agent, (_, qa_pairs) in zip(agents, ALL_QA_SETS):
        qa_index = 0
        agent_files = file_map.get(agent.name, [])
        for day_offset, daily_count in enumerate(daily_distribution):
            day = 6 - day_offset
            for conv_idx in range(daily_count):
                user = users[(conv_idx % 3) + 1]

                qa = qa_pairs[qa_index % len(qa_pairs)]
                qa_index += 1
                question, answer, base_token, _ = qa

                conv_hour = 9 + (conv_idx % 8)
                conv_time = _days_ago(day, hour=conv_hour, minute=random.randint(0, 59))

                conversation = Conversation(
                    user_id=user.id,
                    agent_id=agent.id,
                    title=question[:20] + ("..." if len(question) > 20 else ""),
                )
                conversation.created_at = conv_time
                conversation.updated_at = conv_time + timedelta(minutes=random.randint(1, 10))
                db.add(conversation)
                await db.flush()
                total_conversations += 1

                user_msg = Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=question,
                    token_count=random.randint(15, 50),
                    model_used=None,
                    is_deleted=False,
                )
                user_msg.created_at = conv_time
                db.add(user_msg)

                assistant_time = msg_time = conv_time + timedelta(seconds=random.randint(1, 5))
                token_count = random.choice(token_buckets)
                satisfaction = random.choice(satisfaction_distribution)

                sources = None
                if agent_files and random.random() < 0.7:
                    sources = [{
                        "file_name": random.choice(agent_files),
                        "page": random.randint(1, 20),
                        "content": "相关内容摘录片段...",
                    }]

                assistant_msg = Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=answer,
                    sources=sources,
                    satisfaction=satisfaction,
                    token_count=token_count,
                    model_used=random.choice(["gpt-4", "gpt-3.5-turbo"]),
                    is_deleted=False,
                )
                assistant_msg.created_at = assistant_time
                db.add(assistant_msg)
                total_messages += 2

    await db.flush()
    print(f"[9/9] 对话历史已创建: {total_conversations} 条会话, {total_messages} 条消息（跨 7 天）")


async def _create_audit_logs(db, users: list[User], enterprise: Enterprise):
    logs_data = [
        (users[0], "login", "user", None, {"email": DEMO_EMAIL}, _days_ago(0, 9)),
        (users[0], "create", "agent", None, {"name": "生产助手"}, _days_ago(6, 10)),
        (users[0], "update", "agent", None, {"field": "system_prompt"}, _days_ago(4, 14)),
        (users[0], "delete", "file", None, {"file_name": "过期文档.pdf"}, _days_ago(3, 16)),
        (users[0], "export", "conversation", None, {"count": 10}, _days_ago(2, 15)),
        (users[1], "login", "user", None, {"email": "zhangwei@autoteams.example"}, _days_ago(1, 14)),
        (users[2], "login", "user", None, {"email": "lina@autoteams.example"}, _days_ago(2, 10)),
        (users[0], "invite", "enterprise", None, {"invitee": "wangfang@autoteams.example"}, _days_ago(3, 11)),
        (users[0], "update", "skill", None, {"skill": "文档摘要"}, _days_ago(2, 13)),
        (users[3], "login", "user", None, {"email": "wangfang@autoteams.example"}, _days_ago(3, 9)),
    ]
    for user, action, resource_type, resource_id, details, log_time in logs_data:
        log = AuditLog(
            user_id=user.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id or uuid.uuid4().hex,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            details=details,
        )
        log.created_at = log_time
        db.add(log)
    await db.flush()
    print(f"[附加] 审计日志已创建: {len(logs_data)} 条")


if __name__ == "__main__":
    asyncio.run(seed())
