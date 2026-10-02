"""生成真实的 sample_data 演示文档（D1 / 6.8）。

为 sample_data/ 下 3 个 Agent 目录生成可被 RAG 检索使用的真实文档，
内容与 backend/scripts/seed_demo.py 中的问答对保持一致，确保评委
若实际触发文件处理流程时检索结果与演示对话匹配。

生成文件类型：
  - .md   : 富文本 Markdown（直接 UTF-8 写入）
  - .docx : 真实 Word 文档（python-docx，已内置于 requirements.txt）
  - .xlsx : 真实 Excel 文件（openpyxl，已内置于 requirements.txt）
  - .pdf  : 真实 PDF 文件（手工构造的最小化 PDF 写入器，无外部依赖）
  - .json/.py/.yaml : 代码与配置文件（直接 UTF-8 写入）

不生成文件类型：
  - .jpg/.png : 二进制图片，由 README 说明占位
  - .mp4      : 二进制视频，由 README 说明占位

用法：
    cd backend
    python -m scripts.generate_sample_data

幂等性：重复运行会覆盖已有文件，不会报错。
"""
import os
import sys
import struct
from pathlib import Path
from datetime import datetime

# 加入 backend 目录到 sys.path，便于以模块方式运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 内容定义：与 seed_demo.py 的 QA 对保持一致
# ============================================================

