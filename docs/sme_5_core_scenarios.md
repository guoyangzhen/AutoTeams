# 中小企业 5 大核心场景 · SOP 字段契约与工作流规则

> 依据：`docs/autoteams_restructuring_plan_latest.md` §4.4.6「中小企业 5 大核心场景落地与专项实现」
> 适用版本：AutoTeams 5.0（`feat/5.0` 分支线）
> 目标读者：数字员工模板开发者、SOP 编排者、后端契约评审人
> 文档性质：**契约文档**。本文所有字段名、状态值、端点路径均取自当前仓库真实代码，
> 不描述"计划中"的理想形态；确实尚不具备的能力一律进入 §7「已知能力缺口」，
> 不在正文中以既成事实的口径承诺。

---

## 0. 阅读约定

| 记号 | 含义 |
|---|---|
| ✅ 具备 | 当前代码已实现，端点与字段可直接使用 |
| ⚠️ 约束 | 可用但有限制，使用前必须知道该限制 |
| ❌ 缺口 | 当前不具备，见 §7 |

**枚举约定**：`employment_status`、`MatrixTask.status`、`File.status` 等在数据库层均为
**无约束的 `String`**，取值靠代码与注释约定，**没有 `Enum` 类型、没有 `CHECK` 约束、
Pydantic 层也未用 `Literal` 校验**。写入非法值不会被数据库拒绝。调用方须自行保证取值合法。

---

## 1. 通用契约基线

5 大场景共用同一套底座，本节只定义一次，后续场景只写差异。

### 1.1 SOP 规程卡（FlowCard）——所有场景的流程载体

持久化于 `flow_cards` 表（`backend/app/models/flow_card.py`），完整结构存于
`flow_data` JSON 列，结构契约由 `backend/app/services/flow_core/schema.py` 的 Pydantic
模型校验。

**规程卡字段（`FlowCard`）**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `flow_id` | str | 是 | — | 规程唯一编号，如 `flow-aftersale-refund` |
| `name` | str | 是 | — | 规程名称 |
| `version` | str | 否 | `"1.0.0"` | 版本号（自由字符串，非 semver 强校验） |
| `description` | str | 否 | `""` | 业务背景与适用场景 |
| `guardrails` | FlowGuardrails | 否 | 三项全 `True` | 三大黄金履约铁律 |
| `start_node_id` | str | 是 | — | 起始节点，必须存在于 `nodes` |
| `nodes` | List[FlowNode] | 是 | — | 节点集合，`min_length=1` |
| `edges` | List[FlowEdge] | 否 | `[]` | 连线集合 |
| `terminal_node_ids` | List[str] | 否 | `[]` | 终态节点清单 |

> **图完整性校验现状**：`validate_graph_integrity` 只校验「起始节点在 nodes 中」与
> 「每条边的源/目标节点存在」。**不校验** `terminal_node_ids` 是否存在，
> **不校验**是否有环，**不校验**是否从 `start_node_id` 可达全部节点。
> 编排者需自行保证，否则引擎在运行时才暴露问题。

**节点字段（`FlowNode`）**

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `node_id` | str（必填） | — | 节点唯一标识 |
| `name` | str（必填） | — | 节点业务名称 |
| `node_type` | NodeType | `"collect_info"` | 见下表 5 值 |
| `instruction` | str | `""` | 大模型指引或动作指令 |
| `expected_slots` | List[str] | `[]` | 本节点需收集的槽位名 |
| `bound_tools` | List[str] | `[]` | 本节点允许调用的工具/MCP |
| `scoped_knowledge_buckets` | List[str] | `[]` | 本节点绑定的知识桶（最小必要暴露） |
| `timeout_seconds` | int | `120` | 单步超时，`ge=1, le=3600` |
| `assignee_role` | Optional[str] | `None` | `approval_human` 节点的审核角色 |

**`node_type` 5 值语义（真值表）**

| 值 | 引擎行为 |
|---|---|
| `collect_info` | 槽位未齐 → `waiting_user_input`；槽位已齐且 `adaptive_slot_filling=true` → **自动跳到下一节点** |
| `action_tool` | 执行动作后**自动推进**；⚠️ 若节点名/指令命中高风险词且 `high_risk_confirmation=true` → 先 `waiting_approval` |
| `branch_condition` | 按出边 `condition_expression` 求值分流，执行后自动推进 |
| `approval_human` | 必 `waiting_approval`；`approval_granted=False` → `failed` |
| `sub_flow` | ⚠️ 引擎无专门处理分支，落到默认分支保持 `running`，**空转消耗一次 `step` 调用** |

