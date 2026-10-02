# AutoTeams 生产级部署指南

> **2026-09-29 生产门禁更新**：当前版本尚不具备完整生产放行证据，详见[第二轮审计矩阵](AUDIT_SECOND_REVIEW_2026-09-29.md)与[第三轮复核](AUDIT_THIRD_REVIEW_2026-09-29.md)。API/worker 的 PostgreSQL 连接必须使用配置在 `DATABASE_APP_ROLE` 中的最小权限角色；管理员连接将被拒绝。**设备/渠道/队列引导与匿名安全审计通道已由迁移 `d5e6f7a8b9c0` 补齐**，生产 Compose 也已改为四种数据库身份（见 §3.5），但**尚未经过真实部署与真实角色口令验收**；不要通过打开 DEBUG 绕过。

> **安全状态 Redis**：Compose 新增 `redis-security`，采用 `noeviction`、AOF 和 `appendfsync always`，数据落在独立的 `redis_security_data` 卷；API/worker 的 `TOKEN_BLACKLIST_REDIS_URL` 指向它，普通缓存仍使用 `redis`。外部部署也必须配置独立安全库；策略无法确认或存储故障时生产认证/回调会拒绝服务。不要删除/清空安全卷；迁移旧撤销记录、意外丢失后的全会话失效策略、真实断电恢复及容量仍需验收。本轮只验证配置与代码，不声称运行过 Docker 故障演练。

> 本项目已具备**生产可用**条件（POSTGRES + Redis + ChromaDB + 多服务编排 + 安全加固 + 备份加密）。
> 本指南面向「把整个项目部署到云端服务器，获得一个可完整正常运行的公网访问链接」。

## 一、项目部署架构（先看这个）

AutoTeams 是**全栈多服务应用**，生产环境以 `docker-compose.prod.yml` 一键编排（单台服务器即可跑通）：

```
                         ┌────────────────────────────────────────────┐
  用户浏览器 ──HTTPS──▶  │  frontend (nginx 反代, 端口 80/8080)      │
                         │  ├─ /             → 前端静态资源 (React)    │
                         │  ├─ /api/*        → backend:8000  (FastAPI) │
                         │  ├─ /collab-api/* → collaboration:3001/api │
                         │  ├─ /collab-ws    → collaboration:3001/ws  │
                         │  ├─ /bridge       → collaboration:3001/bridge (Local Runner WS) │
                         │  └─ /local-runner/* → 本地守护进程分发包     │
                         └──────────────┬─────────────────────────────┘
                                        │ (docker 内网)
                    ┌───────────────────┼───────────────────────┐
                    ▼                   ▼                       ▼
              backend(FastAPI)   collaboration(Node/pi.dev)   postgres + redis + chromadb
                                        ▲
                      用户本机 Local Runner ──wss://<域名>/bridge──┘
                                            （主动外连，云端仅可在其授权目录内执行受限文件操作）

```

**本地工具桥接（Local Runner）链路（新增）：**
- 用户本机安装 `autoteams-runner` 后执行网页「本地连接」生成的连接命令，
  以 **`wss://<域名>/bridge`** 主动外连协作服务（`/bridge` 由前端 nginx 反代）。
- 用户安装分发包通过 **`https://<域名>/local-runner/install.ps1`** 一键下载
  （`irm ... | iex`），nginx 在托管时用 `sub_filter` 自动把域名注入安装脚本。
- 云端 AI 员工经「本地会话」只把 `list/read/write/delete` 文件任务下发到本机执行；
  不支持 CLI、shell、命令执行或 Agentic 执行。所有操作同时受用户授权范围（`read`/`read_write`）与路径边界约束。

**关键点：**
- **前端 nginx 统一入口**，所有服务走**同源**（一个域名），天然解决 CORS / Cookie(SameSite) / WebSocket 问题。
- **WebSocket 协作服务**（pi.dev SDK + AgnesAI）是选型硬约束：**不能用 Vercel/Cloudflare 类纯 Serverless**（不支持长连接 WebSocket 与常驻进程）。
- **本地工具桥接需配置共享密钥**：Backend 与 collaboration-service 之间用 `BRIDGE_INTERNAL_SECRET`（`X-Bridge-Secret`）互信鉴权；生产环境（`DEBUG=false`）**必须设置**，否则后端拒绝启动、本地任务与连接状态上报会被拒绝。
- ChromaDB 可在 `CHROMA_HOST=chromadb`（独立容器）或留空（后端嵌入式）两种模式切换。
- 默认单 worker（`--workers 1`），SSE 状态存于单进程；需水平扩展时引入 Redis pubsub。

> 因此最省心、最贴近现有代码的部署方式是：**一台云服务器（或免费永久 VM）+ docker compose**。