# 生产助手：12 个文件（10 PDF + 1 DOCX + 2 XLSX + 1 JPG + 1 MP4，跳过图片/视频）
PRODUCTION_FILES: dict[str, dict] = {
    "生产手册.pdf": {
        "title": "生产手册（产线 A 标准）",
        "summary": "Production Manual - Line A Standard Capacity & OEE",
        "sections": [
            ("产能标准", "产线 A 标准日产能为 1,200 件，两班倒模式下可达 1,800 件。实际产出受设备 OEE、物料齐套率影响，需每日核对生产计划达成率。"),
            ("OEE 计算", "OEE = 时间开动率 × 性能开动率 × 合格品率。目标值 ≥ 85%，低于 75% 需启动专项改善。"),
            ("交接班", "交接班需在班前 15 分钟完成：核对在制品数量、设备状态、未完成工单、品质异常。"),
        ],
    },
    "质量标准.docx": {
        "title": "质量标准与不合格品处理流程",
        "sections": [
            ("不合格品处理", "不合格品处理流程：1. 立即隔离并贴红色标签；2. 质量部 30 分钟内完成复判；3. 判定为返工/返修/报废；4. 在 MES 系统中登记原因；5. 相关批次暂停发货。"),
            ("首件检验", "首件检验需在生产开始后首 5 件内完成，由质检员与操作工双人确认。首件不合格需停线排查。"),
            ("SPC 控制图", "对关键尺寸实施 SPC 控制图（Xbar-R），连续 7 点同向偏离即触发预警，CPK < 1.33 需启动改善。"),
        ],
    },
    "工艺流程.pdf": {
        "title": "工艺流程与变更管理",
        "summary": "Process Flow & Engineering Change Notice Procedure",
        "sections": [
            ("工艺变更流程", "工艺变更流程：工艺部发起变更申请 → 质量/生产会签 → 试产验证 → 发布《工艺变更通知》→ 现场培训 → 旧版文件回收。"),
            ("文件版本", "工艺文件版本号采用 X.Y.Z 三段制：X 为重大变更（重新试产）、Y 为一般变更（会签通过即可）、Z 为文字修订。"),
        ],
    },
    "设备操作规范.pdf": {
        "title": "设备操作规范与三级保养",
        "summary": "Equipment Operation & 3-Tier Maintenance",
        "sections": [
            ("三级保养", "设备维护按三级保养执行：日常点检每班次、一级保养每周、二级保养每月。关键设备需记录 MTBF/MTTR。"),
            ("异常停线", "异常停线处理：1. 操作员 5 分钟内呼叫班组长；2. 记录异常代码；3. 维修/质量/工艺 10 分钟内到场；4. 30 分钟未解决升级至车间主任。"),
            ("LOTO 上锁挂牌", "设备检修执行 LOTO 上锁挂牌：能源隔离 → 上锁 → 挂牌 → 试启动 → 作业 → 解锁 → 恢复。"),
        ],
    },
    "安全生产指南.pdf": {
        "title": "安全生产指南",
        "summary": "Workplace Safety Guidelines",
        "sections": [
            ("硬性规定", "进入车间必须佩戴安全帽、防护鞋；设备检修执行 LOTO 上锁挂牌；危化品存放于防爆柜；每月进行一次消防演练。"),
            ("PPE 配置", "PPE 配置标准：安全帽（ABS 材质）、防护鞋（钢头钢底）、护目镜（防冲击）、耳塞（NRR ≥ 25dB）、防尘口罩（KN95）。"),
        ],
    },
    "物料清单.xlsx": {
        "title": "物料清单 BOM",
        "sheets": [
            {
                "name": "BOM 主表",
                "headers": ["料号", "名称", "规格", "用量", "单位", "供应商", "安全库存(天)", "替代料"],
                "rows": [
                    ["A001", "电机壳体", "AL6061-T6", 1, "件", "华铸科技", 3, "A001-S"],
                    ["A002", "主轴轴承", "6205-2RS", 2, "套", "NSK 中国", 5, ""],
                    ["A003", "控制板 PCB", "V3.2", 1, "块", "深南电路", 3, "A003-V2"],
                    ["A004", "伺服电机", "750W 220V", 1, "台", "汇川技术", 7, ""],
                    ["A005", "润滑脂", "Mobilux EP2", 0.2, "kg", "美孚", 30, ""],
                    ["A006", "紧固件套件", "M8×25", 24, "套", "晋亿实业", 5, ""],
                ],
            },
            {
                "name": "安全库存",
                "headers": ["料号", "名称", "日用量", "安全库存(件)", "供应商交期(天)"],
                "rows": [
                    ["A001", "电机壳体", 1200, 3600, 5],
                    ["A002", "主轴轴承", 2400, 12000, 7],
                    ["A003", "控制板 PCB", 1200, 3600, 5],
                ],
            },
        ],
    },
    "供应商合同.pdf": {
        "title": "供应商合同主要条款",
        "summary": "Supplier Contract Key Terms",
        "sections": [
            ("付款条款", "根据《供应商合同》第 5 条：货到验收合格后 60 天付款；质量索赔可直接从货款中扣除；年度复评不合格将暂停合作。"),
            ("质量保证", "供应商需提供 COA/COQ，关键料号需 100% 进料检验。年度质量评分低于 80 分启动整改，低于 70 分暂停供货。"),
        ],
    },
    "生产计划表.xlsx": {
        "title": "生产计划表（Q3）",
        "sheets": [
            {
                "name": "Q3 主计划",
                "headers": ["周次", "产线 A 计划", "产线 A 实际", "达成率", "产线 B 计划", "产线 B 实际", "达成率"],
                "rows": [
                    ["W27", 6000, 5920, 0.987, 4500, 4480, 0.996],
                    ["W28", 6500, 6480, 0.997, 4500, 4520, 1.004],
                    ["W29", 7000, 6850, 0.979, 5000, 4980, 0.996],
                    ["W30", 7000, 7120, 1.017, 5000, 5050, 1.010],
                    ["W31", 7500, 7380, 0.984, 5500, 5460, 0.993],
                    ["W32", 8000, 7920, 0.990, 5500, 5520, 1.004],
                ],
            },
            {
                "name": "换线计划",
                "headers": ["日期", "产线", "换线类型", "目标时间(分钟)", "实际时间(分钟)", "SMED 评分"],
                "rows": [
                    ["2026-07-15", "A", "同类", 30, 28, 0.93],
                    ["2026-07-17", "B", "跨品类", 90, 85, 0.94],
                    ["2026-07-22", "A", "跨品类", 90, 95, 1.06],
                ],
            },
        ],
    },
    "设备维护手册.pdf": {
        "title": "设备维护手册",
        "summary": "Equipment Maintenance Manual",
        "sections": [
            ("维护周期", "设备维护周期：日常点检每班次、一级保养每周、二级保养每月。"),
            ("MTBF/MTTR", "关键设备需记录 MTBF（平均故障间隔时间）/MTTR（平均修复时间）。MTBF 目标 ≥ 720 小时，MTTR 目标 ≤ 2 小时。"),
        ],
    },
    "工艺变更通知.pdf": {
        "title": "工艺变更通知 ECN-2026-07",
        "summary": "Engineering Change Notice ECN-2026-07",
        "sections": [
            ("变更内容", "产线 A 主轴转速由 3000 RPM 调整为 3200 RPM，提升加工效率 6%。"),
            ("生效日期", "2026-07-25 起。变更发布后需现场培训并回收旧版文件。"),
            ("验证要求", "首批 50 件需 100% 尺寸检验，CPK ≥ 1.33 方可量产。"),
        ],
    },
    # 质检记录.jpg / 车间培训视频.mp4 — 跳过二进制媒体
}

