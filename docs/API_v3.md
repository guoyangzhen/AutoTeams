# AutoTeams v3 API 文档

> 本文档覆盖 v3 重构新增的 API 端点，依据 `docs/spec.md` §10.7 接口契约。
> 与 v1 旧 API（`/api/v1/agents/*`、`/api/v1/setup/*`、`/api/v1/copilot/*` 等）并存，前端逐步切换。
> 基础 URL：`/api/v1`；最后更新：2026-08-21

> 基于实际代码审计（`feat/v3-data-tests` 分支）：所有端点路径、请求体 schema、响应 schema 均对齐 `backend/app/api/*` 与 `backend/app/schemas/*` 实际定义

## 目录

- [设计原则](#设计原则)
- [通用约定](#通用约定)
- [与 v1 API 的对照](#与-v1-api-的对照)
- [WT1 认知层 + 编译器 API](#wt1-认知层--编译器-api)
  - [编译器 Compiler](#编译器-compiler)
  - [认知层 Cognition](#认知层-cognition)
- [WT2 Enterprise Runtime API](#wt2-enterprise-runtime-api)
- [WT3 AI Workforce API](#wt3-ai-workforce-api)
- [WT4 进化层 + 访谈 + 协作 API](#wt4-进化层--访谈--协作-api)
  - [进化层 Evolution](#进化层-evolution)
  - [交互式访谈 Interview](#交互式访谈-interview)
  - [事件驱动协作 Collaboration](#事件驱动协作-collaboration)
- [迁移数据模型对照](#迁移数据模型对照)

---

## 设计原则

1. **渐进替换**：新 API（`/api/v1/compiler/*` 等）与旧 API（`/api/v1/agents/*` 等）并存，前端按 §8 切换计划逐步迁移。
2. **统一响应格式**：沿用 v1 的统一响应结构 `{success, message, data}`，新端点返回 v3 schema 对象作为 `data` 字段。
3. **认证与权限**：所有新端点复用 v1 的 JWT HttpOnly Cookie + CSRF 双提交机制；权限按岗位角色矩阵（`utils/rbac.py`）控制。
4. **幂等性**：除明确标注「非幂等」的端点外，所有 POST/PUT 端点支持重复调用，重复请求返回已存在的资源或最新状态。
5. **错误码**：沿用 `utils/error_codes.py` 常量；新增 v3 业务错误码前缀为 `V3_`。

## 通用约定

| 项目 | 值 |
|------|-----|
| Base URL | `https://your-domain.com/api/v1` |
| 协议 | HTTPS（生产）/ HTTP（开发） |
| 数据格式 | JSON（`Content-Type: application/json`） |
| 字符编码 | UTF-8 |
| 认证 | JWT Bearer Token（HttpOnly Cookie） + CSRF Double Submit |
| 链路追踪 | 所有响应携带 `X-Request-Id` 头 |
| 分页约定 | `?limit=20&offset=0`，响应 `{items: [...], total: N}` |
| 时间格式 | ISO 8601 UTC（`2026-07-29T03:00:00Z`） |

---

## 与 v1 API 的对照

| v1 旧端点 | v3 新端点 | 关系 |
|----------|----------|------|
| `POST /api/v1/setup/start` | `POST /api/v1/interview/sessions` | 替换：访谈式启动替代单向配置 |
| `POST /api/v1/agents/build` | `POST /api/v1/compiler/compile` + `POST /api/v1/workforce/generate` | 拆分：编译与生成分离 |
| `GET /api/v1/agents/{id}` | `GET /api/v1/workforce/{enterprise_id}` | 替换：Workforce 列表替代单 Agent 查询 |
| `POST /api/v1/copilot/chat` | `POST /api/v1/collaboration/events` | 扩展：事件驱动协作替代对话式创建 |
| `POST /api/v1/loop/optimize` | `POST /api/v1/evolution/suggestions/{id}/apply` | 替换：Advisor 建议应用替代 Loop 优化 |
| `GET /api/v1/metrics/dashboard` | `GET /api/v1/evolution/{enterprise_id}/metrics` | 替换：5 类指标 + 成熟度 |
| `GET /api/v1/agents/{id}/version` | `GET /api/v1/runtime/{enterprise_id}/versions` | 替换：Runtime 版本替代 Agent 版本 |

> v1 旧端点保留为兼容层，标注 `@deprecated`，下线时间待前端切换完成后统一公告。

---

## WT1 认知层 + 编译器 API

> 实现位置：`backend/app/api/cognition.py`、`backend/app/api/compiler.py`
> 路由前缀：`router = APIRouter(prefix="/cognition" | "/compiler")`，挂载于 `main.py` 的 `/api/v1`
> 鉴权：所有端点 `Depends(get_current_user)`（JWT HttpOnly Cookie） + 企业隔离校验（`_verify_enterprise_admin`）
> 限流：`@rate_limit_api()`（普通端点）/ `@rate_limit_admin()`（管理员端点）
> 实现状态：✅ 已实现（ cognition 6 个端点 + compiler 6 个端点全部上线）

### 编译器 Compiler

基础路径：`/api/v1/compiler`（router prefix `/compiler` + main.py 挂载 `/api/v1`）

#### POST /compile

触发五级编译（information→knowledge→process→capability→runtime）。请求仅持久化提交可恢复的编译任务；独立 Worker 领取、心跳并执行，客户端通过任务查询或 SSE 观察结果。

**请求体**（`CompileRequest`）：
```json
{
  "enterprise_id": "demo-zhilian",
  "trigger_source": "manual"
}
```

`trigger_source` 在 schema 中为 `str = "manual"`（无 Literal 约束，实际值：`manual`/`file_change`/`interview_complete`/`recompile`）。

**查询参数**（可选）：`folder_path` — 企业文件夹路径，经 `path_security.validate_path` 校验防目录穿越。

**响应**（任务已持久化；`deduplicated=true` 表示复用了相同企业与输入的活动任务）：

```json
{
  "success": true,
  "message": "编译完成",
  "data": {
        "job_id": "comp-2026-001",
    "status": "queued",
    "deduplicated": false,
    "source": "user_upload",
    "folder_name": "enterprise-docs"

  }
}
```

> `status` 是耐久任务状态，初始通常为 `queued`；独立 Worker 领取后变为 `running`，最终为 `completed`、`failed` 或 `cancelled`。显式 `folder_path` 必须通过服务器路径校验；未提供时服务仅可使用受控的示例数据目录。

#### POST /recompile

增量重编译（渐进式建模触发，委托 `ProgressiveModeler.trigger_recompile`）同样提交耐久任务。可显式提供 `folder_path`；省略时仅复用当前企业最近一次 **completed** 编译的输入快照。两者均不可用时返回 `409`，不会创建无法执行的空输入任务。

**请求体**（`RecompileRequest`）：
```json
{
  "enterprise_id": "demo-zhilian",
  "trigger_source": "file_change",
    "affected_stages": ["information", "knowledge"],
  "folder_path": "D:/enterprise-docs"

}
```

`trigger_source` 默认 `"file_change"`；`affected_stages` 可选（`Optional[list[str]]`，未指定时重编译全部 5 级）。

**响应**（`CompileResponse`）：
```json
{
  "success": true,
  "message": "增量重编译任务已创建",
  "data": {
        "job_id": "comp-2026-002",
    "status": "queued"

  }
}
```

#### GET /jobs

分页列出企业的编译任务（`CompilationJobListResponse`）。

**查询参数**：
- `enterprise_id`（必填）：企业 ID
- `limit`（默认 20，范围 1-100）
- `offset`（默认 0，≥0）

**响应**：
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "job_id": "comp-2026-001",
        "enterprise_id": "demo-zhilian",
        "stage": "runtime",
        "status": "completed",
        "progress": 0.0,
        "confidence": 0.87,
        "completeness": 0.85,
        "result": null,
        "started_at": "2026-07-29T03:00:00Z",
        "completed_at": "2026-07-29T03:30:00Z"
      }
    ],
    "total": 1
  }
}
```

`stage` 枚举：`information` / `knowledge` / `process` / `capability` / `runtime` / `complete`。
`status` 枚举：`pending`（历史兼容）/ `queued` / `running` / `completed` / `failed` / `cancelled`。

#### GET /jobs/{job_id}

查询编译任务详情（`CompilationJobResponse`），含各级 artifact 汇总。

**响应**：
```json
{
  "success": true,
  "data": {
    "job_id": "comp-2026-001",
    "enterprise_id": "demo-zhilian",
    "stage": "complete",
    "status": "completed",
    "progress": 0.0,
    "confidence": 0.87,
    "completeness": 0.85,
    "result": {
      "information": {"confidence": 0.92, "discovered_summary": "解析 30 个文件"},
      "knowledge": {"confidence": 0.88, "discovered_summary": "构建 128 节点 / 256 边"},
      "process": {"confidence": 0.85, "discovered_summary": "提取 12 个流程"},
      "capability": {"confidence": 0.90, "discovered_summary": "5 个岗位能力矩阵"},
      "runtime": {"confidence": 0.85, "discovered_summary": "Runtime v1.0.0"}
    },
    "started_at": "2026-07-29T03:00:00Z",
    "completed_at": "2026-07-29T03:30:00Z"
  }
}
```

> `result` 实际为 `dict[str, Any]`，键为 stage 名，值为该级 artifact 的 `confidence` 与 `discovered_summary`。

#### GET /animation/{job_id}

获取编译动画数据（`AnimationResponse`，前端可视化用，展示每级编译过程）。

**响应**：
```json
{
  "success": true,
  "data": {
    "stages": [
      {"name": "信息层", "status": "completed", "discovered": "解析 30 个文件", "confidence": 0.92},
      {"name": "知识层", "status": "completed", "discovered": "构建 128 节点 / 256 边", "confidence": 0.88},
      {"name": "流程层", "status": "completed", "discovered": "提取 12 个流程", "confidence": 0.85},
      {"name": "能力层", "status": "completed", "discovered": "5 个岗位能力矩阵", "confidence": 0.90},
      {"name": "运行时层", "status": "completed", "discovered": "Runtime v1.0.0", "confidence": 0.85}
    ]
  }
}
```

> 注意：`name` 字段实际返回**中文标签**（"信息层"/"知识层"/"流程层"/"能力层"/"运行时层"），而非英文 stage 名。`status` 仅用 `pending`/`running`/`completed`。`discovered` 为字符串摘要（非数字）。

#### GET /completeness/{enterprise_id}

查询企业编译完成度（`CompletenessResponse`，五维加权）。

**响应**：
```json
{
  "success": true,
  "data": {
    "overall": 0.85,
    "dimensions": {
      "information": 0.90,
      "knowledge": 0.85,
      "process": 0.80,
      "capability": 0.85,
      "runtime": 0.85
    },
    "level": "runnable",
    "gaps": [
      {
        "gap_type": "process",
        "description": "缺少审批流文档",
        "affected_positions": ["pos-finance-mgr"],
        "impact_on_completeness": 0.05,
        "suggestion": "补充财务审批 SOP"
      }
    ]
  }
}
```

`level` 枚举：`runnable` / `basic` / `incomplete`。`gaps[].gap_type` 枚举：`data` / `process` / `role` / `knowledge` / `tool`。无编译任务时返回 `overall=0.0 / level="incomplete" / gaps=[]`。

### 认知层 Cognition

基础路径：`/api/v1/cognition`（router prefix `/cognition` + main.py 挂载 `/api/v1`）

#### GET /knowledge-graph/{enterprise_id}

获取企业知识图谱（`KnowledgeGraphResponse`，节点 + 边）。支持按实体类型过滤与分页。

**查询参数**：
- `node_type`（可选）：按实体类型过滤（13 类实体之一）
- `limit`（默认 100，范围 1-500）
- `offset`（默认 0，≥0）

**响应**（节点/边结构来自 `schemas/compiler.py:GraphNode/GraphEdge`，`model_dump()` 后放入 `list[dict]`）：
```json
{
  "success": true,
  "data": {
    "enterprise_id": "demo-zhilian",
    "nodes": [
      {"node_id": "org-1", "node_type": "organization", "name": "智链物联", "attributes": {}, "confidence": 0.95},
      {"node_id": "dept-sales", "node_type": "department", "name": "销售部", "attributes": {}, "confidence": 0.90},
      {"node_id": "pos-sl-2021-045", "node_type": "position", "name": "销售代表", "attributes": {"employee_id": "SL-2021-045"}, "confidence": 0.88}
    ],
    "edges": [
      {"source_id": "org-1", "target_id": "dept-sales", "relation": "has_department", "attributes": {}},
      {"source_id": "dept-sales", "target_id": "pos-sl-2021-045", "relation": "has_position", "attributes": {}}
    ],
    "version": "v1.0.0"
  }
}
```

> 字段名：节点用 `node_id`/`node_type`/`name`/`attributes`/`confidence`；边用 `source_id`/`target_id`/`relation`/`attributes`（无 `id` 字段）。8 类关系：`has_department`/`has_position`/`has_employee`/`has_product`/`has_customer`/`has_process`/`has_kpi`/`reports_to`。

#### PUT /knowledge-graph/{enterprise_id}

增量更新知识图谱（`KnowledgeGraphCreate`，添加节点和边）。

**请求体**：
```json
{
  "nodes": [
    {"node_id": "cust-001", "node_type": "customer", "name": "华智新能源", "attributes": {"tier": "A"}, "confidence": 0.9}
  ],
  "edges": [
    {"source_id": "org-1", "target_id": "cust-001", "relation": "has_customer", "attributes": {}}
  ]
}
```

**响应**：
```json
{
  "success": true,
  "message": "知识图谱已更新",
  "data": {
    "node_count": 129,
    "edge_count": 1
  }
}
```

#### GET /profile/{enterprise_id}

获取企业画像（`EnterpriseProfileResponse`）。尚未生成时返回 `data: null` + `message: "尚未生成企业画像"`。

**响应**（嵌套结构 `EnterpriseProfileData`）：
```json
{
  "success": true,
  "data": {
    "enterprise_id": "demo-zhilian",
    "profile": {
      "basic": {"name": "智链物联", "industry": "工业 IoT", "scale": "50-200 人", "revenue": "5000万-1亿", "location": "深圳", "founded": "2018"},
      "tags": ["工业 IoT", "B2B"],
      "org_summary": {"department_count": 7, "headcount": 120, "key_roles": ["CEO", "销售总监", "财务经理"]},
      "maturity": {"level": "L2", "automation_coverage": 0.45, "ai_workforce_count": 5},
      "business": {"main_products": ["SL-T100", "SL-GW500"], "target_industries": ["新能源", "园区"], "core_processes": ["销售", "客服", "售后"]},
      "gaps": ["财务审批流未文档化"],
      "version": "v1.0.0",
      "updated_at": "2026-07-29T03:30:00Z",
      "completeness_score": 0.85
    }
  }
}
```

#### POST /profile/{enterprise_id}

生成企业画像（从知识图谱提取，委托 `EnterpriseProfiler.generate_profile`）。

**响应**：同 GET /profile/{enterprise_id}，`message: "企业画像已生成"`。

#### GET /operating-model/{enterprise_id}

获取企业运行模型（`OperatingModelResponse`）。尚未构建时返回 `data: null` + `message: "尚未构建运行模型"`。

**响应**（嵌套结构 `EnterpriseOperatingModelData`）：
```json
{
  "success": true,
  "data": {
    "enterprise_id": "demo-zhilian",
    "model": {
      "version": "v1.0.0",
      "completeness": 0.85,
      "organization": {"departments": [...], "reporting_tree": {...}},
      "roles": [
        {"id": "role-sales-rep", "title": "销售代表", "department": "销售部", "level": "L2", "responsibilities": [...], "required_skills": [...], "kpi_ids": [...], "permission_ids": [...]}
      ],
      "processes": [
        {"id": "proc-sales-v2", "name": "销售流程 v2", "type": "sop", "steps": [...], "owner_role_id": "role-sales-mgr", "participants": [...], "trigger_event": "new_inquiry", "system_ids": [...]}
      ],
      "capabilities": [
        {"role_id": "role-sales-rep", "required_capabilities": [...], "knowledge_sources": [...], "tools": [...]}
      ],
      "runtime_rules": {"collaboration_rules": [...], "data_flow_rules": [...], "escalation_rules": [...]},
      "gaps": [{"area": "审批流", "severity": "medium", "suggestion": "补充财务审批 SOP"}]
    },
    "version": "v1.0.0"
  }
}
```

#### POST /operating-model/{enterprise_id}

构建运行模型（从知识图谱提取 6 大块，委托 `OperatingModelBuilder.build_model`）。

**响应**：同 GET /operating-model/{enterprise_id}，`message: "运行模型已构建"`。

---

## WT2 Enterprise Runtime API

基础路径：`/api/v1/runtime`

Runtime 是五级编译器的产物，是被 Agent 直接读取运行的组织实体（含组织/角色/流程/权限/KPI/知识/工具/协作图）。支持版本管理与回滚。

#### GET /{enterprise_id}

获取当前激活的 Runtime（`RuntimeCompileResult` 结构）。

**响应**：
```json
{
  "success": true,
  "data": {
    "runtime_id": "rt-2026-001",
    "enterprise_id": "demo-zhilian",
    "version": "v3.0.0",
    "model_version": "mv-1.0",
    "compiled_at": "2026-07-29T03:30:00Z",
    "completeness": 0.85,
    "organization": {"departments": [...], "reporting_tree": {...}},
    "agents": [...],
    "processes": [...],
    "is_active": true
  }
}
```

#### GET /{enterprise_id}/versions

分页列出 Runtime 版本。

**查询参数**：`limit`、`offset`。

**响应**：
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "version": "v3.0.0",
        "compiled_at": "2026-07-29T03:30:00Z",
        "completeness": 0.85,
        "is_active": true
      },
      {
        "version": "v2.9.0",
        "compiled_at": "2026-07-25T10:00:00Z",
        "completeness": 0.78,
        "is_active": false
      }
    ],
    "total": 2
  }
}
```

#### GET /{enterprise_id}/versions/{version}

按版本号获取指定 Runtime。

`version` 路径参数：如 `v3.0.0`。

**响应**：同 `GET /{enterprise_id}`。

#### POST /{enterprise_id}/rollback

回滚到指定 Runtime 版本。

**请求体**：
```json
{
  "target_version": "v2.9.0"
}
```

**响应**：
```json
{
  "success": true,
  "data": {
    "new_active_version": "v2.9.0",
    "status": "rolled_back"
  }
}
```

> **约束**：只能回滚到已存在的版本；当前激活版本不可回滚到自身。
> 回滚操作会创建一条新的 `runtime_versions` 记录（带 `is_active=True`），原激活版本设为 `is_active=False`。

#### GET /{enterprise_id}/diff

对比两个版本的差异。

**查询参数**：`?a=v2.9.0&b=v3.0.0`。

**响应**（`RuntimeDiffResponse`）：
```json
{
  "success": true,
  "data": {
    "version_a": "v2.9.0",
    "version_b": "v3.0.0",
    "changes": [
      {"section": "organization", "change_type": "modified", "key": "departments[0].head", "detail": "由 张三 变更为 李四"},
      {"section": "agents", "change_type": "added", "key": "agent-003", "detail": "新增售后 Agent"}
    ],
    "summary": "共 2 项变更：1 项修改、1 项新增",
    "a_compiled_at": "2026-07-20T03:00:00Z",
    "b_compiled_at": "2026-07-29T03:30:00Z"
  }
}
```

字段说明（`RuntimeDiffChange`）：
- `section`：变更区域（`organization` / `agents` / `process_engines` / `tools` / `knowledge` / `collaboration_graph`）
- `change_type`：变更类型（`added` / `removed` / `modified`）
- `key`：标识符（agent_id / engine_id / tool_id 等）
- `detail`：可空，变更详情描述

#### GET /{enterprise_id}/organization

获取 Runtime 中的组织运行时（`RuntimeOrganization`）。

**响应**：
```json
{
  "success": true,
  "data": {
    "departments": [
      {"dept_id": "sales", "name": "销售部", "parent_dept_id": null, "head_employee_id": "SL-2020-001", "level": 1}
    ],
    "reporting_tree": {"sales": {"manager": "SL-2020-001", "members": ["SL-2021-045", "SL-2022-058"]}}
  }
}
```

#### GET /{enterprise_id}/agents

获取 Runtime 中的 Agent 配置模板列表（`AgentConfigTemplate`）。

**响应**：
```json
{
  "success": true,
  "data": {
    "agents": [
      {
        "agent_id": "agent-sales-001",
        "agent_name": "销售 Agent",
        "role_id": "role-sales-rep",
        "department": "销售部",
        "level": "L2",
        "system_prompt": "你是智链物联的销售代表...",
        "skills": [...],
        "knowledge_bases": ["kb-sales-faq", "kb-product-spec"],
        "tools": [...],
        "permissions": ["approve_quotation_under_5w"],
        "memory_config": {"short_term": {"max_turns": 20}, "long_term": {"enabled": true}},
        "kpi_ids": ["kpi-sales-revenue", "kpi-response-time"]
      }
    ]
  }
}
```

#### GET /{enterprise_id}/processes

获取 Runtime 中的流程引擎实例列表（`ProcessEngineInstance`）。

**响应**：
```json
{
  "success": true,
  "data": {
    "processes": [
      {
        "process_id": "proc-sales-v2",
        "name": "销售流程 v2",
        "version": "v2.0.0",
        "steps": [...],
        "approval_gates": [...]
      }
    ]
  }
}
```

---

## WT3 AI Workforce API

基础路径：`/api/v1/workforce`

Workforce 是基于 Runtime 能力矩阵生成的 AI 数字员工集合，支持 6 步生成流程与生命周期管理（MVP 3 阶段：recruit / training / production）。

#### POST /generate

触发生成 AI 员工推荐列表（基于 Runtime 能力矩阵）。

**请求体**：
```json
{
  "enterprise_id": "demo-zhilian"
}
```

**响应**（返回推荐列表，尚未创建 Agent）：
```json
{
  "success": true,
  "data": {
    "recommendations": [
      {
        "position_id": "pos-sales-rep",
        "position_name": "销售代表",
        "department": "销售部",
        "level": "L2",
        "required_skills": [...],
        "estimated_kpi": [...],
        "capability_match": 0.92
      }
    ],
    "total": 5
  }
}
```

#### POST /confirm

确认推荐列表，批量创建 AI 员工（Agent）。

**请求体**：
```json
{
  "enterprise_id": "demo-zhilian",
  "confirmed_position_ids": ["pos-sales-rep", "pos-presales", "pos-finance", "pos-cs", "pos-aftersales"],
  "adjustments": {
    "pos-sales-rep": {"system_prompt_override": "你是智链物联资深销售代表..."}
  }
}
```

`adjustments` 可选：针对特定岗位的覆盖配置。

**响应**（`ConfirmResponse`）：
```json
{
  "success": true,
  "data": {
    "created_agents": [
      {"agent_id": "agent-001", "position_id": "pos-sales-rep", "position_name": "销售代表", "lifecycle_stage": "recruit"}
    ],
    "failed": [
      {"position_id": "pos-x", "position_name": "已存在", "error": "Agent already exists"}
    ]
  }
}
```

字段说明：
- `created_agents`（`CreatedAgentInfo`）：`agent_id` / `position_id` / `position_name` / `lifecycle_stage`（默认 `recruit`）
- `failed`（`FailedAgentInfo`）：`position_id` / `position_name` / `error`

#### GET /{enterprise_id}

分页列出 Workforce（含运行指标）。

**查询参数**：`limit`、`offset`。

**响应**：
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "agent_id": "agent-001",
        "name": "销售 Agent",
        "position_id": "pos-sales-rep",
        "lifecycle_stage": "production",
        "stage_entered_at": "2026-07-29T04:00:00Z",
        "metrics": {"tasks_completed": 12, "avg_response_time": 1.5}
      }
    ],
    "total": 5
  }
}
```

#### GET /{agent_id}/lifecycle

查询 Agent 生命周期状态与历史。

**响应**（`LifecycleResponse`）：
```json
{
  "success": true,
  "data": {
    "stage": "production",
    "stage_entered_at": "2026-07-29T04:00:00Z",
    "history": [
      {"stage": "recruit", "stage_entered_at": "2026-07-29T03:30:00Z", "transition_reason": null},
      {"stage": "training", "stage_entered_at": "2026-07-29T03:45:00Z", "transition_reason": "面试通过"}
    ]
  }
}
```

字段说明（`LifecycleHistoryEntry`）：`stage` / `stage_entered_at` / `transition_reason`（可空）

#### POST /{agent_id}/transition

触发阶段转换。

**请求体**：
```json
{
  "target_stage": "production",
  "reason": "training_complete"
}
```

`target_stage` 枚举：`recruit` / `training` / `production`。

**响应**：
```json
{
  "success": true,
  "data": {
    "new_stage": "production",
    "status": "transitioned"
  }
}
```

> **约束**：阶段转换必须按 `recruit → training → production` 顺序，不允许跳级（如 `recruit` 直接 `production`）。

#### GET /{agent_id}/memory/{conversation_id}

查询 Agent 在指定对话中的记忆（三层：短期/实体/长期）。

**响应**：
```json
{
  "success": true,
  "data": {
    "short_term": [
      {"role": "user", "content": "SL-T100 的测温范围是多少？", "timestamp": "2026-07-29T05:00:00Z"}
    ],
    "entity": [
      {"entity": "SL-T100", "attributes": {"测温范围": "-40~+125°C", "精度": "±0.3°C"}}
    ],
    "long_term": [
      {"summary": "客户咨询了 SL-T100 参数", "created_at": "2026-07-29T05:05:00Z"}
    ]
  }
}
```

---

## WT4 进化层 + 访谈 + 协作 API

### 进化层 Evolution

基础路径：`/api/v1/evolution`

进化层包含 AI Advisor（建议生成与应用）、AI Org Analytics（5 类指标 + L1-L5 成熟度）、Evolution Timeline。

#### GET /{enterprise_id}/suggestions

分页列出 AI Advisor 生成的建议。

**查询参数**：`limit`、`offset`。

**响应**（`SuggestionListResponse`）：
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "sug-001",
        "enterprise_id": "demo-zhilian-v3",
        "type": "knowledge",
        "title": "销售 Agent 缺少报价生成技能",
        "description": "建议补充 quote_generator 技能",
        "impact": "预计提升报价准确率 15%",
        "status": "pending",
        "applied_at": null,
        "created_at": "2026-07-29T06:00:00Z"
      }
    ],
    "total": 1
  }
}
```

字段说明（`AdvisorSuggestion`）：
- `type` 取值：`knowledge` / `process` / `capability` / `organization`（`SuggestionTypeLiteral`）
- `status` 取值：`pending` / `applied` / `rejected`（`SuggestionStatusLiteral`）
- `impact` / `applied_at` 可空

#### POST /suggestions/{id}/apply

应用一条建议（如调整 Agent 技能、修改 prompt、补充知识）。

**响应**（`ApplySuggestionResponse`）：
```json
{
  "success": true,
  "data": {
    "applied": true,
    "suggestion_id": "sug-001",
    "affected_agents": ["agent-001"],
    "message": "已应用建议并通知相关 Agent"
  }
}
```

#### POST /suggestions/{id}/reject

拒绝一条建议。

**请求体**（`RejectSuggestionRequest`）：
```json
{
  "reason": "已手动处理"
}
```

**响应**（`RejectSuggestionResponse`）：
```json
{
  "success": true,
  "data": {
    "rejected": true,
    "suggestion_id": "sug-001"
  }
}
```

#### GET /{enterprise_id}/metrics

获取企业组织分析指标（5 类 + 成熟度）。

**查询参数**：`?period=daily|weekly|monthly`（默认 `weekly`）。

**响应**：
```json
{
  "success": true,
  "data": {
    "agent_workload": {"agent-001": {"tasks": 12, "avg_duration": 1.5}},
    "process_efficiency": {"sales-v2": {"avg_cycle_time": 24, "bottleneck": "approval"}},
    "tool_usage": {"quote_generator": {"calls": 8, "success_rate": 0.95}},
    "business_impact": {"revenue": 128400, "conversion_rate": 0.6},
    "maturity_level": "L2"
  }
}
```

`maturity_level` 枚举：`L1`（初始）→ `L2`（受控）→ `L3`（定义）→ `L4`（量化管理）→ `L5`（优化）。MVP 目标 L2。

#### GET /{enterprise_id}/timeline

获取进化时间线（关键事件流水）。

**查询参数**：`limit`、`offset`。

**响应**：
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "event_id": "evt-001",
        "type": "runtime_compiled",
        "description": "Runtime v3.0.0 编译完成",
        "timestamp": "2026-07-29T03:30:00Z",
        "metadata": {"version": "v3.0.0", "completeness": 0.85}
      }
    ],
    "total": 1
  }
}
```

### 交互式访谈 Interview

基础路径：`/api/v1/interview`

访谈式企业启动，7 大类问题库（sales / customer_service / procurement / finance / hr / data / kpi），渐进式对话完善运行模型。

#### POST /sessions

启动一次访谈会话。

**请求体**（`StartSessionRequest`）：
```json
{
  "enterprise_id": "demo-zhilian"
}
```

**响应**（`StartSessionResponse`）：
```json
{
  "success": true,
  "data": {
    "session_id": "iv-001",
    "status": "active",
    "total_count": 0
  }
}
```

`status` 取值：`active` / `completed`（`SessionStatusLiteral`）。

#### GET /sessions/{id}

查询会话状态。

**响应**（`SessionStatusResponse`）：
```json
{
  "success": true,
  "data": {
    "session_id": "iv-001",
    "status": "active",
    "answered_count": 5,
    "total_count": 20,
    "completeness": 0.25
  }
}
```

#### GET /sessions/{id}/next-question

获取下一道问题（基于已完成度智能选择）。

**响应**（`NextQuestionResponse`）：
```json
{
  "success": true,
  "data": {
    "question_id": "q-006",
    "category": "sales",
    "question": "贵司销售流程的关键审批节点有哪些？",
    "priority": "P1",
    "affected_field": "operating_model.approval_flow"
  }
}
```

字段说明：
- `category` 取值：`sales` / `customer_service` / `procurement` / `finance` / `hr` / `data` / `kpi`（`CategoryLiteral`）
- `priority` 取值：`P0` / `P1` / `P2`（`QuestionPriorityLiteral`）
- 无下一问时所有字段为 `null`

#### POST /sessions/{id}/answers

提交一道问题的回答。

**请求体**（`SubmitAnswerRequest`）：
```json
{
  "question_id": "q-006",
  "answer": "<5万经理审批，5-20万总监审批，>20万 CEO 审批"
}
```

**响应**（`SubmitAnswerResponse`）：
```json
{
  "success": true,
  "data": {
    "updated_completeness": 0.30,
    "recompile_triggered": false,
    "next_question": {
      "question_id": "q-007",
      "category": "finance",
      "question": "财务经理是否必审所有报价单？",
      "priority": "P1",
      "affected_field": "operating_model.approval_flow"
    }
  }
}
```

> `next_question` 为 `null` 表示访谈完成。

### 事件驱动协作 Collaboration

基础路径：`/api/v1/collaboration`

事件总线 + 3 种人机协作模式（人类审批介入 / AI 提议确认 / 人类接管）+ 错误处理与回滚。

#### POST /events

发布一个业务事件（触发后续 Agent 协作链）。

**请求体**（`PublishEventRequest`）：
```json
{
  "enterprise_id": "demo-zhilian-v3",
  "event_type": "new_inquiry",
  "payload": {
    "opp_id": "OPP-001",
    "customer_id": "C-001",
    "product_interest": "SL-T100×80+SL-GW500×2"
  },
  "source_agent_id": "agent-sales-001",
  "target_agent_id": null
}
```

字段说明：
- `enterprise_id`（必填）：企业 ID
- `event_type`（必填，最长 64 字符）：事件类型（如 `new_inquiry`/`product_query`/`quotation`/`financial_review`/`approval`/`deal_closed`/`customer_sync`/`after_sales`）
- `payload`（可选，默认 `{}`）：事件负载
- `source_agent_id` / `target_agent_id`（可选）：来源/目标 Agent

**响应**（`PublishEventResponse`）：
```json
{
  "success": true,
  "data": {
    "event_id": "evt-001",
    "status": "pending"
  }
}
```

`status` 取值范围：`pending` / `processed` / `failed`（`EventStatusLiteral`）。

#### GET /events

分页查询事件。

**查询参数**：
- `enterprise_id`（必填）
- `type`（可选，过滤事件类型）
- `limit`、`offset`

**响应**（`EventListResponse`）：
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "evt-001",
        "enterprise_id": "demo-zhilian-v3",
        "event_type": "new_inquiry",
        "payload": {...},
        "source_agent_id": "agent-sales-001",
        "target_agent_id": null,
        "status": "processed",
        "created_at": "2026-07-29T05:00:00Z"
      }
    ],
    "total": 1
  }
}
```

#### POST /approvals/{id}/approve

审批通过（人机协作模式 1：人类审批介入）。

**请求体**：
```json
{
  "comment": "同意，按 S 级大客户价执行"
}
```

**响应**：
```json
{
  "success": true,
  "data": {
    "approved": true,
    "process_resumed": true
  }
}
```

#### POST /approvals/{id}/reject

审批拒绝。

**请求体**：
```json
{
  "reason": "折扣超出授权范围"
}
```

**响应**：
```json
{
  "success": true,
  "data": {
    "rejected": true
  }
}
```

#### POST /rollback

触发回滚（基于操作快照）。

**请求体**（`RollbackRequest`）：
```json
{
  "snapshot_id": "snap-2026-001"
}
```

**响应**（`RollbackResponse`）：
```json
{
  "success": true,
  "data": {
    "rolled_back": true,
    "agent_id": "agent-001",
    "operation": "send_quotation",
    "snapshot_id": "snap-2026-001"
  }
}
```

> 回滚基于 `operation_snapshots` 表中保存的操作前状态快照。

---

## 迁移数据模型对照

v3 新增表与 API 端点的关系对照（迁移文件位于 `backend/migrations/versions/2026_07_29_*.py`）：

| 迁移文件 | 新增表/字段 | 主要消费 API |
|---------|-----------|------------|
| `2026_07_29_0300-a1b2c3d4e7f1_add_cognition_tables.py` | `knowledge_graphs` / `enterprise_profiles` / `enterprise_operating_models` | `/cognition/*` |
| `2026_07_29_0302-a2b3c4d5e8f2_add_compiler_tables.py` | `compilation_jobs` | `/compiler/*` |
| `2026_07_29_0304-a3b4c5d6e9f3_add_runtime_tables.py` | `enterprise_runtimes` / `runtime_versions` | `/runtime/*` |
| `2026_07_29_0306-a4b5c6d7f0a4_extend_enterprise_fields.py` | `enterprises.current_runtime_version_id` | `/runtime/{enterprise_id}/rollback` |
| `2026_07_29_0308-a5b6c7d8f1b5_add_workforce_tables.py` | `workforce_lifecycle` | `/workforce/{agent_id}/lifecycle` |
| `2026_07_29_0310-a6b7c8d9f2c6_add_memory_tables.py` | `long_term_memories` / `entity_memories` | `/workforce/{agent_id}/memory/{conversation_id}` |
| `2026_07_29_0312-a7b8c9d0f3d7_extend_agent_fields.py` | `agents.lifecycle_stage` / `memory_config` / `kpi_ids` / `position_id` | `/workforce/*` |
| `2026_07_29_0314-a8b9c0d1f4e8_add_evolution_tables.py` | `advisor_suggestions` / `org_metrics` | `/evolution/*` |
| `2026_07_29_0316-a9b0c1d2f5e9_add_interview_tables.py` | `interview_sessions` / `interview_questions` | `/interview/*` |
| `2026_07_29_0318-a0b1c2d3f6f0_add_collaboration_tables.py` | `collaboration_events` / `approval_gates` / `operation_snapshots` | `/collaboration/*` |

所有迁移均含可执行的 `downgrade` 函数，且为加性迁移（仅新增表/字段/索引，不改现有字段类型），符合 `spec.md` §3.2 数据库迁移约定。