**连线字段（`FlowEdge`）**

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `source_node_id` / `target_node_id` | str（必填） | — | 必须存在于 `nodes` |
| `condition_expression` | Optional[str] | `None` | 轻量比较表达式，如 `amount > 1000`；`None`/空 ⇒ 无条件边 |
| `priority` | int | `0` | 多分支优先级，**数字越大越先计算** |
| `label` | Optional[str] | `None` | 仅展示用，引擎不读 |

**边选择算法（编排者必须知道）**：出边按 `priority` **降序**排序后取第一条命中者；
条件表达式在受限命名空间 `{"__builtins__": None}` 中 `eval`，**异常一律降级为 `False`**；
若全部不命中，**回落到优先级最高的那条边**（即最高优先级边天然是默认分支）。
⚠️ 变量名必须在 `accumulated_slots` 中存在，否则 `NameError` → 该边判否。

**执行状态（`FlowExecutionState`）——每一步的输出契约**

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `flow_id` | str（必填） | — | 所属规程 |
| `current_node_id` | str（必填） | — | 当前节点 |
| `accumulated_slots` | Dict[str, Any] | `{}` | 已沉淀的上下文槽位 |
| `status` | Literal | `"running"` | `running` / `waiting_user_input` / `waiting_approval` / `completed` / `failed` |
| `history_trace` | List[Dict] | `[]` | 迁移流水 |
| `last_output` | Optional[str] | `None` | 面向用户的当前话术 |
| `error_message` | Optional[str] | `None` | 失败原因 |

> ⚠️ **执行态是客户端携带的无状态契约**。`POST /api/v1/flow-core/execute/step` 每次
> 都从数据库重新读取规程卡，并把**完整 `FlowExecutionState` 作为请求体回传**。
> 服务端不持久化执行实例，没有 `run_id`，没有断点续跑端点。
> 客户端丢失或篡改 `state` 会静默改变执行结果。`history_trace` 仅记录
> `start_flow` 与 `transition` 两种动作，等待/审批/失败不写 trace。

**三大铁律（`FlowGuardrails`，默认全 `True`）**

| 键 | 作用 | 引擎是否真的读取 |
|---|---|---|
| `closed_loop_required` | 闭环原则：禁止以「请稍候」作最终回复 | ❌ **仅存储，引擎从不读取** |
| `adaptive_slot_filling` | 自适应推进：已提供信息禁止重复追问 | ✅ 生效 |
| `high_risk_confirmation` | 关键确认：高风险写入操作强制二次确认 | ✅ 生效 |

高风险触发词（`HIGH_RISK_KEYWORDS`，引擎硬编码）：

```
退款 转账 支付 删除 清空 重置 修改权限 refund transfer delete
```

命中规则：仅当 `high_risk_confirmation=true` **且** `node_type == "action_tool"`
**且** 关键词出现在 `node.name` 或 `node.instruction`（均转小写）中。
`collect_info` 节点**不**受此拦截。

**槽位抽取规则（决定 SOP 输入能不能被正确解析）**

`_extract_slots` 对每个缺失的 `expected_slots` 按三级优先级取值：
1. 正则 `(?:<槽位名>|订单号|手机号|姓名|金额|原因)[:：\s]+([^\s,，。]+)`
2. 文本中**任意**冒号前缀值 `[:：]\s*([^\s,，。]+)`
3. 若该节点**只声明了 1 个** `expected_slot`，则整段输入去空白后全量赋值

> ⚠️ 多槽位节点收到无结构文本时，**同一个冒号值会被写进每一个缺失槽位**。
> 编排多槽位节点时，要么要求结构化输入，要么拆成多个单槽位节点。

### 1.2 数字员工档案（WorkforceProfile）——权限载体

| 字段 | 类型 | 默认 | 场景用途 |
|---|---|---|---|
| `employee_badge` | str(48) unique | — | 工号，跨 SOP 引用成员的唯一键 |
| `display_name` / `job_title` / `department` | str | `"通用业务部"` | 展示 |
| `duty_boundaries` | JSON | `{"allowed":[],"forbidden":[]}` | **权责边界** |
| `tone_style` | str(64) | `"professional"` | 话术风格 |
| `authorized_flows` | JSON list | `[]` | 授权的 SOP ID 清单 |
| `accessible_knowledge_buckets` | JSON list | `[]` | 可检索知识桶清单 |
| `authorized_tools` | JSON list | `[]` | 可调用工具/MCP 清单 |
| `employment_status` | str(32) | `"shadow"` | `shadow` / `active` / `suspended` / `retired` |
| `performance_score` | Float | `100.0` | 0–100 履约考评（⚠️ 仅 Pydantic 层 `ge=0, le=100`，DB 无约束） |

**边界校验算法（`POST /api/v1/workforce-profiles/{id}/check-boundary`）**

对 `action_intent` 与每条边界做**双向小写子串匹配**：