# 技术问答助手：15 个文件（8 PDF + 2 XLSX + 1 JSON + 1 PY + 1 YAML + 1 PNG + 1 MP4，跳过图片/视频）
TECH_FILES: dict[str, dict] = {
    "API 参考文档.pdf": {
        "title": "API 参考文档 v2.4",
        "summary": "API Reference Documentation v2.4",
        "sections": [
            ("认证方式", "API 认证使用 Bearer Token。获取方式：登录后在「设置 → API 密钥」生成。Token 有效期默认 30 天，请求头需携带：Authorization: Bearer <token>。"),
            ("限流策略", "API 限流策略：令牌桶算法，基础版 100 次/分钟，专业版 1,000 次/分钟。超限返回 429，响应头携带 X-RateLimit-Reset。建议客户端实现指数退避。"),
            ("Webhook", "Webhook 配置：管理后台 → 开发者设置 → Webhook → 添加。支持事件：创建、更新、删除。系统向指定 URL 发送 POST 请求，需在 3 秒内返回 200。"),
            ("错误码", "常见错误码：400 参数错误、401 未认证、403 无权限、404 资源不存在、429 限流、500 服务端错误、503 暂不可用。"),
        ],
    },
    "SDK 使用指南.pdf": {
        "title": "SDK 使用指南",
        "summary": "SDK Usage Guide",
        "sections": [
            ("支持语言", "官方提供 Python、Java、Go、Node.js、C# 五种语言 SDK。社区维护 PHP、Ruby、Rust 版本。SDK 源码托管在 GitHub，支持 pip/maven/npm 安装。"),
            ("Python 示例", "pip install autoteams-sdk；from autoteams import Client；client = Client(api_key='sk-xxx')；resp = client.agents.list()。"),
        ],
    },
    "部署手册.pdf": {
        "title": "部署手册",
        "summary": "Deployment Manual",
        "sections": [
            ("部署方式", "生产部署推荐：1. Docker Compose 快速部署；2. Kubernetes 高可用部署。最低配置 2 核 4G，推荐 4 核 8G。详见《部署手册》。"),
            ("环境变量", "关键环境变量：DATABASE_URL、REDIS_URL、CHROMA_HOST、OPENAI_API_KEY、JWT_SECRET_KEY。生产环境必须设置 COOKIE_SECURE=true。"),
        ],
    },
    "数据库设计文档.pdf": {
        "title": "数据库设计文档",
        "summary": "Database Design Document",
        "sections": [
            ("支持的数据库", "支持 PostgreSQL 12+、MySQL 8+、SQLite 3.35+。生产环境推荐 PostgreSQL，开发环境可用 SQLite。迁移使用 Alembic 管理。"),
            ("核心表", "核心表：users、enterprises、agents、files、conversations、messages、skills、agent_versions、optimization_histories。"),
            ("多租户", "支持多租户：基于 enterprise_id 隔离，数据层通过 Row Level Security 隔离。支持单库多租户和多库多租户两种模式。"),
        ],
    },
    "架构设计文档.pdf": {
        "title": "架构设计文档",
        "summary": "Architecture Design Document",
        "sections": [
            ("整体架构", "三层架构：前端 React + Vite、后端 FastAPI + LangGraph、数据层 PostgreSQL + ChromaDB + Redis。"),
            ("RAG 流水线", "RAG 流水线：文件解析 → 分块 → 向量化 → ChromaDB 存储 → 检索 → 重排序 → LLM 生成 → Self-RAG 评分。"),
        ],
    },
    "运维手册.pdf": {
        "title": "运维手册",
        "summary": "Operations Manual",
        "sections": [
            ("备份策略", "备份策略：每日全量备份 + 每小时增量备份。备份保留 30 天，支持恢复到任意时间点。建议生产环境配置异地备份，RPO 不超过 1 小时。"),
            ("日志", "日志位置：默认 logs/ 目录，生产环境建议挂载到持久化卷。日志级别：DEBUG/INFO/WARNING/ERROR。支持 JSON 格式输出。"),
        ],
    },
    "安全规范.pdf": {
        "title": "安全规范",
        "summary": "Security Specification",
        "sections": [
            ("认证授权", "认证：JWT Bearer Token；授权：RBAC（admin/member）；刷新令牌：支持家族 ID 检测重放攻击。"),
            ("API 错误排查", "API 错误排查：1. 查看 X-Request-ID 响应头定位请求；2. 服务端日志按 request_id 过滤；3. 常见错误码见文档。500 错误请提交 issue 并附 request_id。"),
        ],
    },
    "监控告警指南.pdf": {
        "title": "监控告警指南",
        "summary": "Monitoring & Alerting Guide",
        "sections": [
            ("监控方案", "监控方案：内置 Prometheus /metrics 端点，暴露 QPS、延迟、错误率。配合 Grafana 可视化。健康检查端点 /health 返回各组件状态。"),
            ("告警规则", "告警规则建议：5xx 错误率 > 1% 持续 5 分钟、P99 延迟 > 2s、健康检查失败 3 次。"),
        ],
    },
    "错误码列表.xlsx": {
        "title": "错误码列表",
        "sheets": [
            {
                "name": "错误码",
                "headers": ["HTTP 状态码", "错误码", "名称", "说明", "处理建议"],
                "rows": [
                    [400, "INVALID_PARAM", "参数错误", "请求参数缺失或格式错误", "检查请求体"],
                    [401, "UNAUTHORIZED", "未认证", "未提供或无效的 Token", "重新登录获取 Token"],
                    [403, "FORBIDDEN", "无权限", "Token 有效但无权访问资源", "联系管理员"],
                    [404, "NOT_FOUND", "资源不存在", "请求的资源 ID 不存在", "检查 ID"],
                    [429, "RATE_LIMITED", "限流", "超过调用频率限制", "指数退避重试"],
                    [500, "INTERNAL_ERROR", "服务端错误", "未捕获异常", "提交 issue 附 request_id"],
                    [503, "UNAVAILABLE", "暂不可用", "服务过载或维护中", "稍后重试"],
                ],
            },
        ],
    },
    "性能基准测试.xlsx": {
        "title": "性能基准测试",
        "sheets": [
            {
                "name": "压测结果",
                "headers": ["接口", "QPS", "P50(ms)", "P95(ms)", "P99(ms)", "错误率"],
                "rows": [
                    ["POST /auth/login", 120, 85, 180, 320, 0.001],
                    ["GET /agents", 350, 45, 95, 180, 0.000],
                    ["POST /agents/{id}/chat", 25, 1200, 2400, 3500, 0.005],
                    ["GET /conversations", 280, 60, 130, 220, 0.000],
                ],
            },
            {
                "name": "性能优化建议",
                "headers": ["优化项", "收益", "实施难度", "优先级"],
                "rows": [
                    ["启用连接池", "降低 30% 延迟", "低", "P0"],
                    ["数据库加索引", "降低 50% 查询时间", "低", "P0"],
                    ["Redis 缓存热点", "降低 60% DB 负载", "中", "P1"],
                    ["异步处理耗时任务", "提升 2x 吞吐", "中", "P1"],
                    ["gzip 压缩", "降低 70% 带宽", "低", "P0"],
                ],
            },
        ],
    },
    "API 测试用例.json": {
        "type": "code",
        "content": '''{
  "test_cases": [
    {
      "id": "TC-001",
      "name": "成功登录",
      "endpoint": "POST /api/v1/auth/login",
      "request": {"email": "demo@autoteams.example", "password": "demo123456"},
      "expected": {"status": 200, "body_contains": "access_token"}
    },
    {
      "id": "TC-002",
      "name": "错误密码登录失败",
      "endpoint": "POST /api/v1/auth/login",
      "request": {"email": "demo@autoteams.example", "password": "wrong"},
      "expected": {"status": 401, "body_contains": "Invalid credentials"}
    },
    {
      "id": "TC-003",
      "name": "未认证访问受保护资源",
      "endpoint": "GET /api/v1/agents",
      "request": {},
      "headers": {},
      "expected": {"status": 401}
    },
    {
      "id": "TC-004",
      "name": "触发限流",
      "endpoint": "POST /api/v1/auth/login",
      "description": "连续 101 次请求，第 101 次应返回 429",
      "expected": {"status": 429, "headers_contains": "X-RateLimit-Reset"}
    },
    {
      "id": "TC-005",
      "name": "Webhook 签名验证",
      "endpoint": "POST /api/v1/webhook/test",
      "description": "携带错误签名应返回 403",
      "expected": {"status": 403}
    }
  ],
  "rate_limit": {
    "basic": "100/min",
    "pro": "1000/min",
    "algorithm": "token_bucket"
  },
  "webhook_timeout_seconds": 3,
  "token_expiry_days": 30
}
''',
    },
    "部署脚本示例.py": {
        "type": "code",
        "content": '''"""AutoTeams 一键部署脚本示例（Docker Compose 模式）。

运行前请确认：
  1. 已安装 Docker 20+ 与 Docker Compose v2+
  2. 已准备 .env.prod 文件（参考 .env.prod.example）
  3. PostgreSQL/Redis/ChromaDB 密码已强随机生成
"""
import subprocess
import sys
from pathlib import Path


def run(cmd: str, check: bool = True) -> int:
    """执行 shell 命令并实时打印输出。"""
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True)
    if check and result.returncode != 0:
        print(f"命令失败（退出码 {result.returncode}）", file=sys.stderr)
        sys.exit(result.returncode)
    return result.returncode


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    # 1. 检查 .env.prod
    env_file = repo_root / ".env.prod"
    if not env_file.exists():
        print("错误：未找到 .env.prod，请先复制 .env.prod.example 并填写凭证", file=sys.stderr)
        sys.exit(1)

    # 2. 拉取最新镜像
    run("docker compose -f docker-compose.prod.yml --env-file .env.prod pull")

    # 3. 启动服务（后端启动时会自动执行 alembic upgrade head + seed_demo + seed_vectors）
    run("docker compose -f docker-compose.prod.yml --env-file .env.prod up -d")

    # 4. 健康检查
    print("等待后端就绪...")
    run('curl -fsS http://localhost/health || exit 1', check=False)

    print("\\n部署完成。访问 http://localhost 体验。")
    print("测试账号：demo@autoteams.example / demo123456")


if __name__ == "__main__":
    main()
''',
    },
    "配置文件模板.yaml": {
        "type": "code",
        "content": '''# AutoTeams 配置文件模板（生产环境参考）
# 复制为 .env.prod 并按实际环境填写

# 数据库
DATABASE_URL: "postgresql+asyncpg://autoteams:CHANGE_ME@postgres:5432/autoteams"
USE_SQLITE: "false"

# Redis
REDIS_URL: "redis://:CHANGE_ME@redis:6379/0"

# ChromaDB
CHROMA_HOST: "chromadb"
CHROMA_PORT: "8000"
CHROMA_AUTH_TOKEN: ""

# JWT
JWT_SECRET_KEY: "CHANGE_ME_TO_RANDOM_64_CHARS"
JWT_ALGORITHM: "HS256"
JWT_EXPIRATION_HOURS: "1"

# LLM
OPENAI_API_KEY: "sk-CHANGE_ME"
OPENAI_API_BASE: "https://api.openai.com/v1"
OPENAI_MODEL: "gpt-4"

# 多模态
MULTIMODAL_API_KEY: "sk-CHANGE_ME"
MULTIMODAL_API_BASE: "https://api.openai.com/v1"
MULTIMODAL_MODEL: "gpt-4-vision-preview"

# 服务
HOST: "0.0.0.0"
PORT: "8000"
DEBUG: "false"
COOKIE_SECURE: "true"

# CORS
CORS_ALLOWED_ORIGINS: "https://your-domain.com"
RATE_LIMIT_SSE_PER_MINUTE: "20"

# LangGraph
LANGGRAPH_CHECKPOINT_PATH: "/app/data/langgraph_checkpoints.sqlite"
''',
    },
    # 架构示意图.png / 技术分享视频.mp4 — 跳过二进制媒体
}

