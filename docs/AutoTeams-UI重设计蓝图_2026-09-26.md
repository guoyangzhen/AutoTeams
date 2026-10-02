# AutoTeams UI 重设计蓝图 v2（克制 · 编辑式）

> 生成日期：2026-09-26 ｜ 生成方式：Google Stitch MCP（`STITCH_MCP_TOKEN`）
> 依据：`docs/企业级数字员工标杆架构深度对比与AutoTeams演进蓝图.md`
> 资产目录：`autoteams_ui/`（`DESIGN.md` 规范 + 11 张 Stitch 原型与截图 + 评审墙 + 生成脚本）
> 品牌：AutoTeams（智团），数字工号前缀 `ATE-`

## 0. 为什么推翻 v1

v1 采用「深色密集驾驶舱」：满屏描边卡片、彩色胶囊标签 soup、五色等饱和并排、10–11px 中文小字、同款卡片重复 8 次、装饰性虚线框。信息密度高但**无层级、无留白、无焦点**，视觉噪声大于信息价值。

v2 确立的原则：**少即是高级** —— 层级由排版与留白建立，不由边框、底色块和彩色标签建立。

| 维度 | v1 | v2 |
| --- | --- | --- |
| 基底 | 深空蓝黑 `#0B0F17` 全站 | 纸面 `#FAFAF9` 为主，仅驾驶舱与 Trace 用近黑 |
| 容器 | 万物描边圆角卡 | 发丝线分隔，白底无边框；仅大容器 10px 圆角 |
| 颜色 | 5 色等饱和 + 发光 | 中性 6 档；主色 `#1F4FD8` 全屏 ≤3 处，仅用于主行动/选中/关键数字 |
| 状态 | 彩色填充胶囊 | 6px 圆点 + 13px 文字 |
| 字号 | 低至 10–11px | 12px 封底；标题 30 / 区块 17 / 正文 14 / 次级 13 |
| 信息密度 | 铺满视口 | ≤7 区块、≤8 数据行、留白 ≥30% |
| 图表 | 环形进度、雷达、渐变面积 | 1–2px 细线，必要刻度，无填充 |
| 按钮 | 多枚实心主色 | 每屏 ≤1 枚实心主按钮 |

## 1. Stitch 资产

| 项 | 值 |
| --- | --- |
| Project | `projects/6446415064066768933` — AutoTeams v2 · 智团控制台 |
| Design System | `assets/6871ec2a11cb4e36a5df7adda16a96b7`（由 `DESIGN.md` 生成，displayName: AutoTeams） |
| 模型 / 设备 | `GEMINI_3_8_FLASH` / `DESKTOP` |
| 评审墙 | `autoteams_ui/gallery.html`（每屏三视图：**实时原型**（默认，iframe 直跑，矢量清晰）／**高清截图**（`screens-v2/hires/`，3200×2000 @2x）／**完整源码**（内嵌全文，一键复制或下载） |
| 源码文件 | `autoteams_ui/screens-v2/<slug>.html`（18–27 KB 单文件：Tailwind CDN + 内联 SVG 图标，无构建依赖，双击即运行） |

| # | 页面 | 基调 | 文件 |
| --- | --- | --- | --- |
| 01 | 总览驾驶舱 | 近黑 | `screens-v2/01-boss-cockpit.html` |
| 02 | 数字员工花名册 | 浅 | `screens-v2/02-employee-roster.html` |
| 03 | 数字员工岗位档案 | 浅 | `screens-v2/03-employee-profile.html` |
| 04 | 企业资产编译工坊 | 浅 | `screens-v2/04-compiler-studio.html` |
| 05 | 规程卡编排器与仿真 | 浅 | `screens-v2/05-flow-editor.html` |
| 06 | 团队协同看板 | 浅 | `screens-v2/06-team-board.html` |
| 07 | 岗位竞标与验收 | 浅 | `screens-v2/07-bidding-arena.html` |
| 08 | 全渠道接入中枢 | 浅 | `screens-v2/08-connect-gateway.html` |
| 09 | 工具生态（MCP + Local Runner） | 浅 | `screens-v2/09-tool-ecosystem.html` |
| 10 | 全链路 Trace 观测 | 近黑 | `screens-v2/10-trace-observability.html` |
| 11 | 组织进化中枢 | 浅 | `screens-v2/11-evolution-hub.html` |