---

## 二、免费/低成本云端方案推荐（按本项目实测结论）

> 说明：你在国内（Asia/Shanghai），所以**海外 PaaS（Vercel/Railway/Render）公网访问不稳定**（DNS 污染、海外出口丢包、SSL 握手失败），且 Railway 已不再提供真正免费层。以下按「国内可直连 + 免费 + 支持 WebSocket 全栈」筛选。

| 方案 | 免费额度 | 国内可直连 | 支持 WebSocket 全栈 | 适合本项目理由 | 需要做的事 |
|---|---|---|---|---|---|
| **① 云服务器 / 永久免费VM + docker compose**（推荐） | Oracle Cloud 永久免费层（2 AMD + 4 ARM 核、24GB 内存，免费**永久**）；腾讯云/阿里云轻量（按量年付约 ¥100-300） | ✅ 选大陆节点需备案，选香港/新加坡节点免备案 | ✅ 完全支持 | 与现有 `docker-compose.prod.yml` **零改造**，一个域名全栈跑通，最稳 | 绑卡（Oracle）、SSH 部署、备域名 |
| **② Sealos**（国内云原生 PaaS） | 注册即送免费额度，容器按量 | ✅ 北京/杭州/广州多可用区，快 | ✅ 支持任意 Docker 镜像 + WebSocket | 免运维服务器，一键容器，自动域名+HTTPS | 逐容器部署（前端/后端/库） |
| **③ Zeabur**（亚洲节点 PaaS） | 每月约 5 美元免费额度，无强制绑卡 | ✅ 亚洲节点优化，国内直连 | ✅ 支持 Docker + Node/Python | 内置 MySQL/PG/Redis，一键关联 | 逐服务部署 |
| ~~Vercel + Railway~~ | Vercel 前端免费；Railway 已取消免费层 | ❌ 不稳定 | ❌ 后端不可用 | 仅适合纯前端/海外演示 | 国内访问需代理，不推荐作为正式裸链接 |

### 结论（按你的需求排序）

1. **要「最省心、一个公网链接、长期可用」→ 选方案①**：一台 **Oracle 永久免费 ARM VM**（或腾讯云/阿里云香港轻量），装 Docker 后 `docker compose up -d` 即可，`docker-compose.prod.yml` 已内置所有服务与安全加固。
2. **要「零运维、国内访问快、免服务器」→ 选方案② Sealos**：国内可用区 + 免费额度 + 自动 HTTPS 二级域名，把前端、后端、Postgres、Redis、ChromaDB 逐个容器起来即可。
3. **要「平衡」→ 选方案③ Zeabur**：亚洲节点、免费额度、内置数据库，导入 GitHub 仓库自动构建。

> 海外（Vercel+Railway）方案保留在文末作为「国际演示」备选项，**正式裸链接不推荐**。

---

## 三、方案①：云服务器 + docker compose（推荐，最稳）

### 3.1 选择一台免费/低成本的服务器

**推荐 A：Oracle Cloud 永久免费层（free forever）**
- 注册需绑信用卡（不会扣费），选择 ARM 实例（Ampere A1，最高 4 核 24GB 免费）。
- 选 **首尔/新加坡/东京** 等亚太区域，国内访问相对快，且**免 ICP 备案**。
- 参考：`https://www.oracle.com/cloud/free/`

**推荐 B：腾讯云 / 阿里云 轻量应用服务器（省心）**
- 开发者/学生优惠时 2C2G 约 ¥100-300/年；大陆节点需备案域名，香港节点免备案、国内直连快。

### 3.2 环境准备（一次性的）

```bash
# 1. 安装 Docker + Compose 插件（以 Ubuntu 为例）
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker

# 2. 拉取代码
git clone https://github.com/<你的用户名>/autoteams.git && cd autoteams

# 3. 准备生产环境变量
cp .env.prod.example .env.prod
```

### 3.3 配置 `.env.prod`（必改项）