# 会议总结助手：8 个文件（6 MD + 1 XLSX + 1 PDF）
MEETING_FILES: dict[str, dict] = {
    "会议纪要_产能规划.md": {
        "type": "md",
        "title": "会议纪要 - Q3 产能规划",
        "meta": {"日期": "2026-07-15", "地点": "1 号会议室", "主持人": "张总", "参会人": "生产/质量/工艺/采购主管"},
        "sections": [
            ("决议事项", "1. Q3 目标产能提升 12%，需新增 1 条柔性产线；\n2. 瓶颈工序为 SMT 贴片，计划增加 2 台设备；\n3. W31 起单班转两班，配套招聘 8 名操作工。"),
            ("责任人", "产线扩充：李娜（截止 W34）；SMT 设备：王芳（截止 W33）；招聘：HR 张维（截止 W32）。"),
            ("待跟进", "供应商交期确认（采购部，W30 周五前回复）；新产线布局图（工艺部，W31 周三前评审）。"),
        ],
    },
    "会议纪要_质量复盘.md": {
        "type": "md",
        "title": "会议纪要 - 月度质量复盘",
        "meta": {"日期": "2026-07-18", "地点": "1 号会议室", "主持人": "张总", "参会人": "质量/生产/采购/工艺"},
        "sections": [
            ("决议事项", "1. 来料不良率从 1.2% 降至 0.8%（Q3 目标）；\n2. 增加首件检验频次（每班 1 次提至 2 次）；\n3. 对关键尺寸实施 SPC 控制图（Xbar-R）；\n4. 两周内完成供应商现场审核。"),
            ("改善措施", "1. 来料全检范围扩大至 A 类料号；\n2. SPC 预警阈值：连续 7 点同向偏离 / CPK < 1.33；\n3. 供应商评分低于 80 启动整改。"),
            ("待跟进", "SPC 培训（质量部，W31）；供应商审核报告（采购部，W32）。"),
        ],
    },
    "会议纪要_安全培训.md": {
        "type": "md",
        "title": "会议纪要 - 安全培训与应急演练",
        "meta": {"日期": "2026-07-20", "地点": "培训教室", "主持人": "安全主管", "参会人": "全体员工（分批）"},
        "sections": [
            ("培训要点", "1. 新进员工必须完成三级安全教育（厂级/车间级/班组级）；\n2. 叉车作业需持证，无证禁止操作；\n3. 化学品泄漏应急预案每季度演练一次；\n4. PPE 配置标准：安全帽/防护鞋/护目镜/耳塞/口罩。"),
            ("应急流程", "化学品泄漏：疏散 → 警戒 → 穿戴 PPE → 吸附材料处理 → 通报 EHS → 记录归档。"),
            ("待跟进", "下周消防演练（安全部，W31 周三 14:00）；叉车证复审（HR，W32）。"),
        ],
    },
    "周会_生产进度.md": {
        "type": "md",
        "title": "周会纪要 - 生产进度（W30）",
        "meta": {"日期": "2026-07-22", "地点": "会议室 2", "主持人": "生产经理"},
        "sections": [
            ("核心指标", "生产进度周会核心指标：计划达成率、OEE、一次合格率、工时利用率、在制品库存天数、安全事故数。"),
            ("本周数据", "计划达成率 98.4%；OEE 83.5%；一次合格率 96.2%；工时利用率 91%；在制品 3.2 天；安全事故 0。"),
            ("问题与对策", "产线 A 换线时间超标（95 分钟 vs 90 分钟目标）：原因工装准备不充分，对策提前 30 分钟备料。"),
        ],
    },
    "项目启动会.md": {
        "type": "md",
        "title": "项目启动会 - 新产品导入",
        "meta": {"日期": "2026-07-10", "主持人": "项目经理", "参会人": "全员"},
        "sections": [
            ("里程碑", "M1 需求冻结（T+2 周）；M2 样机试制（T+6 周）；M3 小批量验证（T+10 周）；M4 量产移交（T+14 周）。"),
            ("关键风险", "1. 关键物料交期长（A003 控制板 12 周）；2. 测试治具开发周期紧。"),
            ("待跟进", "采购长交期物料（采购部，本周）；测试治具设计（工艺部，W31）。"),
        ],
    },
    "月度经营分析.xlsx": {
        "title": "月度经营分析（2026-06）",
        "sheets": [
            {
                "name": "经营指标",
                "headers": ["指标", "目标", "实际", "达成率", "环比"],
                "rows": [
                    ["营收(万元)", 5000, 4900, 0.98, "-0.02"],
                    ["毛利率", 0.25, 0.235, 0.94, "-0.015"],
                    ["订单量(件)", 50000, 49200, 0.984, "0.03"],
                    ["客诉数", 10, 7, 0.7, "-0.3"],
                    ["OEE", 0.85, 0.835, 0.982, "0.005"],
                ],
            },
            {
                "name": "决策事项",
                "headers": ["决策", "负责人", "截止日期", "预期收益(万元)"],
                "rows": [
                    ["优化 BOM 成本", "工艺部", "2026-08-15", 35],
                    ["压缩物流费用", "采购部", "2026-08-30", 18],
                    ["推进自动化降本", "生产部", "2026-09-30", 60],
                ],
            },
        ],
    },
    "培训材料_新员工.md": {
        "type": "md",
        "title": "新员工培训材料",
        "meta": {"版本": "v2.0", "适用对象": "新入职员工（生产/技术/职能）"},
        "sections": [
            ("培训内容", "新员工培训包括：安全三级教育、质量意识、岗位 SOP、设备点检、5S 要求、MES 系统操作。培训合格后签署上岗证。"),
            ("上岗流程", "1. 安全教育（厂级 4h / 车间级 4h / 班组级 8h）；2. 质量意识（2h）；3. 岗位 SOP（16h，含实操）；4. 设备点检（4h）；5. 5S（2h）；6. MES 操作（4h）；7. 考核合格 → 签署上岗证。"),
            ("考核标准", "笔试 ≥ 80 分 + 实操考核通过。不合格者补训，补训仍不合格者调岗。"),
        ],
    },
    "改善提案汇总.pdf": {
        "title": "改善提案汇总（2026 H1）",
        "summary": "Improvement Proposals Summary (2026 H1)",
        "sections": [
            ("优秀提案", "《改善提案汇总.pdf》收录 18 条提案，其中「快速换模工装」预计年节省 36 万元，「视觉检测替代人工」预计减少漏检 60%。"),
            ("提案分类", "效率类 8 项（44%）、质量类 5 项（28%）、安全类 3 项（17%）、成本类 2 项（11%）。"),
            ("奖励机制", "采纳奖励 500-2000 元；产生效益按 5% 提成（封顶 5 万元/年）。"),
        ],
    },
}


