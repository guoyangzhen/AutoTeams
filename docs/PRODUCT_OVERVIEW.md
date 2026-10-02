# AutoTeams 产品总览

> 面向**新接手这台机器或第一次读这个仓库**的读者：先看定位与边界，再看链路，最后照着「新机器最短路径」把环境跑起来。
> 本文只写仓库里能读到的实现与已知缺口。不写 ROI、客户数量、生产验收结论——这些没有代码或留证支撑。
> 更新日期：2026-09-30。

## 1. 定位

**AutoTeams 是面向中小企业的 AI 数字员工工作台。** 企业把自己的资料、岗位和重复流程整理成可管理的数字员工，再在同一工作台里配置职责边界、推进流程、处理审批、查看审计痕迹，并在需要时回滚到已保存的版本。

使用它的人通常是三类：

| 角色 | 在工作台里做什么 |
| --- | --- |
| 企业负责人 / 业务主管 | 看今天有什么待处理（驾驶舱）、裁决待审批、看组织指标 |
| 数字员工配置者 | 建岗位档案、划定可执行与禁止职责、把经验写成可审批的 SOP |
| 审批人 / 系统管理员 | 审批流程节点、查审计日志、配置渠道账号与工具授权 |

它**不是**聊天机器人（对话只是运行过程的一种呈现），也**不是**通用 RPA 或任意命令执行器。流程里的工具节点如果缺少配置会明确失败，不会伪造执行结果（见 [flow_core/tool_executor.py](../backend/app/services/flow_core/tool_executor.py)）。

## 2. 工作台导航

左侧栏两个分组、七个入口，定义在 [frontend/src/components/Layout.tsx](../frontend/src/components/Layout.tsx)；完整路由表在 [frontend/src/App.tsx](../frontend/src/App.tsx)。

| 入口 | 路由 | 用途 |
| --- | --- | --- |
| 总览驾驶舱 | `/dashboard` | 运行状态、事件趋势、待裁决项 |
| 数字员工 | `/workforce` | 花名册、岗位档案、职责允许/禁止项与边界探针 |
| 工作流 | `/flows`（`?tab=sop` / `?tab=team`） | 规程卡编排、团队协同看板 |
| Agent 接入 | `/agents` | AI 员工构建、对话与流式回答 |
| 数据看板 | `/analytics` | 员工指标与运行数据 |
| 审计日志 | `/audit-logs` | 全量审计查询、导出与链校验 |
| 组织设置 | `/settings` | 账户、企业与主题 |

旧路径 `/compile`、`/runtime`、`/build`、`/knowledge`、`/canvas` 统一重定向到 `/flows`；`/tools`、`/channel-management`、`/runner-studio` 重定向到 `/connectors` 的对应标签页。截图或外部材料引用旧路径时会自动落到新页面，但语义可能已变。

## 3. 全链路：资料 → 岗位 → 编译 → 运行 → 审批 → 审计 → 回滚

