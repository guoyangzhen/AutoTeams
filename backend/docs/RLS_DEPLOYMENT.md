# 行级安全（RLS）启用与验收

关联审计项：AUD-19。

> **第二轮状态：仍未闭合，禁止按本文历史切换步骤直接放行生产。** 当前代码已增加生产连接角色强制校验；不是只打告警。管理员或拥有业务对象的连接会被拒绝。新策略与矩阵进展见[第二轮审计复核](../../docs/AUDIT_SECOND_REVIEW_2026-09-29.md)。下面的 36 表统计是上一轮历史覆盖，不能作为当前完整清单。

> **第三轮进展（`d5e6f7a8b9c0`）**：新增两个受限角色并把能力拆开 ——
> `autoteams_worker`（队列，表权限镜像 API 角色 + 六个跨租户领取/恢复函数）、
> `autoteams_bootstrap`（渠道引导，**零表权限**，只读指定账号的验签材料）。
> API 角色**没有**任何跨租户领取/恢复权限，数据库级反例见
> `tests/test_rls_bootstrap_postgres.py`。匿名/无租户审计改走受控入口
> `app_append_bootstrap_audit`（动作白名单 + 主体必须等于 `app.current_user_id`
> + 只能追加到链尾），账本原有策略未放宽。
> 生产切换**仍未完成**：需要 `DATABASE_WORKER_URL/ROLE` 与
> `DATABASE_BOOTSTRAP_URL/ROLE`，且 Compose 仍使用管理员连接。
> 详见 [第三轮分支复核](../../docs/AUDIT_R3_BRANCH_REVIEW_2026-09-29.md)。

> 当前认证相关表不加 RLS 是兼容例外，不意味着所有这些表都必须全表授权才能登录。应用层过滤不能替代数据库隔离；这组例外及设备/渠道/worker 引导、匿名安全审计写入还需要受控专用通道。不要靠扩大运行角色权限解决未验收业务路径。


## 背景

迁移 `2026_07_24_0200` 为部分表建了企业隔离策略，策略读取自定义 GUC
`app.current_enterprise_id`。但在该策略写下时：

1. 应用代码**从未设置**这个 GUC，`current_setting(..., true)` 恒为 NULL，
   策略对所有行返回 false；
2. Compose 用 `POSTGRES_USER` 初始化数据库，官方镜像把它建成**超级用户**，
   而超级用户直接绕过 RLS。

也就是说 RLS 既没生效，运维却会以为存在第二道租户隔离 —— 这比没有策略更危险。

## 现在做了什么

| 组件 | 位置 | 作用 |
|---|---|---|
| 租户上下文 | `app/utils/db_tenant_context.py` | `ContextVar` 绑定当前企业 |
| 事务钩子 | `app/database.py` `_apply_tenant_context_on_begin` | 每个事务 `SET LOCAL app.current_enterprise_id` |
| 认证绑定 | `app/utils/auth/deps.py` `get_current_user` | 认证成功后绑定租户 |
| 运行角色 | 迁移 `b6c7d8e9f0a1` | 创建受限 `autoteams_app`，给直接归属表和认证表分别授权 |
| 连接身份检查 | `app/database.py` | 生产 PostgreSQL 强制校验实际运行角色、属性、成员关系、所有权与 row_security；不满足则拒绝 |

### 为什么是 `SET LOCAL` 而不是 `SET`

`SET LOCAL` 的作用域是当前事务，事务结束自动清除。连接归还连接池后不会把上一个
请求的租户带给下一个请求 —— 这是"连接池复用不串租户"的关键。

### 为什么未认证时写空串而不是不设置

未绑定租户时写入空串 `''`，RLS 策略会拒绝所有行。这让"没有租户上下文"表现为
**失败关闭**，而不是"变量缺失时按策略默认值放行"。

## 认证路径上的表为什么不加 RLS

`users` / `enterprises` / `invitations` / `local_path_grants` /
`setup_sessions` / `llm_api_configs` 必须在**登录之前**读取：应用验证凭据时还不知道
调用者属于哪个租户。给它们加 RLS 会让所有人无法登录。

这就是审计所说的"明确无租户维护任务的专用边界"。这些表的隔离由应用层保证：
`app/utils/tenant_scope.py` 的统一入口 + 唯一约束 + 每个查询都带 `enterprise_id`。

## 2026-09-29 第二轮真实 PostgreSQL 验收

在 PostgreSQL 16.15 的**独立空集群**（`127.0.0.1:55434` / `autoteams_omp_acceptance`，
仅本轮审计使用）上新增前向迁移 `c2d3e4f5a6b8`，并重跑了整套验收。

### 覆盖了什么