| 变量 | 说明 | 生成方式 |
|---|---|---|
| `POSTGRES_PASSWORD` | 强密码（≥32 字符） | `openssl rand -hex 32` |
| `REDIS_PASSWORD` | 强密码（≥32 字符） | `openssl rand -hex 32` |
| `JWT_SECRET_KEY` | 随机密钥（生产不设会拒绝启动） | `python3 -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `AUDIT_SIGNING_KEY` | 审计链签名（与 JWT 不同） | 同上 |
| `CHROMA_AUTH_TOKEN` | ChromaDB 认证 | `openssl rand -hex 32` |
| `METRICS_AUTH_TOKEN` | /metrics 访问令牌 | `openssl rand -hex 16` |
| `BACKUP_ENCRYPTION_KEY` | 备份加密密码 | `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` **务必异地保存** |
| `OPENAI_API_KEY` / `DEFAULT_LLM_PROVIDER` | 至少一组 LLM Key | DeepSeek/豆包/通义/智谱任选（国内推荐 DeepSeek） |
| `AGNES_API_KEY` | 协作服务（pi.dev）用 | 你的 AgnesAI Key |
| `BRIDGE_INTERNAL_SECRET` | Backend 与协作服务共享的内部密钥（本地工具桥接鉴权），两者必须一致 | `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `RUNNER_PUBLIC_BRIDGE_URL` | 你的公网域名根，用于生成用户本机 `wss://<域名>/bridge` 连接命令（**云端部署必填**，留空则本机连不上云端） | 如 `https://autoteams.example.com` |
| `COLLAB_SERVICE_URL` | 后端调用协作服务的内网地址（compose 内已固定为容器名，一般无需改） | 保持默认 `http://127.0.0.1:3001`（compose 会覆盖） |
| `CORS_ALLOWED_ORIGINS` | 填你的公网域名 | 如 `https://autoteams.example.com` |
| `FRONTEND_URL` | 前端公网地址（生成邀请链接用） | 同上 |
| `BACKEND_IMAGE` / `FRONTEND_IMAGE` / `COLLAB_IMAGE` | 预先构建镜像地址（CI 已推送三者） | 见下方「3.4 镜像来源」 |

> 其余变量（速率限制、上传大小、连接池、Sentry等）保留默认值即可。

### 3.4 镜像来源（二选一）

默认 `docker-compose.prod.yml` 从 GHCR 拉预构建镜像（前端/后端/协作服务三者均由 CI 推送）。若未配置 CI，可改为**本地构建**。

**方式 A（推荐，纯本地构建，无需 CI）：** 编辑 `docker-compose.prod.yml`，把 `frontend`/`backend`/`collaboration-service` 的 `image:` 改为 `build:`：

```yaml
  frontend:
    build: ./frontend                    # 原 image: ghcr.io/... 改为本地构建
  backend:
    build: ./backend                     # 原 image: ghcr.io/... 改为本地构建
  collaboration-service:
    build: ./collaboration-service       # 原 image: ghcr.io/... 改为本地构建
```

**方式 B（用 CI 推送 GHCR）：** 在 GitHub → Settings → Secrets 配置 `GH_PAT`、`GHCR_OWNER`，推送代码触发 `.github/workflows/deploy.yml` 构建并推送 `*-frontend`、`*-backend`、`*-collab` 三个镜像。

### 3.5 数据库身份与启动顺序（AUD-19）

生产有**四种**数据库身份，对应四种能力，互不混用：

| 身份 | 使用者 | 能力 | 口令变量 |
|---|---|---|---|
| `${POSTGRES_USER}`（默认 autoteams，集群超级用户） | `migrate` 一次性服务、`postgres` 容器、`backup` | 只做 DDL 迁移、**为三个受限角色设置口令**、集群初始化、逻辑备份 | `POSTGRES_PASSWORD` |
| `autoteams_app` | `backend` | 业务读写；**没有**任何跨租户队列能力 | `APP_DB_PASSWORD` |
| `autoteams_worker` | 三个队列 Worker | 与 API 相同的表权限 + 跨租户领取/恢复函数 | `WORKER_DB_PASSWORD` |
| `autoteams_bootstrap` | 仅 `backend` 的渠道回调引导 | 零表权限，只读指定账号的验签材料 | `BOOTSTRAP_DB_PASSWORD` |

启动顺序：`migrate` 用管理员连接执行 `alembic upgrade head`，**成功退出**后
`backend` 与三个 Worker 才被放行（`service_completed_successfully`）。
`backend` 已显式覆盖镜像默认的 `migrate_and_start.sh`，因此**不会**再自己拿管理员
连接迁移。bootstrap 凭据只发给 `backend`，不下发到任何 Worker、前端或协作服务。

```bash
# 两个 --env-file 都需要（Docker Compose v2.17+ 支持多个 --env-file）
docker compose -f docker-compose.prod.yml \
  --env-file .env.prod --env-file .env.prod.db up -d
docker compose -f docker-compose.prod.yml --env-file .env.prod --env-file .env.prod.db ps
docker compose -f docker-compose.prod.yml --env-file .env.prod --env-file .env.prod.db logs -f backend
```

单独重跑迁移（不带起服务）：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod --env-file .env.prod.db \
  run --rm migrate