| 环节 | 做了什么 | 源码落点 | 实际边界 |
| --- | --- | --- | --- |
| 资料接入 | 上传文件与文件夹、扫描提取、顾问式访谈问答 | [files.py](../backend/app/api/files.py)、[folders.py](../backend/app/api/folders.py)、[interview.py](../backend/app/api/interview.py) | 不保证自动理解任意企业文档；抽取质量取决于模型与文档类型 |
| 岗位 | 岗位推荐与生成、岗位档案、可执行/禁止职责、边界探针 | [workforce.py](../backend/app/api/workforce.py)、[workforce_profiles.py](../backend/app/api/workforce_profiles.py)、[workforce/generator.py](../backend/app/services/workforce/generator.py) | 花名册档案与底层 Agent 是两类对象；**未绑定 Agent 的档案不能对话**，页面已禁用该入口 |
| 编译 | 五级流水线（信息 → 知识 → 流程 → 能力 → Runtime），输入快照排为持久任务，可查状态与完成度 | [compiler.py](../backend/app/api/compiler.py)、[compiler/pipeline.py](../backend/app/services/compiler/pipeline.py) | 编译产物是候选运行模型，需要人工确认与审批后才能进入真实流程 |
| 运行 | FlowCard 保存 / 从文本合成 / 启动 / 单步推进，执行状态在服务端持久化，会话与工具轨迹可见 | [flow_core.py](../backend/app/api/flow_core.py)、[flow_core/engine.py](../backend/app/services/flow_core/engine.py)、[run_store.py](../backend/app/services/flow_core/run_store.py) | 缺配置的工具节点会失败或标为演示，不会伪装成功 |
| 审批 | 运行中的人工审批接口、待办聚合（顶栏通知与驾驶舱共用同一数据源） | [flow_core.py](../backend/app/api/flow_core.py) 的 approve 端点、[frontend/src/api/collaboration.ts](../frontend/src/api/collaboration.ts) | 审批人不在场时流程停在审批点；没有超时自动放行 |
| 审计 | 全量审计日志、导出与链校验，签名密钥独立保管 | [audit_logs.py](../backend/app/api/audit_logs.py)、[密钥轮换指南](密钥轮换指南.md) | 完整性依赖 `AUDIT_SIGNING_KEY` 妥善保管与轮换；链校验通过不等于业务动作正确 |
| 回滚 | Enterprise Runtime 与 Agent 均有版本列表、diff 与回滚 | [runtime.py](../backend/app/api/runtime.py)、[agents/crud.py](../backend/app/api/agents/crud.py) | 回滚只回到**本系统已保存**的版本，不会撤销外部系统（渠道、第三方 SaaS）已发生的动作 |

## 4. 外部插件与渠道对接：现在到哪一步

### 已在主工作台内

| 能力 | 落点 | 边界 |
| --- | --- | --- |
| MCP 工具目录 | [mcp_evolution.py](../backend/app/api/mcp_evolution.py) 的 `/mcp/servers`、`/mcp/tools`、`/mcp/call` | 工具在授权目录内执行；目录归属与 CLI 授权传递有实现约束，不是任意命令通道 |
| 渠道账号与 Webhook | [connectors.py](../backend/app/api/connectors.py)：企业微信 URL 验证 / 回调、飞书事件校验，加密与验签在 [connectors/signature.py](../backend/app/services/connectors/signature.py) | 协议实现（签名校验 + AES 解密）已在本地验证；**尚无真实企业租户与真实账号的端到端验收**，见 [AUDIT_R3_WECOM_2026-09-29.md](AUDIT_R3_WECOM_2026-09-29.md) |
| 本地入站模拟 | `connectors.py` 的 `/simulate/inbound` | 用于本地演练回调链路，不代表真实渠道消息 |
| Local Runner | [runner_v2.py](../backend/app/api/runner_v2.py)：设备注册、令牌轮换、撤销、心跳、任务领取与回执、挑战校验；载荷契约见 [local-runner/contract/runner_v2_payloads.json](../local-runner/contract/runner_v2_payloads.json) | 设备回执与凭据属于**本机状态**，克隆仓库不会带走；换机需重新注册 |
| 外部 Agent 受限 API | [external_agents.py](../backend/app/api/external_agents.py)：连接、派发、断开；密钥策略见 [AGENT_API.md](AGENT_API.md) | 外部 Agent 只能在受限动作集内操作，越权请求被拒绝 |

### 不在主工作台内

抖音 / 小红书评论与私信回复、Instagram / TikTok 线索抓取与邮件跟进、企微数据库自然语言查询、客服问答等能力由**独立插件**提供，**尚未整合进本仓库**，也没有源码或演示材料可供复核（见 [PRODUCT_EVIDENCE.md](bp/delin-2026/PRODUCT_EVIDENCE.md) 第 8 节）。不要把主工作台的测试结果归因给这些插件，也不要在对外材料里把它们写成已交付功能。

## 5. 身份、安全与多租户

- 登录态：JWT + Refresh，Cookie 配置见 `.env.example`（`COOKIE_SECURE` / `COOKIE_SAMESITE` / Cookie 名称）。
- 多租户：员工、文件、通知、审计等数据按企业与登录人归属，切换身份不会沿用上一份快照（[useNavigationData.ts](../frontend/src/hooks/useNavigationData.ts)）。
- 公开注册默认关闭（`REGISTRATION_ENABLED=false`）；`DEBUG=false` 时后端会拒绝无防护的公开注册。
- Agent API 密钥只存摘要（`AGENT_API_KEY_HMAC_SECRET`），限流按分钟配额。
- 审计链签名使用独立密钥，轮换流程见 [密钥轮换指南](密钥轮换指南.md)。