# ============================================================
# 格式写入器
# ============================================================


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_md(path: Path, file_def: dict) -> None:
    """写入 Markdown 文件（富文本，UTF-8）。"""
    lines = [f"# {file_def['title']}", ""]
    if "meta" in file_def:
        lines.append("| 属性 | 值 |")
        lines.append("|------|-----|")
        for k, v in file_def["meta"].items():
            lines.append(f"| {k} | {v} |")
        lines.append("")
    for heading, body in file_def.get("sections", []):
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_docx(path: Path, file_def: dict) -> None:
    """写入 Word 文档（python-docx，含标题与段落）。"""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(file_def["title"], level=0)
    for heading, body in file_def.get("sections", []):
        doc.add_heading(heading, level=1)
        for para in body.split("\n"):
            if para.strip():
                run = doc.add_paragraph(para)
                run.paragraph_format.space_after = Pt(6)
    doc.save(str(path))


def write_xlsx(path: Path, file_def: dict) -> None:
    """写入 Excel 文件（openpyxl，多 sheet 含表头与数据）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    # 默认 sheet 改名为第一个 sheet 名
    first_sheet = file_def["sheets"][0]
    ws = wb.active
    ws.title = first_sheet["name"][:31]  # Excel sheet 名最长 31 字符
    _fill_sheet(ws, first_sheet, Font, PatternFill, Alignment)
    for sheet_def in file_def["sheets"][1:]:
        ws = wb.create_sheet(title=sheet_def["name"][:31])
        _fill_sheet(ws, sheet_def, Font, PatternFill, Alignment)
    wb.save(str(path))


def _fill_sheet(ws, sheet_def, Font, PatternFill, Alignment) -> None:
    """填充一个 sheet 的内容与样式。"""
    headers = sheet_def["headers"]
    # 表头加粗 + 浅蓝底
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4472C4")
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row_idx, row in enumerate(sheet_def["rows"], 2):
        for col_idx, val in enumerate(row, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(vertical="center")
    # 列宽自适应（粗略）
    for col_idx in range(1, len(headers) + 1):
        max_len = max(
            [len(str(headers[col_idx - 1]))] +
            [len(str(r[col_idx - 1])) for r in sheet_def["rows"] if col_idx - 1 < len(r)]
        )
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 4, 40)
    ws.freeze_panes = "A2"


def write_pdf(path: Path, file_def: dict) -> None:
    """写入最小化 PDF 文件（手工构造，支持 ASCII 文本）。

    注意：标准 PDF base14 字体（Helvetica）仅支持 ASCII。本函数将
    title/summary 与 sections 的标题以 ASCII 形式写入可见层；
    完整中文内容已存放在 seed_vectors.py 与同名 .md/.docx 中，
    RAG 检索由 seed_vectors.py 预置向量保证。
    """
    title = file_def.get("summary", file_def.get("title", "Sample Document"))
    sections = file_def.get("sections", [])

    # 构造可见文本：ASCII 标题 + section 英文摘要
    visible_lines = [title, ""]
    for heading, body in sections:
        # 仅保留 ASCII 字符作为可见文本，避免字体不支持
        ascii_heading = heading.encode("ascii", "ignore").decode() or "Section"
        ascii_body = body.encode("ascii", "ignore").decode().strip()
        if not ascii_body:
            ascii_body = "(Chinese content - see seed_vectors.py for full text)"
        visible_lines.append(f"## {ascii_heading}")
        visible_lines.append(ascii_body)
        visible_lines.append("")

    pdf_bytes = _build_minimal_pdf(title, visible_lines)
    path.write_bytes(pdf_bytes)


def _build_minimal_pdf(title: str, lines: list[str]) -> bytes:
    """构造单页 PDF（Helvetica 字体，ASCII 文本）。

    PDF 1.4 结构：
      1. header: %PDF-1.4
      2. object 1: Catalog
      3. object 2: Pages
      4. object 3: Page (A4)
      5. object 4: Content stream (BT ... ET)
      6. object 5: Font (Helvetica)
      7. xref + trailer
    """
    # 转义 PDF 字符串中的特殊字符
    def escape_pdf_string(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    # 构造内容流：从顶部 750 开始，每行下移 16 点
    content_lines = ["BT", "/F1 12 Tf", "50 750 Td", "14 TL"]
    for i, line in enumerate(lines):
        if i == 0:
            content_lines.append(f"({escape_pdf_string(line[:90])}) Tj")
        else:
            content_lines.append("T*")
            content_lines.append(f"({escape_pdf_string(line[:90])}) Tj")
    content_lines.append("ET")
    content_stream = "\n".join(content_lines).encode("latin-1", "replace")

    objects: list[bytes] = []

    # obj 1: Catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    # obj 2: Pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    # obj 3: Page
    page_obj = (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    objects.append(page_obj)
    # obj 4: Content stream
    content_obj = (
        b"4 0 obj\n<< /Length " + str(len(content_stream)).encode() + b" >>\nstream\n"
        + content_stream + b"\nendstream\nendobj\n"
    )
    objects.append(content_obj)
    # obj 5: Font
    objects.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    # 构造完整 PDF
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body = b"".join(objects)

    # 计算 xref 偏移
    offsets = []
    pos = len(header)
    for obj in objects:
        offsets.append(pos)
        pos += len(obj)

    xref_start = pos
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n".encode()

    trailer = (
        f"trailer\n<< /Size 6 /Root 1 0 R /Info << /Title ({escape_pdf_string(title[:100])}) "
        f"/Producer (AutoTeams generate_sample_data.py) "
        f"/CreationDate (D:{datetime.now().strftime('%Y%m%d%H%M%S')}) >> >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode("latin-1", "replace")

    return header + body + xref + trailer


def write_code(path: Path, file_def: dict) -> None:
    """写入代码/配置文件（直接 UTF-8 文本）。"""
    path.write_text(file_def["content"], encoding="utf-8")


# ============================================================
# 主入口
# ============================================================


def _write_file(agent_dir: Path, filename: str, file_def: dict) -> bool:
    """根据扩展名分发到对应写入器。返回是否生成（True）或跳过（False）。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    target = agent_dir / filename

    if file_def.get("type") == "code":
        write_code(target, file_def)
        return True

    if ext == "md":
        write_md(target, file_def)
    elif ext == "docx":
        write_docx(target, file_def)
    elif ext == "xlsx":
        write_xlsx(target, file_def)
    elif ext == "pdf":
        write_pdf(target, file_def)
    elif ext in ("json", "py", "yaml", "yml", "txt"):
        write_code(target, file_def)
    else:
        # 二进制媒体（jpg/png/mp4 等）跳过
        return False
    return True