1. 先查 `forbidden`：命中即 `allowed=false`，返回 `matched_boundary`；
2. `allowed` 非空时，intent 必须命中其一才 `allowed=true`，否则
   `allowed=false`，reason 为「未在岗位明确授权的允许清单中，默认最小权限拦截」；
3. `allowed` 为空且未命中 forbidden → **默认放行**。

> ⚠️ **禁止项优先于允许项**。⚠️ 子串匹配是朴素实现，短意图可能误命中长边界文本，
> 存在假阳性。`allowed` 为空时是**默认放行**而非默认拒绝——新员工的档案若未填
> `allowed`，将不产生任何限制。这与最小权限预期相反，模板初始化时必须显式填写 `allowed`。

### 1.3 协同任务（MatrixTask）——多员工场景的载体

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `team_id` | str(36) | — | 所属工作组（**无 FK 约束**） |
| `parent_task_id` | str(36) | `None` | 递归拆解层级（**无 FK**） |
| `title` / `description` | str | — | 任务描述 |
| `priority` | int | `1` | 越大越紧急，列表按 `DESC` 排序 |
| `status` | str(32) | `"pending"` | 状态机，见下 |
| `suggested_profile_id` / `assignee_profile_id` | str(36) | `None` | 建议人 / 中标执行人（**无 FK**） |
| `deliverable_report` | JSON | `None` | 交付三件套 |
| `review_feedback` | JSON | `None` | Leader 验收意见 |
| `version` | int | `1` | 乐观锁，award/deliver/review 时 +1 |

**任务状态机**：`pending → bidding → in_progress → review → done / rework / escalated`

> ⚠️ `escalated` 只在模型注释中声明，**没有任何服务会写入该值**。
> ⚠️ `rework` **没有**回到 `bidding`/`in_progress` 的实现路径。
> ⚠️ 所有状态跃迁**均无前置状态校验**，`award_and_start_task` 不检查当前状态。

**竞标与 HP 对赌（3 轮竞聘）**

每轮扣血公式（`BiddingEngine.evaluate_bid_round`）：

```
score     = clamp(score, 0, 10)
deduction = (10 - score) * 3            # 单轮 0–30 HP
HP_r      = max(0, HP_{r-1} - deduction)
淘汰判定   = HP_r <= 0
```

HP 跨轮结转：按 `(task_id, candidate_profile_id)` 取 `bid_round` 最大的一行作为当前 HP，
首轮从 `100.0` 起算。落库 `score` 为**未截断的原始值**，扣血用的是截断值。

终局裁决（`adjudicate_winner`）：

```
FinalScore = final_hp * 0.7 + performance_score * 0.3
```

全部候选人 HP 归零时破格取最高分者。⚠️ 该函数**不被 `award` 端点调用**，
中标人由客户端在 `AwardTaskRequest` 中提交。

### 1.4 知识与溯源（File / Message）——所有场景的证据链

**文件登记（`files` 表）**：`status` 取值 `uploaded → processing → completed | failed`；
`chunk_count` / `vector_count` 记录切分与向量化结果；`content_hash` 与 `agent_id`
组成唯一约束 `uq_files_agent_content_hash`（同一文件对同一 Agent 不可重复入库）。

**消息与溯源（`messages` 表）**

| 字段 | 说明 |
|---|---|
| `role` | 实际只落库 `"user"` / `"assistant"` 两种（`system`/`tool` 仅存在于内存提示词） |
| `content` | 正文 |
| `sources` | JSON，**唯一的引用落库位置** |
| `satisfaction` | 单个可空 String，显式值 `satisfied`/`neutral`/`unsatisfied`/`dissatisfied`；隐式值 `implicit:satisfied:copy`/`implicit:unsatisfied:regenerate`/`implicit:unsatisfied:dwell` |
| `token_count` / `model_used` | 成本与实际生效模型（多级降级后） |

> ⚠️ `sources` 字段**两条链路形状不一致**：流式路径写
> `{content, source, file_type, distance}`，非流式路径只写 `{content, source}`。
> 消费方不可假定字段齐备。
> ⚠️ `implicit:*` 满意度值不落入 metrics 聚合的 CASE 分支，
> **对 `AgentKPI.satisfaction_score` 与满意度趋势零贡献**。

### 1.5 横切约束（所有端点适用）

| 约束 | 说明 |
|---|---|
| 路径前缀 | 全部端点以 `/api/v1` 开头；`/health` 与 `/metrics` 除外 |
| 租户隔离 | 企业归属取自 JWT（`current_user.enterprise_id`），**永不从请求体接收** |
| CSRF | 所有 `POST/PUT/PATCH/DELETE` 需 `csrf_token` Cookie + `X-CSRF-Token` 头配对 |
| 机器对机器 | 用 `X-AutoTeams-Agent-Key` 头打 `/api/v1/agent-api/v1/*`（旧名 `X-AutoFDE-Agent-Key` 仍兼容） |
| 限流 | 每个端点带限流装饰器，写操作与 SSE 配额更低 |
| 响应包裹 | 统一 `{"success": bool, "data": ..., "message"?: str}` |

