# AutoTeams Agent API

> **定位**：Agent API 是 AutoTeams 面向外部工作流、应用和 AI Agent 的受限 REST 产品接口。它与浏览器登录会话分离，使用仅展示一次的机器密钥、企业归属、细粒度 scope、可选 Agent allow-list、到期和撤销机制进行控制。

## 1. 安全模型

机器凭证只能由所属企业的管理员在已登录的控制台会话中创建。完整 API key 仅在创建响应中返回一次；服务端只保存其 HMAC-SHA256 摘要，列表接口仅显示不可认证的前缀。

| 控制项 | 行为 |
|---|---|
| 密钥传递 | 每个外部请求必须携带 `X-AutoTeams-Agent-Key: afd_sk_...`；不得将密钥放入 URL、日志、前端代码或聊天提示词。 |
| 企业隔离 | 请求的企业由服务端根据密钥固定，任何请求体、路径参数或客户端声明都不能切换企业。 |
| 最小权限 | 每个凭证只获得创建时授予的 scope；缺失 scope 返回 `403`。 |
| Agent 范围 | 可选 `allowed_agent_ids` 是精确 allow-list；空数组表示允许访问该企业全部 Agent。 |
| 生命周期 | 凭证支持过期和即时撤销。所有者账户停用或企业停用后，密钥立即失效。 |
| 审计 | 密钥创建、撤销和机器调用均写入审计日志；审计记录不保存聊天提示词正文。 |
| 限流 | 所有机器端点使用独立的 `AGENT_API_RATE_LIMIT_PER_MINUTE` 配额。 |

> **第一版刻意不开放**编译提交、Agent 构建提交/恢复、审批、人类接管、本地路径授权及任何本地文件写入/删除能力。外部 Agent 可读取状态并对获授权的数字员工发起对话，但不能绕过人工与企业治理边界。

## 2. 创建、保存与撤销凭证

管理员使用现有浏览器登录会话访问如下管理端点。该路径仍受 CSRF 和管理员权限保护，不可由外部机器密钥调用。

| 操作 | 方法与路径 | 说明 |
|---|---|---|
| 创建 | `POST /api/v1/agent-api/credentials` | 返回一次性完整密钥。 |
| 列表 | `GET /api/v1/agent-api/credentials` | 返回前缀、scope、范围、上次使用时间与撤销/到期状态。 |
| 撤销 | `POST /api/v1/agent-api/credentials/{credential_id}/revoke` | 立即令密钥失效，不可恢复。 |

创建请求示例：

```json
{
  "name": "客户支持编排器（生产）",
  "scopes": ["agent:read", "agent:chat", "build:read", "compiler:read"],
  "allowed_agent_ids": ["agent-customer-support"],
  "expires_at": "2026-12-31T00:00:00Z"
}
```

生产环境应将创建响应中的 `api_key` 立即写入专用密钥管理系统。若发生泄露、人员离职、服务迁移或调用方环境不再受控，应先撤销旧凭证、再创建新凭证；不要尝试通过修改前缀或数据库字段“轮换”。

## 3. 机器调用端点

所有机器端点以 `/api/v1/agent-api/v1` 为前缀，并返回项目统一结构 `{success, message, data}`。

| Scope | 方法与路径 | 行为 |
|---|---|---|
| `agent:read` | `GET /agents` | 返回企业中当前凭证可见的 Agent 摘要。 |
| `agent:read` | `GET /agents/{agent_id}` | 返回单个获授权 Agent 的摘要。 |
| `agent:chat` | `POST /agents/{agent_id}/chat` | 发起同步 RAG 对话并持久化会话与消息。 |
| `compiler:read` | `GET /compiler/jobs/{job_id}` | 查询同企业耐久编译任务的状态摘要。 |
| `build:read` | `GET /build/jobs/{task_id}` | 查询同企业耐久 Agent 构建任务的状态摘要。 |

对话示例：

```bash
curl --request POST "https://autoteams.example.com/api/v1/agent-api/v1/agents/agent-customer-support/chat" \
  --header "Content-Type: application/json" \
  --header "X-AutoTeams-Agent-Key: $AUTOTEAMS_AGENT_API_KEY" \
  --data '{"content":"请基于当前知识库总结退款流程，并列出引用来源。"}'
```

如果要延续会话，将上一次响应的 `data.conversation_id` 放回请求体的 `conversation_id`。该 ID 只是会话标识而不是能力令牌；服务端仍会按机器凭证的企业、Agent 范围和 scope 重新校验。

## 4. 错误处理与运行约束

| 状态码 | 含义 | 调用方动作 |
|---|---|---|
| `401` | 密钥缺失、无效、撤销、到期，或所属账户/企业已停用。 | 停止重试，检查密钥生命周期与调用环境。 |
| `403` | scope 缺失或未被允许访问目标 Agent。 | 请求管理员以最小范围创建/调整新凭证；不要扩大现有凭证。 |
| `404` | 资源不存在，或不属于该凭证所在企业。 | 将其视为不可见资源，不要根据错误推断其他企业数据。 |
| `429` | 调用超出机器 API 配额。 | 使用指数退避并降低并发；不得绕过限流。 |
| `5xx` | 受控服务故障或下游 LLM/RAG 临时不可用。 | 仅对幂等读取安全重试；对话调用应使用调用方请求 ID 记录，避免无界重复。 |

机器 API 依赖与正常业务一致的 PostgreSQL、Worker、ChromaDB 与 LLM 配置。`agent:chat` 是具有成本和消息持久化副作用的操作，调用方必须提供自身的用户确认、预算与重试上限。对于高影响业务动作，应通过 AutoTeams 的现有审批、构建和编译流程由人类管理员显式发起。

## 5. 生产配置

生产环境必须设置独立且随机的 `AGENT_API_KEY_HMAC_SECRET`，不要让它回退到 JWT 或审计签名密钥。推荐生成方式：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

可选变量：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `AGENT_API_KEY_HMAC_SECRET` | 空 | 机器密钥摘要的专用 HMAC 密钥；生产必填。 |
| `AGENT_API_KEY_PREFIX` | `afd_sk_` | 密钥识别前缀；变更会使旧密钥无法通过格式校验。 |
| `AGENT_API_RATE_LIMIT_PER_MINUTE` | `60` | 单实例/Redis 共享限流的机器 API 每分钟配额。 |

远程 MCP 适配层位于独立分支。其生产接入应采用 OAuth/OIDC 资源服务器、令牌受众校验和最小权限 scope；REST Agent API 的 HMAC 机器密钥适合服务到服务的受控调用，不替代用户授权委托。