```

> 这些是**配置层**的约束。角色的真实权限行为由 PostgreSQL 实库用例验证
> （`backend/tests/test_rls_bootstrap_postgres.py` 等）；本节尚未经过真实生产部署验收。

> **迁移服务是必要且最小化的例外**：它除管理员连接外，还拿到三个受限角色口令 ——
> 迁移只创建 LOGIN 角色、**不设置口令**，因此 `migrate` 在升级成功后用管理员连接
> 显式写入这三个口令（参数化语句，输出中不出现口令）。除此之外它不持有任何
> 应用层密钥。

> **口令必须是 URL 安全字符**（字母、数字、`-` `_` `.` `~`，长度 32–128）。这些口令
> 会被拼进 `postgresql+asyncpg://角色:口令@主机/库`；含 `@` `:` `/` `#` `?` 会让连接串
> 被静默解析成错误的主机或认证失败。因此 `migrate` 在**连接数据库之前**就做校验，
> 不合规直接以非零退出——不会带着坏连接串把应用服务拉起来。请用
> `openssl rand -hex 32` 或 `python -c "import secrets;print(secrets.token_urlsafe(32))"`
> 生成。四个口令必须互不相同。
>
> **migrate 的四条硬规则**（都由 `scripts/migrate_cli` 在**连接数据库之前**检查，
> 违反即非零退出，不会带着问题把应用服务拉起来）：
>
> 1. **URL 安全**：只接受字母、数字与 `-` `_` `.` `~`，长度 32–128；不截断、不转义。
> 2. **不是占位值**：仍是 `CHANGE_ME_*`（直接复制示例文件）即拒绝，避免生产用上
>    公开已知的口令。
> 3. **四个身份互不相同**：admin / app / worker / bootstrap 共用口令会把数据库里
>    分开的权限重新合并（worker 与 app 共用等于让 API 拿到跨租户队列能力）。
> 4. **口令轮换是原子的**：三个受限角色在**同一个事务**里设置口令，任一失败整体
>    回滚，不会出现"app 已换新口令、worker 还是旧口令"的半轮换状态。
>
> 异常输出会先遮蔽这四个口令再打印（驱动异常可能带上执行的 SQL 与绑定参数）。

### 3.6 绑域名 + HTTPS（可选）

- 大陆节点：在云厂商完成 ICP 备案后，把域名 A 记录指向服务器公网 IP。
- 香港/海外节点：免备案，直接解析。
- HTTPS：用 Caddy / Nginx / `certbot` 一键签发（Let's Encrypt）。若用 IP 直连，可先跳过 HTTPS，但生产环境 `COOKIE_SECURE=true` 要求 HTTPS，可用自签或临时设为 `COOKIE_SECURE=false` 做功能验证。

### 3.7 初始化演示数据（可选）

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod --env-file .env.prod.db \
  exec backend python -m scripts.seed_demo
```

### 3.8 本地工具桥接（Local Runner）部署说明

> 该能力让「云端 AI 在本机安全地读写已授权目录中的文件」。该能力**不提供终端命令或 Agentic CLI 执行**。以下配置已在

> `frontend/nginx.conf` 与 `docker-compose.prod.yml` 内置，**无需额外改 nginx/compose**，只需正确填环境变量即可。

**需要满足的 4 个前提（缺一不可）：**

1. **`BRIDGE_INTERNAL_SECRET`**：在 `.env.prod` 中设置，Backend 与 collaboration-service **必须完全一致**。
   生成：`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`。
2. **`RUNNER_PUBLIC_BRIDGE_URL`**：填你的公网域名根（如 `https://autoteams.example.com`），
   用于生成用户本机连接命令里的 `wss://<域名>/bridge`。
3. **协作服务运行**：`collaboration-service` 容器健康、`AGNES_API_KEY` 已配置，
   nginx `/bridge` 反代已生效（`frontend/nginx.conf` 已内置）。
4. **分发包已发布**：前端镜像构建时会把 `frontend/public/local-runner/`（`install.ps1` +
   `local-runner.zip`）打进 `dist`，经 nginx `/local-runner/` 托管。

**用户侧完整流程（部署后自动可用，无需人工干预）：**

```powershell
# ① 本机一键安装（域名由 nginx sub_filter 自动注入，无需设置任何环境变量）
irm https://<域名>/local-runner/install.ps1 | iex

# ② 网页「协作工作台 → 本地连接」中添加本地文件夹路径并选授权范围，
#    点「生成连接命令」，复制后在本机 PowerShell 运行：
autoteams-runner connect --server wss://<域名>/bridge --token <令牌> --grant <授权ID> --path "D:\项目" --scope read_write
```

**打包分发包（仅在要更新本地守护进程源码时执行）：**

```powershell
.\scripts\package-local-runner.ps1   # 重新生成 frontend/public/local-runner/{install.ps1, local-runner.zip}
```

**验证：**