这些是代码里已落地的机制。**是否在你的部署环境里配置正确，属于部署方的责任，本仓库不代为承诺。**

## 6. 已验证与未验证

已在本机留证的部分（细节见对应审计文档）：

- 后端 pytest 回归、前端 vitest 交互回归与 Vite 构建在本地通过。
- MCP 读取授权目录文件的链路，在常驻演示与真实 Chrome 下界面、API、端侧内容一致（[AUDIT_USABILITY_FOLLOWUP_2026-09-30.md](AUDIT_USABILITY_FOLLOWUP_2026-09-30.md)）。

未验证、对外不应声称的部分：

- 没有付费客户、收入、市场份额或可量化降本增效的证据。
- 真实企业渠道（企业微信 / 飞书）账号的端到端验收尚未完成。
- 全自主运行、任意本地命令执行、完整人工接管均未实现；部分人工接管接口仍返回 501。
- 生产环境就绪度仍有未关闭项，见 [AUDIT_SECOND_REVIEW_2026-09-29.md](AUDIT_SECOND_REVIEW_2026-09-29.md) 与运行边界说明 [技术架构与运行边界_2026-08-22.md](技术架构与运行边界_2026-08-22.md)。

## 7. 新机器最短路径

主路径：`E:/AgentProjects/AutoTeams/AutoTeams`（仓库根）。完整交接说明见 [NEW_MACHINE_HANDOFF_2026-09-30.md](NEW_MACHINE_HANDOFF_2026-09-30.md)。

1. **克隆**：用有权限的 GitHub 账号或 SSH 密钥克隆 `https://github.com/guoyangzhen/AutoTeams.git`，不要把 PAT 拼进 URL。
2. **自检**：在仓库根运行 `python scripts/check_new_machine.py`，它只校验 BP、归档、依赖锁与 Runner 压缩包，不读秘密、不碰数据库。
3. **运行时**：Python 3.11；Node 18 / npm 9+（`frontend/package.json` 的 `engines` 限定 `<19`）；完整编排另需 Docker + Compose；浏览器验收另需 Chrome。
4. **环境变量**：从 `.env.example` 复制出 `.env`（只放在仓库根），为本机生成新的 `JWT_SECRET_KEY`、`AUDIT_SIGNING_KEY`、`BRIDGE_INTERNAL_SECRET`、`METRICS_AUTH_TOKEN`，并配置模型与数据库。不要从历史日志里抄示例值当生产口令。
5. **后端**：`cd backend` → `pip install --require-hashes -r requirements.lock` → `alembic upgrade head` → `uvicorn app.main:app --reload --port 8000`。
6. **前端**：`cd frontend` → `npm ci` → `npm run dev`（端口 3000）。其它子目录（`collaboration-service`、`local-runner`、`electron`）按需各自 `npm ci`。
7. **首个管理员**：`docker compose exec -e BOOTSTRAP_ADMIN_PASSWORD='<强口令>' backend python -m scripts.bootstrap_admin --email … --enterprise … --name …`，随后到「审计日志」确认 `bootstrap / enterprise` 记录。
8. **别指望克隆能带走的**：演示数据库、模型与渠道凭据、Runner 设备回执、旧机 `.env`、构建产物。这些属于本机状态或秘密材料，必须在新机重新配置或按受控流程迁移。

## 8. 文档地图

- 现在就读：[README.md](../README.md)、[产品说明书](AutoTeams产品说明书.md)、[核心场景字段契约](sme_5_core_scenarios.md)
- 运维：[部署指南](DEPLOY.md)、[后端 API](API_v3.md)、[Agent API](AGENT_API.md)
- 溯源：[BP 源码研究](bp/delin-2026/PRODUCT_EVIDENCE.md)、`docs/AUDIT_*` 与 `docs/ENGINEERING_*` 记录、`docs/Archived/` 历史归档