---

## 2. 场景一 · 智能客服（7×24 线上客服接待员）

### 2.1 定位

面向 10–100 人企业最痛的一条：响应不及时流失、夜间周末无人值守、新人培训周期长。
数字员工承担 FAQ 一线应答与留资引导，把复杂客诉与 VIP 客户**升级给人**。

### 2.2 员工档案配置

| 项 | 值 |
|---|---|
| `job_title` | 在线客服专员 |
| `employment_status` | `active`（转正后）/ `shadow`（试用） |
| `duty_boundaries.allowed` | 回答产品常见问题、查询订单状态、引导客户留资、创建工单 |
| `duty_boundaries.forbidden` | 承诺退款金额、修改客户账户权限、直接向客户发送合同条款 |
| `accessible_knowledge_buckets` | `kb-faq`、`kb-product-manual`、`kb-aftersale-policy` |
| `authorized_flows` | `flow-inquiry-intake`、`flow-ticket-create` |
| `authorized_tools` | `order_query`、`ticket_create`、`crm_lead_capture` |

### 2.3 SOP 输入字段契约

**`flow-inquiry-intake`（询盘受理）**

| 槽位（`expected_slots`） | 类型 | 必填 | 采集节点 | 说明 |
|---|---|---|---|---|
| `customer_name` | str | 是 | `collect_info` | 客户称呼 |
| `contact` | str | 是 | `collect_info` | 手机号或邮箱 |
| `inquiry_text` | str | 是 | `collect_info` | 客户原始诉求 |
| `customer_tier` | str | 否 | `collect_info` | `normal` / `vip`，影响升级策略 |
| `order_id` | str | 否 | `collect_info` | 关联订单，用于订单类问题 |

> 采集节点建议**单槽位拆分**（见 §1.1 槽位抽取三级优先级的坑），
> 或在话术中明确要求客户按 `手机号：xxx` 格式回答。

### 2.4 SOP 输出字段契约

| 输出项 | 载体 | 字段 | 说明 |
|---|---|---|---|
| 应答正文 | `FlowExecutionState.last_output` | str | 面向客户的话术 |
| 引用依据 | `messages.sources[]` | `{content, source, file_type, distance}` | FAQ 原文片段 |
| 留资记录 | MatrixTask `deliverable_report` | JSON | `{lead_id, contact, inquiry, captured_at}` |
| 工单 | MatrixTask `deliverable_report` | JSON | `{ticket_id, priority, owner}` |
| 升级信号 | `FlowExecutionState.status` | `"waiting_approval"` | 遇 VIP/复杂客诉时置位 |

### 2.5 工作流规则

1. **知识检索必须限定桶**：`scoped_knowledge_buckets` 只挂 `kb-faq` 等客服桶，
   严禁把财务/法务桶挂到客服节点——这是 `WorkforceProfile.accessible_knowledge_buckets`
   与节点级 `scoped_knowledge_buckets` 的**双重收窄**。
2. **应答前必须过边界**：`POST /api/v1/workforce-profiles/{id}/check-boundary`
   传 `action_intent`，命中 `forbidden` 立即转人工，不得自行解释边界。
3. **升级条件写成分支边**：`customer_tier == "vip"` 或 `inquiry_text` 命中投诉词时，
   走 `priority` 更高的边进入 `approval_human` 节点。
4. **禁止空转回复**：受 `closed_loop_required` 语义约束，
   任何终态 `last_output` 不得为「请稍候 / 正在处理」。⚠️ 该开关**引擎并不读取**，
   需由编排纪律 + 前端话术约束保证。
5. **满意度回流**：用户显式评价写 `PUT /api/v1/conversations/messages/{id}/satisfaction`；
   ⚠️ 不要依赖隐式信号，它不进 KPI 聚合。
6. **全程留痕**：每次应答落 `messages` 一行，`role="assistant"`，
   使客诉可回溯到具体引用片段。

---

## 3. 场景二 · 销售助手（跟进与线索管家）

### 3.1 定位

报价不及时被竞对抢单、销售日志记录繁琐、客户画像分散。
数字员工解析询盘、从产品价表秒级出规范报价、自动记录跟进与提醒。

### 3.2 员工档案配置