| 项目 | 结果 |
|---|---|
| `alembic upgrade head`（空库） | 到 `c2d3e4f5a6b8` |
| `downgrade base` → `upgrade head` | 通过 |
| `downgrade -1` → `upgrade head` | 通过 |
| `test_rls_policy_registry.py` | 8 passed（无需数据库；清单与 ORM 元数据一致性） |
| `test_rls_forward_repair.py` | 见下：历史策略名替换、未知策略失败关闭、往返、模型表齐备 |
| `test_rls_postgres_matrix.py` | 41 张直接表 + 19 张间接表 + 账本的 A/B 双租户矩阵 |
| `test_rls_http_identity.py` | 真实 `get_current_user` + HTTP `/api/v1/agents` 的租户传递 |
| `test_health_postgres.py` | 迁移过的真实库上验证 schema 探测（head / 落后 / 未知 / 多 head） |

### 这一轮实际补上的东西

上一轮只覆盖 36 张带 `enterprise_id` 的表，**另有 24 张业务表落在策略之外**：

* 5 张同样带 `enterprise_id` 却从未进清单（`advisor_suggestions`、
  `agent_api_credentials`、`agent_templates`、`approval_gates`、
  `runner_device_commands`）—— 运行角色对它们连权限都没有；
* 19 张靠外键归属的表（上一轮点名的 11 张 + `task_plans`、`skills`、
  `skill_executions`、`conversations`、`messages`、`files`、`operation_snapshots`、
  `interview_questions`、`confidential_file_accesses`）。

间接表按"父表属于本租户"判定，父表自身也有 RLS 且读同一个 GUC，方向一致；
没有租户上下文时子查询看不到任何父行 → 失败关闭。

### 前向修复（针对已 stamp `b6c7d8e9f0a1` 的库）

`b6c7d8e9f0a1` 曾被就地修改过，**已 stamp 该 revision 的库不会重跑它**。
`c2d3e4f5a6b8` 因此对全部清单做幂等重申，并在两种情况下**直接失败而不是放行**：

1. 每张受保护表上存在清单之外的策略（PERMISSIVE 策略取并集，一条被放宽成
   `USING (true)` 就足以让整张表对所有租户可见）；
2. 运行角色持有受保护清单以外表的权限（"全表放开绕过 RLS"）。

历史策略名是 `{table}_enterprise_isolation`，与新名 `{table}_tenant_isolation`
并存时同样取并集；升级会显式删除历史命名，降级会**恢复历史命名**，否则后续迁移
删除 `enterprise_id` 列时会因策略依赖而失败（这正是本轮实测到的 `downgrade base`
失败原因，已修复并加入回归）。

### 平台级表

| 表 | 授权 | 原因 |
|---|---|---|
| `audit_logs` | SELECT / INSERT，**无 UPDATE/DELETE** | 账本只追加。读按 `user_id` 所属租户；写必须带租户上下文且 `user_id` 属于本租户 |
| `audit_chain_state` | SELECT / INSERT / UPDATE | 全平台单链游标，单行且不含租户数据；链推进天然跨租户 |
| `skill_templates` | SELECT | 平台预置目录，无租户数据 |
| `alembic_version` | SELECT | 就绪探针要比对 head；缺这条授权会让运行角色下的健康检查永远 503（本轮实测发现） |

## 仍然 OPEN 的上线阻塞

以下两项**没有**在本轮闭环，切换生产连接前必须解决；它们涉及尚未建立可信身份时的
受保护数据访问与安全事件记录。

1. **匿名安全事件的审计写入（阻断项）**：`log_audit(db, None, ...)` 用于登录失败、
   账户锁定等尚无用户的事件。账本的 INSERT 策略现在要求租户上下文且 `user_id`
   属于本租户，因此受约束运行角色**写不了**这类记录。本轮明确没有用
   `WITH CHECK (true)` 去"解决"它——那等于允许任何租户以别人的 `user_id` 伪造
   审计记录。正确解法是独立的受控安全写入通道（专用角色或 SECURITY DEFINER
   函数 + 服务端签名校验），需与安全侧一起设计后补齐。
2. **设备令牌 / 渠道回调 / 后台 worker 的租户引导**：这些入口在解析租户之前
   就要读 `runner_device_credentials`、`runner_device_tokens`、`channel_*`
   等受保护表。策略已就位，但**引导上下文尚未实现**；在这些入口完成受控引导
   之前，把生产连接切到 `autoteams_app` 会让它们读到空结果而不是权限错误 ——
   失败仍然是关闭的，但不能算通过验收。
**已完成的连接校验（不再列为待实现项）**：`DEBUG=false` 时，每个新 PostgreSQL
物理连接核对实际角色与 `DATABASE_APP_ROLE`、危险属性、成员关系、对象所有权和
row_security；缺少配置或校验失败会拒绝连接。14 个单元测试与 4 个真实 PG 检查
通过。此校验不会解决上述引导缺口；现有 Compose 管理员连接也会被拒绝。

### 可重复执行

仅对**新建、专用的空 PostgreSQL 数据库**运行；`downgrade base` 会删除目标库中
的业务 schema。以下 PowerShell 示例用无口令的本地临时集群，其他环境用各自安全的
连接配置，勿把口令写入命令输出或提交到仓库。