## 2. 外壳与组件

- 顶栏 64px：AutoTeams 字标 · 企业名 · 全局搜索 · 1 枚通道状态点 · 通知 · 头像。
- 侧栏 240px：指挥 / 员工 / 生产 / 流程 / 资产 / 接入 / 治理；选中态 = 2px 主色左标 + 墨色文字，无底色块。
- 内容区：左右留白 40px，列宽上限 1280px 居中；页面标题 30px + 一行 13px 副标题，右侧最多一个主按钮。
- 状态 = 6px 圆点 + 13px 文字（`在岗 / 实习中 / 已停用 / 在线 / 阻断`）。
- 员工条目 = 一行列表（32px 字母牌 + 姓名 + 工号 mono + 岗位 + 部门 + 60px 绩效细条 + 状态点），**不用卡片包裹**。
- 节点图 = 白底 1px 发丝线节点 + 1px 实线连线 + 12px 条件标签，选中节点主色描边。
- 裁决确认 = 居中浮层：动作 / 影响 / 回滚三行事实 + 取消 / 确认。
- 表格仅在数据真正表格化时使用：行高 48px、仅底部发丝线、无斑马纹。

## 3. 页面蓝图与数据依赖

| 页面 | 回答的问题 | 主导区 | 依赖数据 |
| --- | --- | --- | --- |
| 01 驾驶舱 | 今天谁在跑、卡在哪 | 14 天闭环趋势单线 | `workforce_profiles`、`matrix_tasks`、审批队列 |
| 02 花名册 | 我有哪些编制 | 7 行员工名录 | `WorkforceProfile`、`performance_score` |
| 03 岗位档案 | TA 能做什么、禁止什么 | 岗位边界两栏 | `duty_boundaries`、`authorized_flows/knowledge/tools`、`ShadowTask` |
| 04 编译工坊 | 资产如何变成组织 | L1–L5 五步线 | 五级编译器产出、准入门槛指标、版本历史 |
| 05 规程卡编排 | 流程如何被强约束 | 节点画布 | `FlowCard/FlowNode/FlowEdge`、节点级 `knowledge_scope` |
| 06 团队看板 | 多人如何组队交付 | 4 列任务 | `WorkgroupTeam`、`MatrixTask`、`SharedBlackboardEntry` |
| 07 竞标验收 | 为什么是 TA 中标 | 候选人对比表 | `TaskSelectionBid`、`deliverable_report` |
| 08 渠道中枢 | 员工在企微/飞书上线了吗 | 5 行渠道列表 | 渠道适配器、幂等/重试指标、SOP 保护窗、身份映射 |
| 09 工具生态 | 工具哪来、端侧是否在线 | MCP 列表 + 工具表 | `MCPClientEngine`、执行沙箱、加密凭证 |
| 10 Trace | 每一步发生了什么 | 六段瀑布 | 标准化 Span、引文高亮、护栏判定、审计留痕 |
| 11 组织进化 | 影子考核如何、要不要回滚 | 影子考核 stepper | `LoopEngine`、知识缺口、版本对比与回滚 |

## 4. 与演进蓝图的对应

| 阶段 | 页面 |
| --- | --- |
| Phase 1 员工实体化 | 02、03、10 |
| Phase 2 SOP 状态机 | 05、04 |
| Phase 3 竞标协同与黑板 | 06、07 |
| Phase 4 全渠道网关 | 08 |
| Phase 5 生态与进化闭环 | 09、11、01 |

## 5. 品牌更名（已落地 · `feat/branding-evolution`）

AutoTeams → AutoTeams；工号 `AFDE-*` / `AT-*` → `ATE-*`；端侧 CLI `autoteams-runner` → `autoteams-runner`。
前端字面量触点：`pages/Login.tsx`、`pages/Register.tsx`、`pages/Invite.tsx`、`pages/Settings.tsx`（含 `X-AutoTeams-Agent-Key` 文案）、`components/Logo.tsx`、`components/HubPage.tsx`、`components/ui/CommandPalette.tsx`、`components/LocalConnectionGuide.tsx`、`components/CompanyValueTour.tsx`、`hooks/useAutonomyMode.ts`、`hooks/useTheme.tsx`、`hooks/useZone.ts`、`components/Layout.tsx`、`utils/productAnalytics.ts`，以及 `autoteams_*` 存储键。