| 项 | 值 |
|---|---|
| `job_title` | 销售跟进专员 |
| `duty_boundaries.allowed` | 解析询盘邮件、生成报价单草稿、记录跟进日志、设置下次提醒 |
| `duty_boundaries.forbidden` | 直接向客户发送报价、承诺折扣底线、修改客户信用额度 |
| `accessible_knowledge_buckets` | `kb-price-list`、`kb-standard-clauses`、`kb-competitor-matrix` |
| `authorized_flows` | `flow-inquiry-parse`、`flow-quotation-draft`、`flow-followup-log` |
| `authorized_tools` | `crm_query`、`doc_analyzer`、`chart_renderer` |

### 3.3 SOP 输入字段契约

**`flow-quotation-draft`（报价生成）**

| 槽位 | 类型 | 必填 | 采集节点 | 说明 |
|---|---|---|---|---|
| `customer_name` | str | 是 | `collect_info` | 客户名称 |
| `product_sku` | str | 是 | `collect_info` | 产品型号/编号 |
| `quantity` | int | 是 | `collect_info` | 数量 |
| `amount` | float | 是 | `collect_info` | 客户目标价或预算 |
| `delivery_terms` | str | 否 | `collect_info` | 交付条件 |
| `validity_days` | int | 否 | `collect_info` | 报价有效期，默认 30 |

### 3.4 SOP 输出字段契约

| 输出项 | 载体 | 字段 | 说明 |
|---|---|---|---|
| 报价单草稿 | `FlowExecutionState.last_output` | str | **草稿，非终稿** |
| 询盘解析结果 | MatrixTask `deliverable_report` | JSON | `{customer, items[], pain_points[], decision_makers[]}` |
| 跟进日志 | 黑板条目 `content` | str | 写入 `(team_id, topic)` 唯一文档 |
| 下次提醒 | `deliverable_report` | JSON | `{remind_at, channel, reason}` |
| 发送闸门 | `status == "waiting_approval"` | — | 出站前必须人工核准 |

### 3.5 工作流规则

1. **报价一律走金额分支**：`amount > 1000` 走 `approval_human`（经理核准），
   `amount <= 1000` 走自动草稿。表达式变量名必须与槽位名一致，否则该边判否并
   **回落到优先级最高边**——编排时务必让兜底边指向安全分支。
2. **禁止自动发送**：报价发送是 `action_tool` 节点且指令含"发送"，
   引擎会因命中高风险语义词之外的规则落到 `waiting_approval` 的前提是
   节点名/指令含高风险词；⚠️ **"发送报价"不含 `HIGH_RISK_KEYWORDS` 中的词**，
   因此必须**显式**使用 `approval_human` 节点，不要依赖引擎自动拦截。
3. **禁止项优先**：`forbidden` 含"直接向客户发送报价"，任何外发动作前
   必须先过 `check-boundary`。
4. **跟进日志写黑板**：用 `POST /api/v1/teams/{team_id}/blackboard`，
   以 `topic` 为活文档键做 upsert。注意 `is_pinned` 是**粘性**的
   （`is_pinned or existing.is_pinned`），发帖无法取消置顶。
5. **竞标选人用 HP**：`POST /api/v1/teams/{team_id}/tasks/{task_id}/bid`
   提交 3 轮陈述，得分低则每轮扣 18–30 HP，HP 归零即淘汰。
6. **终局裁决显式化**：如需自动定标，须在服务端显式调用
   `FinalScore = final_hp*0.7 + performance_score*0.3`，
   ⚠️ `/award` 端点**不调用**该函数，直接采信客户端提交的 `winner_profile_id`。

---

## 4. 场景三 · 知识问答（企业数字专家 / 知识管家）

### 4.1 定位

内部文档零散、找制度问老员工耗时长、文档更新后信息不同步。
数字员工承担制度/手册/SOP 的检索与溯源问答，并汇总知识缺口。

### 4.2 员工档案配置

| 项 | 值 |
|---|---|
| `job_title` | 企业知识管家 |
| `duty_boundaries.allowed` | 检索内部文档、回答制度与流程问题、汇总未命中问题、标注文档版本 |
| `duty_boundaries.forbidden` | 对外输出未公开的薪酬与合同原文、代客户做出制度解释承诺 |
| `accessible_knowledge_buckets` | `kb-hr-policy`、`kb-finance-policy`、`kb-it-runbook`、`kb-standard-clauses` |
| `authorized_flows` | `flow-doc-qa`、`flow-gap-collect` |
| `authorized_tools` | `doc_analyzer`、`web_search`、`multilingual_translate` |

### 4.3 SOP 输入字段契约

**`flow-doc-qa`（制度问答）**