```powershell
# 服务器上确认 /bridge 反代与分发包可达
curl -I https://<域名>/local-runner/install.ps1
# 浏览器打开「本地连接」，授权后本机运行连接命令，卡片应显示「已连接」，
# 并出现该机器就绪的工具徽章（list/read/write/delete）。

```

### 3.9 一键部署（推荐，最快上手）

> 脚本 `deploy/deploy.sh` 把方案①的全过程（环境检查、装 Docker、拉代码、生成 `.env.prod`、
> 启动服务、配置宿主机 nginx HTTPS 反代、健康检查）封装成一条命令。你只需准备：
> **一台服务器 + 一个已解析的域名 + DeepSeek/AgnesAI 的 API Key**，其余（强密码等）脚本自动生成。

```bash
# 在服务器上执行（Ubuntu/Debian 推荐）
bash <(curl -fsSL https://raw.githubusercontent.com/guoyangzhen/AutoTeams/main/deploy/deploy.sh)
```

或手动下载到服务器后执行：

```bash
git clone https://github.com/guoyangzhen/AutoTeams.git && cd AutoTeams
bash deploy/deploy.sh
```

**脚本交互时会依次询问（其余全部自动）：**
1. 你的公网域名（如 `autoteams.example.com`）→ 同时写入 `CORS_ALLOWED_ORIGINS` / `FRONTEND_URL` / `RUNNER_PUBLIC_BRIDGE_URL` / `AUTOTEAMS_RUNNER_DOWNLOAD_BASE`。
2. GitHub 用户名（拉取预构建镜像，默认 `guoyangzhen`）→ 写入三个 `*_IMAGE` 地址。
3. DeepSeek API Key（`sk-...`）→ 写入 `OPENAI_API_KEY`。
4. AgnesAI API Key（`sk-...`）→ 写入 `AGNES_API_KEY`（协作服务 pi.dev 用）。
5. 是否安装并配置宿主机 nginx HTTPS 反代（默认是）。

**脚本自动完成：**
- 检测 Docker / Compose，缺失则自动安装。
- 从 `.env.prod.example` 生成 `.env.prod`，自动生成并填入 `POSTGRES_PASSWORD` / `REDIS_PASSWORD` / `JWT_SECRET_KEY` / `AUDIT_SIGNING_KEY` / `CHROMA_AUTH_TOKEN` / `METRICS_AUTH_TOKEN` / `BACKUP_ENCRYPTION_KEY` / `BRIDGE_INTERNAL_SECRET` 等全部敏感值。
- `docker compose -f docker-compose.prod.yml pull && up -d` 启动全部服务。
- 生成宿主机 nginx 反代配置并（可选）用 certbot 申请 HTTPS 证书。
- 健康检查并打印访问地址。

> 脚本所有生成项都可在之后手动编辑 `.env.prod` 覆盖，且自动保留了旧配置备份（`.env.prod.bak.*`），可安全回滚。

### 3.10 nginx 反代详解

AutoTeams 的 nginx 反代分**两层**，理解后即可按需增删：

**① 前端容器内 nginx（`frontend/nginx.conf`，已配置好，无需改动）**
前端镜像内自带的 nginx 负责把各个路径转发到后端与协作服务，**一个域名同源**解决了 CORS / Cookie / WebSocket 问题：

| 路径 | 转发到 | 用途 |
|---|---|---|
| `/` | 前端静态资源 | React 页面 |
| `/api/*` | `backend:8000` | 后端 FastAPI |
| `/api/v1/agents/*/chat/stream` | `backend:8000`（SSE 专用，buffering off） | 流式对话 |
| `/collab-api/*` | `collaboration-service:3001/api/`（去前缀） | 协作服务 HTTP |
| `/collab-ws` | `collaboration-service:3001/ws`（WebSocket） | 协作对话 WS |
| `/bridge` | `collaboration-service:3001/bridge`（WebSocket） | 本地 Runner 外连 |
| `/local-runner/*` | 静态分发包（`install.ps1` + `local-runner.zip`） | 本机下载安装 |

该容器 `listen 8080`，在 `docker-compose.prod.yml` 中映射为宿主机 `${FRONTEND_PORT:-80}:8080`。

**② 宿主机 nginx（可选，用于 HTTPS + 域名）**
如果不想直接用 `http://IP:80`，而是绑定域名并启用 HTTPS，在宿主机装一层 nginx 反代到前端容器即可。`deploy/deploy.sh` 会自动生成如下配置（也可手动创建）：

```nginx
# /etc/nginx/conf.d/autoteams.conf
# 前端容器内的 nginx 已负责所有内部转发，这层只做 443 → 前端容器映射端口(默认80)
server {
    listen 80;
    server_name autoteams.example.com;
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2;
    server_name autoteams.example.com;
    ssl_certificate     /etc/letsencrypt/live/autoteams.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/autoteams.example.com/privkey.pem;
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket / SSE 必须
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 360s;
    }
}
```

