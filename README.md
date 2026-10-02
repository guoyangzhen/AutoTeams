<div align="center">

# 🤖 AutoTeams
### 面向中小企业的 AI 数字员工工作台
**Every SMB Deserves a 7×24 AI Workforce**

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Dual-License](https://img.shields.io/badge/Commercial-Dual--License-orange.svg)](#-商业双许可政策)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18.2%2B-61DAFB?logo=react)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.5%2B-3178C6?logo=typescript)](https://typescriptlang.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker)](https://docker.com)

[核心特性](#-核心特性) • [5大场景](#-5-大中小企业核心业务场景) • [9大工作台](#-9-大核心工作空间) • [极速开箱](#-极速开箱部署) • [外部Agent](#-外部-agent-开放接入) • [环境变量](#-环境变量配置全景表) • [常见问题](#-常见问题与故障排查-faq) • [商业双许可](#-商业双许可政策)

</div>

---

## 💡 产品定位

**AutoTeams** 专为 10~100 人规模的中小企业打造。中小企业往往不需要复杂的学术论文算法 DEMO，也不需要理解深奥的「五级编译器」或「反事实推演」——中小企业真正需要的是：

> **「下载安装 → 选择行业 → 上传公司文档 → 3 位专精 AI 员工立刻 7×24 开工帮你干活。」**

同时，AutoTeams 也是一个**完全开放的 AI 数字员工调度中心**。企业不仅可以开箱即用内置的智能客服、销售助手等专属员工，还可以将本地已在运行的 **OpenAI Codex CLI**、**Claude Code CLI** 与任意遵循 **MCP (Model Context Protocol)** 协议的自研 Agent 统一接入同一个工作台，实现人机协同、任务分派与审计归档。

---

## 🌟 核心特性

- 👥 **真实数字员工体系**：每位数字员工配备规范工号（`ATE-` 统一升级）、专属字牌头像、权责边界规则与专属知识库，具备明确的合规动作放行与越权拦截机制。
- ⚡ **9 大收敛工作空间**：清退冗余菜单，聚焦 SME 最核心的 9 大工作流闭环（总览驾驶舱、花名册、对话工作台、知识库、工作流规则、外部 Agent 接入、数据看板、系统设置、极速入职向导）。
- 🧠 **企业级多模型路由 + BYOK (自备 Key)**：
  - 默认以 **DeepSeek-V4.1-Flash** 为日常主力模型（极高性价比与毫秒级响应）；
  - 备选支持 **Kimi (Moonshot)** 超长上下文研读与 **GLM-4** 复杂推理；
  - 支持企业用户在设置页即时配置私有 API Key（BYOK），密钥加密落库，运行时即配即生效，无需重启进程。
- 🛡️ **工业级安全底座**：
  - **Zero eval() RCE**：规程引擎采用严格的 AST 抽象语法树白名单解析器，彻底禁止任何函数调用与属性反射，杜绝代码执行逃逸；
  - **Safe Subprocess**：受管子进程严禁 Shell 字符串拼接通道，执行环境预先剥离数据库连接串与 API Key 等敏感环境变量；
  - **动态一次性配对**：端侧执行器（Runner）废除 30 天静态 Token，改为 10 分钟一次性配对码与排他并发锁，彻底解决凭证盗用与连接抢占；
  - **存储型 XSS 防御**：附件上传彻底移出未净化 SVG，Mermaid 流程图显式运行于 strict 隔离沙箱。

---

## 🏢 5 大中小企业核心业务场景

系统开箱内置 5 套面向中小企业日常痛点的专精数字员工模板与工作流：

| 核心场景 | 专属数字员工定位 | 核心业务能力 | 解决的企业痛点 |
|---------|----------------|------------|---------------|
| **1. 智能客服** | 7×24 线上客服接待员 | 毫秒级知识库匹配回答常见客诉；亲切自然，复杂高危退款诉求自动推送主管二次确认 | 响应不及时导致客源流失、夜间周末无人值守、新人客服培训周期长 |
| **2. 销售助手** | 销售跟进与线索管家 | 快速解析客户需求与询盘邮件；基于价格表秒级生成规范报价单；自动记录跟进日志与下次催办提醒 | 报价不及时被竞对抢单、销售日志记录繁琐、客户画像分散 |
| **3. 知识问答** | 企业数字专家 / 知识管家 | 企业规章制度、产品手册、SOP 规程深度检索；支持 Word/PDF/Excel/Markdown 原文溯源与页码高亮；自动汇总知识缺口 | 内部文档零散难找、老员工反复回答基础问题、制度更新后信息不同步 |
| **4. 繁琐运营** | 运营专员 / 流程自动化助理 | 批量 Excel/CSV 报表格式清洗、对齐与核对；报销单初审；跨流程任务定时提醒与催办通知 | 机械录入耗费精力、人工核算容易出错漏项、流程运转卡顿 |
| **5. 数据分析** | 经营数据参谋 / 报表分析师 | 自然语言查询经营指标（“上周各渠道转化率”）；自动生成每日/每周经营简报与指标异动预警 | 老板看不懂复杂 BI、每周熬夜赶做汇总 PPT、对业务异动反应迟钝 |

---

## 🖥️ 9 大核心工作空间

```
AutoTeams Workspace Suite:
├── 1. 首页 / 极速入职向导 (OnboardingWizard)
│   ├── 0 员工企业自动唤起 3 步入职向导（选行业 → 传资料 → 部署首批员工）
│   └── 已有员工企业平滑直通总览驾驶舱
├── 2. 总览驾驶舱 (/dashboard)
│   ├── 今日核心指标、14天会话与满意度极细趋势图
│   └── 待办审批门（HITL 人机协同，支持现场核准/驳回并秒级更新）
├── 3. 数字员工花名册 (/workforce)
│   ├── 网格视图与紧凑表格视图双模无缝切换
│   ├── 岗位权责档案抽屉（合规动作放行 vs 高危动作实时越权拦截）
│   └── 员工名片「找她聊聊」一键进入对话页
├── 4. 对话工作台 (/chat/:id)
│   ├── WebSocket 双向流式通信、毫秒级响应
│   ├── 顶部多模型即时切换下拉（DeepSeek / Kimi / GLM-4）
│   └── RAG 引用来源全文展开查看与原文高亮溯源
├── 5. 知识库管理 (/knowledge)
│   ├── 企业业务文档安全拖拽上传与解析
│   └── 向量化状态跟踪与增量索引
├── 6. 业务工作流 (/workflows)
│   ├── SOP 规程规则卡片列表与自动化触发器
│   └── 自然语言输入 → AI 逆向编译 SOP 规则 → 状态机单步步进
├── 7. 外部 Agent 接入 (/agents)
│   ├── 本地 OpenAI Codex、Claude Code、MCP 探针状态监听
│   └── 跨智能体子任务委托分派（Team Handoff）与执行回执查看
├── 8. 经营数据看板 (/analytics)
│   ├── 会话量、满意度与替代人工工时折算
│   └── 动态企业数字化 ROI 与成本质量明细漏斗
└── 9. 组织与模型设置 (/settings)
    ├── 企业基本档案与团队成员权限
    └── AI 模型路由网关与企业私有 Key (BYOK) 加密持久化
```

---

## 🚀 极速开箱部署

### 方式一：Docker Compose（推荐生产与本地全套部署）

```bash
# 1. 克隆代码仓库
git clone https://github.com/guoyangzhen/AutoTeams.git
cd AutoTeams

# 2. 准备环境变量文件
cp .env.example .env

# 编辑 .env 文件，填写基础必要项：
#   - JWT_SECRET_KEY：生成命令 python -c "import secrets; print(secrets.token_urlsafe(32))"
#   - DEEPSEEK_API_KEY 或 OPENAI_API_KEY：填入您的 AI 模型服务密钥
#   - POSTGRES_PASSWORD：自定义数据库密码

# 3. 启动全套容器集群（PostgreSQL + Redis + ChromaDB + 后端 API + 前端 UI）
docker compose up -d

# 4. 执行数据库迁移（首次启动必须执行，创建最新数据库表与索引）
docker compose exec backend alembic upgrade head

# 5. （可选）注入全套生产级真实演示数据（含演示企业、8位数字员工、3条SOP与审批门）
docker compose exec backend python -m scripts.seed_production_ready

# 6. 访问应用
# 前端工作台: http://localhost:3000 (或 http://localhost:5173)
# 后端 API 文档: http://localhost:8000/docs
# 系统健康检查: http://localhost:8000/health
```

---

### 方式二：本地源码快速开发调试

#### 1. 后端服务启动 (Python 3.12+)
```bash
cd backend

# 创建并激活虚拟环境
python -m venv .venv
source .venv/bin/activate       # macOS / Linux
# 或 Windows: .venv\Scripts\activate

# 安装生产依赖
pip install -r requirements.txt

# 配置环境变量（本地开发默认采用 SQLite + 纯净内存模式）
cp ../.env.example .env

# 执行数据库迁移
alembic upgrade head

# 启动 FastAPI 后端服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

#### 2. 前端服务启动 (Node.js 18+)
```bash
# 另开一个终端窗口
cd frontend

# 安装前端依赖
npm install

# 启动 Vite 开发服务器
npm run dev
# 访问 http://localhost:5173 即可开始使用
```

---

## 🔑 默认登录账号

如果您运行了 `python -m scripts.seed_production_ready` 演示数据注入脚本，可直接使用官方预置的企业数字化执行长账号登录体验：

- **登录地址**：`http://localhost:5173/login`（或 `http://localhost:3000/login`）
- **登录邮箱**：`demo@autofde.com`
- **登录密码**：`demo123456`
- **权限角色**：企业超级管理员（Commander），可查看与管理所有员工、审批门与工作流数据。

*注：在生产环境中，请通过 `/register` 注册您的自有企业主账号，并及时在设置页修改默认密码。*

---

## ⚙️ 环境变量配置全景表

| 环境变量 | 必填 | 默认值 | 作用与说明 |
|:---|:---:|:---:|:---|
| `JWT_SECRET_KEY` | ✅ | - | 用户登录态与 Cookie 签名密钥，**生产环境必须随机生成** |
| `DEEPSEEK_API_KEY` | ✅ | - | DeepSeek 主力模型 API 密钥（官方默认主力路由） |
| `MOONSHOT_API_KEY` | 可选 | - | Kimi (Moonshot) 密钥，用于 128k 超长文档穿透 |
| `ZHIPU_API_KEY` | 可选 | - | 智谱 GLM-4 密钥，用于复杂业务逻辑与推理 |
| `OPENAI_API_KEY` | 可选 | - | OpenAI 官方或任意第三方中转网关密钥 |
| `USE_SQLITE` | - | `true` | 是否使用 SQLite 本地单文件数据库（生产环境建议设为 `false`） |
| `DATABASE_URL` | ⚠️ | - | PostgreSQL 连接串（`USE_SQLITE=false` 时必填） |
| `REDIS_URL` | - | `redis://127.0.0.1:6379/0` | 缓存与 Celery 任务队列地址（未配置时自动降级为单机内存模式） |
| `CHROMA_HOST` | - | `localhost` | 向量数据库 Chroma 地址（留空则自动启用内嵌持久化模式） |
| `CORS_ALLOWED_ORIGINS` | ⚠️ | `http://localhost:3000,http://localhost:5173` | 允许跨域的前端源白名单（生产环境禁止设为 `*`） |
| `REGISTRATION_ENABLED` | - | `false` | 是否开启公开注册（生产默认强制关闭以保护租户安全，仅允许企业邀请加入） |
| `METRICS_AUTH_TOKEN` | ⚠️ | - | Prometheus `/metrics` 监控端点保护 Token |
| `BRIDGE_INTERNAL_SECRET`| ⚠️ | - | 本地执行器 (Runner) 与云端桥接共享通讯密钥 |

*详细配置参数可参考根目录 [.env.example](.env.example)。*

---

## 🔌 外部 Agent 开放接入

AutoTeams 原生支持接入外部本地 Agent，让您的桌面端成为个人极客与企业开发者的综合调度中心：

1. **自动探测**：桌面端启动时自动探测本地环境变量及默认端口（如 Claude Code CLI、OpenAI Codex CLI、本地 MCP 端口）；
2. **MCP (Model Context Protocol) 兼容**：将外部 Agent 封装的标准 Tools/Resources 映射为数字员工可用能力；
3. **协同委托 (Team Handoff)**：在统一工作台界面下，内建数字员工可向外部 Agent 发起任务委托，并回填执行结果。

外部调用 API 规范与鉴权详见 [docs/AGENT_API.md](docs/AGENT_API.md)。

---

## ❓ 常见问题与故障排查 (FAQ)

### Q1：首次启动容器或本地运行时，前端 API 报 500 错误？
**A**：首次运行必须执行数据库迁移以建立基础表结构。
- Docker 模式：`docker compose exec backend alembic upgrade head`
- 本地模式：`cd backend && alembic upgrade head`

### Q2：对话工作台发消息没有收到回复？
**A**：
1. 检查 `.env` 中的 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 是否有效；
2. 若未配置 API Key，系统会自动进入演示模拟回复状态；
3. 检查网络连接是否正常，或在设置页配置自定义 Base URL。

### Q3：如何将系统部署到公网服务器？
**A**：
1. 配置域名与 Nginx 反向代理，将 `/api` 指向后端 8000 端口，静态页面托管前端 `dist`；
2. 在 `.env` 中设置 `CORS_ALLOWED_ORIGINS=https://你的公网域名`；
3. 详细云端部署步骤请参阅 [docs/DEPLOY.md](docs/DEPLOY.md)。

---

## 🧪 工程测试与验证

本项目坚持 100% 真实工程交付标准，配备完整的自动化回归与端到端黄金旅程验证套件：

```bash
# 1. 运行后端核心回归测试套件 (包含多模型路由、安全沙箱、外部Agent等)
cd backend
python -m pytest tests/test_llm_router.py tests/test_external_agents.py tests/test_safe_eval.py tests/test_safe_harness_sec.py

# 2. 运行真实用户 20 步黄金旅程端到端全景走查
python -m scripts.e2e_user_journey_test

# 3. 运行前端 TypeScript 编译与生产打包
cd ../frontend
npm run build
```

---

## ⚖️ 商业双许可政策

AutoTeams 采用**开源 + 商业双许可模式 (Dual-Licensing Business Model)** 分发：

### 1. 开源许可证（GNU AGPLv3）
本项目源码在 **GNU Affero General Public License v3.0 (AGPL-3.0)** 许可证下完全开源。
- 允许个人学习、学术研究及完全开源项目免费使用；
- **强传染性约束**：根据 AGPL-3.0 条款，任何将本项目用于提供网络服务（SaaS）、云端托管或修改分发的使用者，**必须强制将其全部衍生业务代码同样以 AGPL-3.0 协议向网络用户公开**。

### 2. 商业闭源授权许可证（Commercial License）
若您的企业或机构存在以下任一诉求：
- 将 AutoTeams 部署于商业生产环境或企业内部内网，且**不希望**公开自身的业务代码与系统改动；
- 基于 AutoTeams 开发商业化闭源产品、提供付费 SaaS 云端服务；
- 需要商业技术支持、定制化 SOP 开发、专有企业服务等级协议（SLA）保障；

**您必须在商业上线前获得版权所有者的官方商业授权证书。**

> **商业授权咨询与商务合作**：
> - 著作权人：**郭阳震 (guoyangzhen)**
> - 商务与授权邮箱：**[upgyz@qq.com](mailto:upgyz@qq.com)**
> - 官方代码仓库：[https://github.com/guoyangzhen/AutoTeams](https://github.com/guoyangzhen/AutoTeams)

---

<div align="center">
Made with ❤️ by AutoTeams Team · Copyright © 2026 guoyangzhen
</div>