### 5.1 已切换为破坏性更名的部分

| 项 | 变更 | 联动范围 |
| --- | --- | --- |
| 浏览器本地键 | `autoteams_*` → `autoteams_*` | 纯客户端；用户侧栏折叠、主题、导航历史等偏好重置一次 |
| npm 包名 | `autoteams-frontend` → `autoteams-frontend` | `package.json` + `package-lock.json` 同步 |
| 数字工号前缀 | `AT-` → `ATE-` | `services/workforce/profile_service.py::generate_next_badge` 是唯一运行时产出点；`workforce_profiles.employee_badge` 无 DDL 默认值与前缀约束，改前缀不影响已落库数据，但历史行仍保留旧前缀，需数据迁移才会统一 |
| 端侧 CLI | 包名 / bin / 帮助文案 / 安装脚本 / 连接命令 → `autoteams-runner` | `local-runner/{package.json,bin,src}`、三份 `install.ps1`、`runner_session.py::build_setup_command`、`LocalConnectionGuide.tsx`、`DEPLOY.md`；`local-runner.zip` 已重新打包 |

### 5.2 保留旧名（有意为之）

| 项 | 原因 |
| --- | --- |
| `X-AutoFDE-Agent-Key` 仍被后端受理 | 外部 Agent 程序是已发布的公开 API 契约，不能一刀切断。后端改为**新名优先、旧名兜底**（`agent_api_auth.extract_agent_api_key`），CORS 同时放行两个请求头，前端文档只展示新名。过渡期结束后删除 `AGENT_API_KEY_HEADER_LEGACY` 与 CORS 旧条目 |
| `AUTOTEAMS_RUNNER_DOWNLOAD_BASE` 环境变量 | 运维已在 `.env.example` / `deploy.sh` 中设置，属部署契约 |
| `%USERPROFILE%\.autoteams\runner` 安装目录、历史部署域名 | 改路径会让存量安装变成孤儿；域名需 DNS 先就绪 |
| `docs/design/archive/stitch_output/`、`docs/design/archive/clean_stitch_output/`、`autoteams_ui/` 内的 AutoTeams 字样 | v1 原型归档资产，DESIGN.md §8 是对**新界面**的禁令，不是对历史资产的清理要求 |
| `docs/Archived/**` | 历史归档，按约定保留 |

## 6. 落地要点

- 路由建议：`CockpitPage` `WorkforceGallery` `WorkforceProfile` `CompilerStudio` `FlowEditorPage` `WorkgroupKanban` `BiddingArena` `ChannelManagement` `ToolEcosystem` `TraceExplorer` `EvolutionHub`（后四个与蓝图 WBS 命名一致）。
- 现有可复用：`TaskKanban.tsx`、`PipelineStepper.tsx`、`EvolutionTimeline.tsx`、`RuntimeVisualizer.tsx`、`ui/IdChip.tsx`，按新 token 重绘即可保留逻辑。
- 主题：现前端默认浅色主题与 v2 契合，只需把 `tailwind.config.js` 的 brand/surface 色阶对齐 `#FAFAF9/#FFFFFF/#1F4FD8/#0B0B0B`，并为 01/10 两屏提供近黑主题作用域。
- 术语红线：按蓝图 §7.1 映射表执行，界面不得出现外部标杆系统专有名词。

## 7. 复现

```powershell
cd D:\AIProjects\AutoTeams\autoteams_ui
python stitch_client.py tools                     # 查看 Stitch MCP 能力
python -u generate_screens_v2.py                  # 全量重新生成 11 屏（HTML + Stitch 原图）
python -u generate_screens_v2.py 05-flow-editor   # 单屏重生成
python build_gallery.py                           # 重建评审墙（内嵌全部源码）
```

高清截图 `screens-v2/hires/*.png` 由 Chromium 以 `1600×1000 @2x` 逐个打开 HTML 后全页截图得到（浏览器运行时执行，非 Python 脚本）；`build_gallery.py` 检测到 `hires/` 即优先使用，缺失时回退到 Stitch 原始 PNG（512×410）。

项目与设计系统 ID 记录在 `stitch_config.json`；页面提示词与全部设计约束集中在 `generate_screens_v2.py` 的 `RULES` 与 `PAGES` 段，改文案即可迭代。