> 说明：`proxy_pass http://127.0.0.1:80` 指向「前端容器映射的宿主机端口」。若你把 `FRONTEND_PORT` 改成了别的值（如 8080），这里要改成对应的端口。
> 申请证书：`certbot --nginx -d autoteams.example.com`（脚本已内置）。

---

## 四、方案②：Sealos（国内 PaaS，免服务器）

Sealos 是国内云原生平台，支持任意 Docker 镜像，自动分配域名 + HTTPS，注册送免费额度，国内（北京/杭州/广州）访问快。参考：<https://cloud.sealos.run/>

> 本项目是「前端 nginx + 后端 + 3 个中间件 + 协作服务」多容器，Sealos 需**逐容器**部署（或用其「应用商店」先起 Postgres/Redis/ChromaDB）。若想一步到位且不想逐容器拆 `docker-compose.prod.yml`，**方案①更省事**。

**步骤概览：**
1. 注册登录 → 工作台 →「应用管理」。
2. 先用「应用商店」或「数据库」创建 **PostgreSQL / Redis**（自动生成连接串）。
3. 后端：创建应用，镜像指向 `ghcr.io/<owner>/autoteams-backend:latest`（或本地 push 的镜像），端口 8000，填 `.env.prod` 中后端变量（`DATABASE_URL`/`REDIS_URL` 用第 2 步连接串，`CHROMA_HOST` 留空走嵌入式）。
4. 协作服务：创建应用，镜像 `ghcr.io/<owner>/autoteams-collab:latest`，端口 3001，填 `AGNES_API_KEY`、`BRIDGE_INTERNAL_SECRET`（与后端一致）等。
5. 前端：构建 `frontend` 得到静态产物，用「对象存储/静态托管」或 nginx 镜像托管，并把 `/api`、`/collab-api`、`/collab-ws`、`/bridge` 反代到后端/协作服务，并把 `local-runner/`（`install.ps1` + `local-runner.zip`）一起托管（可参考 `frontend/nginx.conf`）。若启用本地工具桥接，需保证 `/bridge` 支持 WebSocket 反向代理。
6. 绑定域名或使用自动分配的二级域名，开启 HTTPS。

---

## 五、方案③：Zeabur（亚洲节点 PaaS）

Zeabur 对国内直连友好，每月约 5 美元免费额度，内置 MySQL/PostgreSQL/Redis，支持 Docker、Node、Python，自动签发域名与 HTTPS。参考：<https://zeabur.com/>

**步骤概览：**
1. 用 GitHub 登录，关联 AutoTeams 仓库。
2. 新建项目，添加服务：
   - **PostgreSQL / Redis**：用平台内置数据库一键创建，自动注入连接串。
   - **backend**：选择 `backend` 目录，平台识别 FastAPI/Docker，设置 `DATABASE_URL`/`REDIS_URL`/`JWT_SECRET_KEY`/`OPENAI_API_KEY` 等。
   - **collaboration**：选择 `collaboration-service` 目录（Node + Docker），设置 `AGNES_API_KEY`、`BRIDGE_INTERNAL_SECRET`（与后端一致）。
   - **frontend**：选择 `frontend` 目录静态托管，把 `/api` 反代到 backend、`/collab*` 与 `/bridge` 反代到 collaboration，并托管 `local-runner/` 分发包（可参考 `frontend/nginx.conf`）。
3. 平台分配 `xxx.zeabur.app` 域名，自动 HTTPS；可绑定自定义域名。

---

## 六、方案④（遗留海外）：Vercel + Railway

> 仅建议用于**海外演示/国际评审**。国内直连不稳定，且 Railway 已无真正免费层，**正式公网链接不推荐此方案**。

### 步骤 1：后端 → Railway
1. Railway 新建项目，添加 **PostgreSQL** 插件（自动生成 `DATABASE_URL`，把 `postgres://` 改成 `postgresql+asyncpg://`）。
2. 部署 backend：New → GitHub Repo → 选择 AutoTeams，Root Directory `backend`（自动识别 `Dockerfile`/`railway.json`）。
3. 环境变量：`USE_SQLITE=false`、`CHROMA_HOST=`（嵌入式）、`JWT_SECRET_KEY`、`OPENAI_API_KEY`/`DEFAULT_LLM_PROVIDER`、`AGNES_API_KEY` 等（见 `.env.prod.example`）。
4. 部署后得到 `https://<name>.up.railway.app`，验证 `/health` 与 `/docs`。