| 槽位 | 类型 | 必填 | 采集节点 | 说明 |
|---|---|---|---|---|
| `question` | str | 是 | `collect_info` | 员工原始提问 |
| `dept_scope` | str | 否 | `collect_info` | 提问人部门，决定知识桶 |
| `doc_type` | str | 否 | `collect_info` | `policy` / `manual` / `sop` |
| `as_of_date` | str | 否 | `collect_info` | 期望生效版本日期 |

### 4.4 SOP 输出字段契约

| 输出项 | 载体 | 字段 | 说明 |
|---|---|---|---|
| 答案正文 | `messages.content` | str | 面向提问人 |
| 溯源片段 | `messages.sources[]` | `{content, source, file_type, distance}` | **chunk 级溯源** |
| 相关度 | SSE `metadata` 事件 `self_rag_score` | float 0–1 | ⚠️ 非逐条引用分值 |
| 知识缺口 | 黑板条目 | str | 累计"问了但没答上"的问题 |

> ❌ **「页码高亮」当前不可实现**。RAG 持久化链路中**没有页码、字符偏移、行号、
> 包围盒字段**：PDF 抽取阶段以 `"\n\n"` 拼接各页并丢弃页边界；切分器返回
> 纯 `list[str]`，不产出位置索引。ChromaDB 元数据里唯一的定位量是
> `chunk_index`，而它**没有透出到 `sources`**。
> 现有溯源粒度是 **chunk 级**（文件名 + 原文片段 + 距离），详见 §7.1。

### 4.5 工作流规则

1. **按部门收窄桶**：`dept_scope` 决定 `scoped_knowledge_buckets`，
   跨部门越权检索必须在 SOP 层阻断，而非依赖检索层过滤。
2. **引用必须落库**：非流式链路只写 `{content, source}`，
   消费方渲染引用前需容错缺失 `file_type` / `distance`。
3. **距离不是置信度**：`distance` 是 ChromaDB 原始余弦距离（越小越近），
   ⚠️ 且**重排后不更新**——存下来的是重排前的值，不能用它排序或当质量分。
4. **知识缺口闭环**：检索无命中时写黑板（`topic=knowledge-gaps`），
   定期由人评审补文档——这是"文档更新后信息不同步"的唯一兜底。
5. **质量评估入库**：RAG 四指标（faithfulness / answer_relevancy /
   context_precision / context_recall，0–1）落在 `rag_evaluations` 表，
   与 `messages` 分离，需单独查询。
6. **涉密文件隔离**：`files.is_confidential` / `is_highly_confidential` /
   `confidential_status` 三个字段需在检索入口显式过滤；
   授权与撤销走 `/api/v1/files/{id}/confidential/grant|revoke`。

---

## 5. 场景四 · 繁琐运营（流程自动化助理）

### 5.1 定位

机械重复录入耗费精力、人工审核易疏漏错别字与金额、流程卡顿。
数字员工承担批量报表清洗、凭证初审、关键信息核对与定时催办。

### 5.2 员工档案配置

| 项 | 值 |
|---|---|
| `job_title` | 运营自动化专员 |
| `duty_boundaries.allowed` | 清洗去重报表、抽取凭证字段、核对合同关键信息、发送催办通知 |
| `duty_boundaries.forbidden` | 审批报销、修改原始台账数据、发起对外付款 |
| `accessible_knowledge_buckets` | `kb-finance-policy`、`kb-audit-standards` |
| `authorized_flows` | `flow-table-clean`、`flow-voucher-precheck`、`flow-contract-keyinfo`、`flow-reminder` |
| `authorized_tools` | `regex_scrubber`、`invoice_ocr_parser`、`erp_ledger_query`、`tool_send_notification` |

### 5.3 SOP 输入字段契约

**`flow-table-clean`（报表清洗）**

| 槽位 | 类型 | 必填 | 采集节点 | 说明 |
|---|---|---|---|---|
| `file_id` | str | 是 | `collect_info` | 源文件 ID（`POST /api/v1/files/upload` 返回） |
| `dedupe_key` | str | 是 | `collect_info` | 去重主键列名，如 `contract_no` |
| `amount_column` | str | 是 | `collect_info` | 金额列名，用于求和校验 |
| `output_format` | str | 否 | `collect_info` | `xlsx` / `csv`，默认 `xlsx` |

**`flow-voucher-precheck`（报销凭证初审）**

| 槽位 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `voucher_id` | str | 是 | 凭证单号 |
| `amount` | float | 是 | 报销金额，驱动金额分支 |
| `category` | str | 否 | 费用科目 |
| `has_invoice` | bool | 否 | 是否附发票 |

### 5.4 SOP 输出字段契约