```powershell
cd backend
$env:DEBUG='true'
$env:JWT_SECRET_KEY='test-secret-key-for-unit-tests-only-32chars-plus'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:DATABASE_URL='postgresql+asyncpg://postgres@127.0.0.1:55432/autoteams_rls_acceptance'
E:/AgentProjects/.audit-autoteams-venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head
E:/AgentProjects/.audit-autoteams-venv/Scripts/python.exe -m alembic -c alembic.ini downgrade base
E:/AgentProjects/.audit-autoteams-venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head
E:/AgentProjects/.audit-autoteams-venv/Scripts/python.exe -m alembic -c alembic.ini downgrade -1
E:/AgentProjects/.audit-autoteams-venv/Scripts/python.exe -m alembic -c alembic.ini upgrade head

$env:RLS_TEST_ADMIN_URL='postgresql+psycopg2://postgres@127.0.0.1:55432/autoteams_rls_acceptance'
$env:RLS_TEST_APP_URL='postgresql+asyncpg://autoteams_app@127.0.0.1:55432/autoteams_rls_acceptance'
$env:DATABASE_URL=$env:RLS_TEST_APP_URL
$env:DATABASE_APP_ROLE='autoteams_app'
E:/AgentProjects/.audit-autoteams-venv/Scripts/python.exe -m pytest tests/test_rls_postgres.py -q --tb=line --disable-warnings
```

此修复修改了历史 revision `b6c7d8e9f0a1`，只在**尚未执行该 revision**的库上
自动生效。已 stamp 到该 revision、或手工改过其角色/策略的 PostgreSQL 库，
须先盘点实际 schema 并另做前向修复，不能因版本号为 head 就视为通过。

## 切换步骤

RLS 的正确性依赖**真实运行角色**。本地实库证明上述限定范围内的机制可用，
生产切换仍受下文的业务路径与全表矩阵阻塞。

```bash
# 1. 迁移建角色（已随迁移链执行）
docker compose exec backend alembic upgrade head

# 2. 在 .env / .env.prod 中配置非超级用户的连接串
#    DATABASE_URL=postgresql+asyncpg://autoteams_app:<口令>@postgres:5432/autoteams
#    DATABASE_APP_ROLE=autoteams_app

# 3. 确认运行账户确实不是超级用户
docker compose exec postgres psql -U autoteams_app -d autoteams -c \
  "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;"
# 期望：rolsuper = f, rolbypassrls = f

# 4. 权限矩阵验证（见下）
```

## 生产切换前剩余验收

对每个实际启用 RLS 的租户表，用满足本表约束的 A/B 行验证四类操作在
`autoteams_app` 下被隔离；本地已对 `agents` 完成，其他直接归属表尚待补齐：

```sql
-- 准备：企业 A / B 各一行
SET app.current_enterprise_id = 'ent-A';
SELECT count(*) FROM agents;   -- 只应看到 A 的
INSERT INTO agents (id, enterprise_id, ...) VALUES (...);  -- A 可以
INSERT INTO agents (id, enterprise_id, ...) VALUES (...);  -- 写 enterprise_id='ent-B' 必须被拒（WITH CHECK）
UPDATE agents SET name='x' WHERE enterprise_id = 'ent-B'; -- 影响 0 行
DELETE FROM agents WHERE enterprise_id = 'ent-B';         -- 影响 0 行
```

以及连接池复用场景：同一个连接先服务企业 A 的请求，再服务企业 B 的请求，
确认 A 的事务结束后 `app.current_enterprise_id` 已失效（`SET LOCAL` 语义），
B 的请求不会读到 A 的数据。

## 已知边界与上线阻塞

- 11 张间接归属表没有 `enterprise_id` 列，不能套用直接比较策略：
  `agent_versions`、`matrix_tasks`、`shared_blackboard_entries`、
  `task_selection_bids`、`runner_device_credentials`、`runner_device_tokens`、
  `runner_task_frames`、`counterfactual_diffs`、`optimization_histories`、
  `audit_logs`、`rag_evaluations`。迁移未给运行角色这些表授权；例如 Agent
  更新和设备令牌路径会被权限错误阻断。原候选 `workforce` 在当前 schema 中不存在。
  这些表需要逐表设计经外键归属的策略和授权，不可通过超级用户或全表放开绕过。
- 设备令牌换取/验证、渠道回调账号解析、后台 worker 跨租户领取在解析租户前
  访问受保护表，但当前只有 HTTP `get_current_user` 绑定租户。这些入口必须补
  受限引导上下文或专用受控角色并验收后才能把生产应用连接切到 `autoteams_app`。
- 对已有运行角色，迁移会拒绝其拥有受保护表或为其他角色成员的情况；验收同样
  检查 `NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE/NOINHERIT`、无成员
  关系和无受保护表所有权。应用角色不应被赋予可 `SET ROLE` 到表所有者的成员关系。
- 未配置 `DATABASE_APP_ROLE` 时应用只打 CRITICAL 告警、不拒绝启动，
  因此该配置本身不是运行角色安全证明。生产切换完成后应改成启动即拒绝。