### 步骤 2：协作服务 → Railway（WebSocket）
另起一个 Railway 服务，Root Directory `collaboration-service`，设置 `PORT=3001`、`AUTOTEAMS_BACKEND_URL=<railway后端>`、`AGNES_API_KEY`。注意 Railway 需开启 WebSocket 支持。

### 步骤 3：前端 → Vercel
1. 导入 GitHub 仓库，Framework Preset **Vite**，Root Directory `./`（`vercel.mjs` 已配置）。
2. 环境变量 `BACKEND_URL=https://<railway后端域名>`（只填 origin，不带路径、查询参数或凭据）。
   `vercel.mjs` 在配置加载时生成实际 `/api/*` 转发地址；不再依赖静态JSON展开环境变量。
   本地用 `node --test scripts/test_vercel_config.mjs` 验证配置。程序化配置依据
   [Vercel 官方说明](https://vercel.com/docs/project-configuration/vercel-ts)，需使用支持该格式的当前平台/CLI。
   本轮验证到实际配置加载，尚未执行远端部署或浏览器网络验收。
3. 部署后获得 `https://<name>.vercel.app`。

> 因前端与后端/协作服务分域，需保证 Cookie `SameSite=None; Secure` 且 CORS 配置正确；WebSocket 建议走 Vercel 的 rewrites 反代到 Railway。

---

## 七、生产环境变量总表（`.env.prod`）

| 分组 | 变量 | 必填 | 说明 |
|---|---|---|---|
| 数据库 | `POSTGRES_USER` `POSTGRES_PASSWORD` `POSTGRES_DB` | ✅ | Compose 自动拼 `DATABASE_URL` |
| | `USE_SQLITE=false` | ✅ | 强制 PostgreSQL |
| 缓存 | `REDIS_PASSWORD` | ✅ | 用于 token 黑名单/共享限流；单实例可留空回退内存 |
| 向量库 | `CHROMA_HOST` | — | `chromadb`：独立容器；`空`：后端嵌入式 |
| | `CHROMA_AUTH_TOKEN` | 建议 | 认证 token |
| 安全 | `JWT_SECRET_KEY` | ✅ | 生产不设拒绝启动 |
| | `AUDIT_SIGNING_KEY` | ✅ | 审计链签名，与 JWT 不同 |
| | `METRICS_AUTH_TOKEN` | 建议 | /metrics 保护 |
| | `CORS_ALLOWED_ORIGINS` | ✅ | 你的公网域名 |
| | `COOKIE_SECURE=true` `COOKIE_SAMESITE=None` | ✅ | HTTPS 下必须 |
| LLM | `DEFAULT_LLM_PROVIDER` + `OPENAI_API_KEY`/`OPENAI_API_BASE`/模型 | ✅ 至少一组 | 国内推荐 DeepSeek |
| | `AGNES_API_KEY` | ✅ | 协作服务（pi.dev）用 |
| | `EMBEDDING_MODEL` | — | 中文推荐 `BAAI/bge-large-zh-v1.5` |
| 本地工具桥接 | `BRIDGE_INTERNAL_SECRET` | ✅ | Backend 与协作服务共享密钥，两者必须一致；生产不设拒绝启动 |
| | `RUNNER_PUBLIC_BRIDGE_URL` | 云端必填 | 公网域名根，用于生成用户本机 `wss://<域名>/bridge` 连接命令；留空则回退协作服务内网地址（仅本地直连可用，云端下用户本机连不上） |
| | `COLLAB_SERVICE_URL` | — | 后端调用协作服务的内网地址，compose 内固定为容器名，一般无需改 |
| 前端 | `VITE_API_BASE_URL=/api/v1` | — | 默认即可，nginx 反代 |
| | `FRONTEND_URL` | 建议 | 生成邀请链接 |
| 监控 | `SENTRY_DSN` `VITE_SENTRY_DSN` | 建议 | 错误上报 |
| 备份 | `BACKUP_ENCRYPTION_KEY` | ✅ | 备份加密，务必异地保存 |

---

## 八、上线后验证清单

- [ ] `https://<域名>/health` 返回 `status: healthy`，且 `database up`、`chromadb up`。
- [ ] `/docs`（Swagger）可访问。
- [ ] 用 `demo@autoteams.example` + 你在部署时设置的 `DEMO_PASSWORD` 登录（需先跑 `seed_demo`，未设置该环境变量时脚本会拒绝创建弱口令账号）。
- [ ] 建一个 Agent、传一份文档、发起一次对话（验证 SSE / LLM）。
- [ ] 打开「协作工作台」发起协作（验证 pi.dev 协作服务 + WebSocket）。
- [ ] 触发一次编译/运行时构建（验证 Postgres + ChromaDB 读写）。
- [ ] 登出后立即用旧 token 访问应被拒绝（验证 Redis 黑名单，若配置）。
- [ ] `curl -I https://<域名>/local-runner/install.ps1` 返回 200（分发包可达）。
- [ ] 本机运行 `irm https://<域名>/local-runner/install.ps1 | iex` 成功安装 `autoteams-runner`。
- [ ] 网页「本地连接」添加授权路径 → 本机运行连接命令 → 卡片显示「已连接」并出现工具徽章（验证 `/bridge` 反代 + `BRIDGE_INTERNAL_SECRET`）。
- [ ] 在本地会话中让 AI 执行一次 `list` / `read`（验证云端→本机任务下发执行）。

---

## 九、备份与恢复

```bash
# 备份（加密 + 校验）
./scripts/backup_db.sh          # 输出 autoteams_日期.tar.gz.gpg + .sha256

# 恢复（自动校验 + 解密）
./scripts/restore_db.sh backups/autoteams_日期.tar.gz.gpg
```

在容器内执行：`docker compose ... exec backend bash scripts/backup_db.sh`。

> `BACKUP_ENCRYPTION_KEY` 丢失将无法恢复备份，务必异地安全保存。

---

## 十、常见问题

| 问题 | 解决方案 |
|---|---|
| 容器启动后 backend 500（多半是表结构未迁移） | 用管理员连接单独跑迁移：`docker compose -f docker-compose.prod.yml --env-file .env.prod --env-file .env.prod.db run --rm migrate`，再 `up -d backend`。**不要**用 `exec backend alembic`——backend 已无管理员连接，迁移已拆到独立 `migrate` 服务 |
| 启动即报 `APP_DB_PASSWORD` / `WORKER_DB_PASSWORD` 等缺失 | `.env.prod.db` 未提供，或启动命令漏了第二个 `--env-file`；compose 用 `:?` 失败关闭，不接受默认口令 |
| 启动报 `PostgreSQL runtime identity does not match` | `.env.prod.db` 中的口令与数据库里对应角色的口令不一致；角色名由迁移创建，固定为 `autoteams_app` / `autoteams_worker` / `autoteams_bootstrap`，需与 `.env.prod.db` 一一对应 |
| 数据库用了 SQLite | 检查 `USE_SQLITE=false` 与 `DATABASE_URL` |
| 对话无回复 | 检查 `OPENAI_API_KEY` / `DEFAULT_LLM_PROVIDER` 是否正确 |
| 协作工作台打不开 | 检查 `AGNES_API_KEY`、`collaboration-service` 是否 healthy、nginx `/collab-api`/`/collab-ws` 是否反代 |
| WebSocket 连不上 | 检查 nginx 是否配置 `Upgrade`/`Connection: upgrade` 头（`nginx.conf` 已内置） |
| 本地守护进程连不上（连接命令报错/卡片一直「待连接」） | 检查 `.env.prod` 的 `BRIDGE_INTERNAL_SECRET` 是否设置且两端一致、`RUNNER_PUBLIC_BRIDGE_URL` 是否为公网域名、nginx `/bridge` 反代是否生效、`collaboration-service` 是否 healthy |
| 本机 `irm ...install.ps1 | iex` 下载失败 | 确认 `https://<域名>/local-runner/install.ps1` 可访问（nginx 需托管 `local-runner/` 分发包） |
| 本地任务报「授权为只读」 | 授权范围选 `read_write` 才能执行 write/delete/cli/agentic |
| 本地任务报「命令不在白名单内」 | `cli` 仅允许白名单命令（默认 ls,cat,echo,grep,find,head,tail,node,npm,pnpm,yarn,git），可用 `AUTOTEAMS_RUNNER_CLI_ALLOW` 扩展 |
| 国内访问慢/打不开 | 选国内可用区（Sealos）或香港/首尔/新加坡节点（VPS）；海外 PaaS 需代理 |
| CORS 报错 | 设 `CORS_ALLOWED_ORIGINS` 为前端域名；同域部署（方案①②③）天然无此问题 |
| Cookie 不生效 | HTTPS 下 `COOKIE_SECURE=true`；跨域需 `COOKIE_SAMESITE=None` |
| 登出后 token 仍能用 | 配置 `REDIS_URL`，否则黑名单仅存单个进程内存 |
| /health 返回 degraded | 检查 `dependencies` 中 redis/chromadb 状态与连接串 |
| 邀请链接不完整 | 配置 `FRONTEND_URL` 为前端公网地址 |
| 中文检索效果差 | 确认 `EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5` 已下载 |
| Oracle 免费 VM 被回收 | 保持登录/资源使用率，避免长期 0 使用；或用轻量服务器兜底 |