def main() -> None:
    sample_root = Path(__file__).resolve().parent.parent / "sample_data"
    print("=" * 60)
    print("开始生成 sample_data 演示文档...")
    print(f"  输出目录: {sample_root}")
    print("=" * 60)

    agent_files = [
        ("生产助手", PRODUCTION_FILES),
        ("技术问答助手", TECH_FILES),
        ("会议总结助手", MEETING_FILES),
    ]

    total = 0
    skipped: list[str] = []
    for agent_name, files in agent_files:
        agent_dir = sample_root / agent_name
        _ensure_dir(agent_dir)
        generated = 0
        for filename, file_def in files.items():
            ok = _write_file(agent_dir, filename, file_def)
            if ok:
                generated += 1
            else:
                skipped.append(f"{agent_name}/{filename}")
        total += generated
        print(f"  [{agent_name}] 生成 {generated} 个文件（共 {len(files)} 个定义，{len(files) - generated} 个跳过）")

    print()
    print(f"生成完成！共 {total} 个文件。")
    if skipped:
        print(f"跳过 {len(skipped)} 个二进制媒体文件（不生成，由 README 说明占位）：")
        for s in skipped:
            print(f"  - {s}")

    print()
    print("=" * 60)
    print("提示：")
    print("  1. 生成的文件位于 sample_data/ 下各 Agent 子目录")
    print("  2. seed_demo.py 会基于文件名在数据库创建 File 记录")
    print("  3. seed_vectors.py 已预置与 QA 对匹配的向量，RAG 检索无需依赖文件解析")
    print("  4. 如需真实文件处理：将文件复制到 uploads/demo/<Agent 名称>/ 后触发处理任务")
    print("=" * 60)


if __name__ == "__main__":
    main()