| 输出项 | 载体 | 字段 | 说明 |
|---|---|---|---|
| 清洗报告 | `deliverable_report` | JSON | `{total_in, duplicated, invalid, total_out, checksum}` |
| 差异清单 | `deliverable_report.diff[]` | JSON | `{row_key, column, raw, normalized, action}` |
| 初审结论 | `FlowExecutionState.last_output` | str | 通过 / 疑点 / 驳回建议 |
| 催办通知 | 黑板 + 外发 | str | ⚠️ 外发需渠道确认 |
| 台账写入 | — | — | ❌ 禁止自动写原始台账，见 §7.2 |

### 5.5 工作流规则

1. **先入库再处理**：源文件必须先 `POST /api/v1/files/upload` 落 `files` 表，
   拿到 `file_id` 才能进 SOP。⚠️ `content_hash` 对同一 Agent 唯一，
   重复上传同一文件会被拒。
2. **处理状态三段式**：`uploaded → processing → completed|failed`，
   失败时 `error_message` 有值，⚠️ 没有 CHECK 约束，需编排侧自行校验。
3. **金额必审**：`amount` 驱动分支，大额走 `approval_human`。
   注意引擎高风险词表里有「支付」「退款」，凭证初审节点名若含"支付"会被自动拦截为
   `waiting_approval`——这是期望行为，但要有对应审批人。
4. **禁止自动写台账**：`duty_boundaries.forbidden` 明确禁止修改原始台账，
   所有产出必须是**新文件 + 差异清单**，由人确认后落库。
5. **清洗必须可回滚**：输出必须附 `checksum`（行数 + 金额合计），
   出现差异时能定位到行。
6. **催办幂等**：黑板以 `topic` 为键 upsert，同一 `topic` 重复发帖覆盖 `content`，
   天然幂等；⚠️ 但 `citations` **仅在非空时覆盖**，空值会保留历史引用。

---

## 6. 场景五 · 数据分析（经营数据参谋）

### 6.1 定位

老板看不懂复杂 BI、统计依赖专人每周熬夜做 PPT、对业务波动反应迟钝。
数字员工承接自然语言指标查询、生成结构化简报与异常预警。

### 6.2 员工档案配置

| 项 | 值 |
|---|---|
| `job_title` | 经营数据分析参谋 |
| `duty_boundaries.allowed` | 查询经营指标、生成日报周报、计算同比环比、发出异常预警 |
| `duty_boundaries.forbidden` | 修改业务数据源、对外发布经营数据、下发考核结论 |
| `accessible_knowledge_buckets` | `kb-metric-definition`（指标口径字典） |
| `authorized_flows` | `flow-metric-query`、`flow-daily-brief`、`flow-anomaly-alert` |
| `authorized_tools` | `chart_renderer`、`anomaly_detector`、`erp_ledger_query` |

### 6.3 SOP 输入字段契约

**`flow-metric-query`（指标查询）**

| 槽位 | 类型 | 必填 | 采集节点 | 说明 |
|---|---|---|---|---|
| `metric_name` | str | 是 | `collect_info` | 指标名，须命中 `kb-metric-definition` |
| `time_range` | str | 是 | `collect_info` | 如 `2026-W38` / `last_7d` |
| `dimension` | str | 否 | `collect_info` | 下钻维度，如 `channel` |
| `filters` | JSON | 否 | `collect_info` | 附加过滤条件 |

**`flow-anomaly-alert`（异常预警）**

| 槽位 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `metric_name` | str | 是 | 监控指标 |
| `threshold_pct` | float | 否 | 波动阈值，默认 20% |
| `notify_channel` | str | 否 | 通知渠道 |

### 6.4 SOP 输出字段契约

| 输出项 | 载体 | 字段 | 说明 |
|---|---|---|---|
| 指标值 | `deliverable_report` | JSON | `{metric, value, time_range, dimension, breakdown[]}` |
| 同比环比 | `deliverable_report` | JSON | `{mom, yoy}`，单位 `%` |
| 图表 | 工具产物 | — | `chart_renderer` 返回 |
| 结构化简报 | `FlowExecutionState.last_output` | str | 日报/周报正文 |
| 预警 | 黑板 + 外发 | JSON | `{metric, current, baseline, deviation_pct, severity}` |

### 6.5 工作流规则

1. **口径先行**：所有指标必须先在 `kb-metric-definition` 中登记口径，
   查询节点绑定该桶；口径缺失时应返回"未定义"而非猜测。
2. **只读硬约束**：`duty_boundaries.forbidden` 含"修改业务数据源"，
   分析节点**只允许** `action_tool` 读取类工具，不得出现写库指令。
3. **异常判定用分支边**：`deviation_pct > threshold_pct` 走告警边，
   否则走汇总边。变量名必须与槽位一致。
4. **对外发布需人审**：日报周报若含薪酬/客户敏感数据，
   外发前必须 `approval_human`。
5. **指标回溯**：`GET /api/v1/metrics/business` 是经营指标的唯一真实来源，
   ❌ 不得在前端硬编码任何指标数值（见计划 §4.5.1）。
6. **零值诚实**：接口无数据时必须显示空态引导，
   禁止用兜底假数填充——这是计划 §二「真数据」原则的硬约束。

---

## 7. 已知能力缺口

以下能力在 §4.4.6 中被列为目标，但**当前代码不具备**。本文正文未按既成事实承诺，
此处集中登记，供排期。

### 7.1 知识溯源无页码粒度（阻断「页码高亮」）

- **现状**：`messages.sources[]` 只有 `{content, source, file_type?, distance?}`。
- **缺失**：页码、字符偏移、行号、包围盒、逐条引用相关度分。
- **根因**：PDF 抽取以 `"\n\n"` 拼接各页并丢弃页边界；`ChunkerService`
  返回纯 `list[str]` 无位置副产物；`chunk_index` 未透出到 `sources`。
- **补齐成本**：需在切分层产出位置 sidecar 并写入 ChromaDB 元数据，
  再在 `_build_sources` 透出——属 RAG 链路改造，非配置项。
- **过渡口径**：对外只承诺 **chunk 级溯源**（文件名 + 原文片段）。

### 7.2 缺少业务系统写回通道

`files` / `MatrixTask` 均为内部表，**没有任何端点把分析/清洗结果写回企业既有
ERP/CRM**。场景四的「台账落地」与场景五的「数据源」目前只能产出文件与建议，
需人手工执行或另行接入外部 Agent（§4.4.5）。

### 7.3 竞标与特遣队并行

- `/api/v1/teams`（Team-Matrix）与 `/api/v1/strike_teams`（5.0 特遣队）
  是**两套独立资源**，字段与状态机不互通，勿混用。
- `escalated` 状态、`rework` 回流路径**均未实现**。
- `workforce_profiles` / `matrix_tasks` 等表**没有对应的 Alembic 迁移**，
  仅在测试中由 `Base.metadata.create_all` 建表；生产库需另行迁移。

### 7.4 出站渠道为入站模式

`connectors` 仅提供**入站** webhook（企微 / 飞书）与 `simulate/inbound`，
**无主动外发端点**。场景四催办、场景五预警的"外发"当前需经黑板 + 人工，
或接入外部 Agent。

### 7.5 隐式满意度不进 KPI

`satisfaction` 的 `implicit:*` 取值不落入 `metrics_service` 的聚合分支，
对 `AgentKPI.satisfaction_score` 与满意度趋势**零贡献**。
依赖隐式信号做员工考核会得到失真数据。

---

## 8. 附录 · 状态枚举真值表

| 域 | 枚举 | 取值 | 约束强度 |
|---|---|---|---|
| SOP 执行 | `FlowExecutionState.status` | `running` / `waiting_user_input` / `waiting_approval` / `completed` / `failed` | ✅ Pydantic `Literal` |
| SOP 节点 | `NodeType` | `collect_info` / `action_tool` / `branch_condition` / `approval_human` / `sub_flow` | ✅ Pydantic `Literal` |
| 任务 | `MatrixTask.status` | `pending` → `bidging` → `in_progress` → `review` → `done` / `rework` / `escalated` | ⚠️ 约定，无约束；`escalated` 无写入方 |
| 员工 | `employment_status` | `shadow` / `active` / `suspended` / `retired` | ⚠️ 约定，无约束 |
| 生命周期 | `workforce_lifecycle.stage` | `recruit` / `training` / `production` / `evaluation` / `continuous_learning` / `promotion` / `retired` | ⚠️ 约定，无约束 |
| 文件 | `File.status` | `uploaded` → `processing` → `completed` / `failed` | ⚠️ 约定，无约束 |
| 消息角色 | `Message.role` | `user` / `assistant` | ⚠️ 约定；DB 实际不写入 `system`/`tool` |
| 满意度 | `Message.satisfaction` | `satisfied` / `neutral` / `unsatisfied` / `dissatisfied` / `implicit:satisfied:copy` / `implicit:unsatisfied:regenerate` / `implicit:unsatisfied:dwell` | ⚠️ 自由 String；`implicit:*` 不进 KPI |
| 竞标轮次 | `TaskSelectionBid.bid_round` | 1 = 方案陈述，2–3 = 反驳与细化 | ⚠️ 无上界校验 |
| 特遣队 | `StrikeTeamStatus` | `forming` / `active` / `reviewing` / `dissolved` | ✅ Pydantic `Literal` |

> 上表中"约束强度"为 ✅ 的枚举，非法值会在请求入口被 422 拒绝；
> 为 ⚠️ 的枚举，非法值会**静默写入数据库**并只在业务逻辑深处暴露。
