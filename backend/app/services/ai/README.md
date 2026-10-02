# AI 能力包（`app/services/ai/`）

AutoTeams 5.0 决策四「**LLM 策略 — DeepSeek 优先 + 多模型 + BYOK**」的实现层。

> 依据：`docs/autoteams_restructuring_plan_latest.md` §决策四、§4.2.2（服务层重构 — 消灭巨型文件）

本包是 `app/services/llm_service.py` 的实现体。`llm_service.py` 已从 585 行巨型类
瘦身为**兼容门面**，保留全仓 21 个模块依赖的公共 API；实际逻辑分布如下：

| 模块 | 行数 | 职责 |
|---|---|---|
| `registry.py` | 310 | Provider 注册表、决策四路由矩阵、4.0 国产模型别名/预设 |
| `router.py` | 228 | 候选链装配、故障转移编排、熔断状态代理 |
| `credentials.py` | 184 | 凭证解析、BYOK 运行时切换 |
| `circuit.py` | 61 | 模型级熔断器 |

---

## 一、多模型路由矩阵

### 1.1 任务类型 → Provider 链

定义于 `registry.ROUTING_MATRIX`，**顺序即尝试顺序**：

| 任务类型 `TaskType` | 取值 | 首选 | 备选 | 决策依据 |
|---|---|---|---|---|
| 日常对话 / 客服 | `conversation` | **DeepSeek** | Kimi (Moonshot) | 高频、低延迟优先 |
| 复杂推理 / 分析 | `reasoning` | **DeepSeek** | GLM-4 (智谱) | GLM-4 的 Function Call 与复杂业务逻辑 |
| 代码相关 | `code` | 外接 Codex/Claude | DeepSeek | 决策四要求代码交给外接 Agent |
| 文档总结 / 摘要 | `summarization` | **DeepSeek** | Kimi (Moonshot) | Kimi 的超长上下文适合长文档研读 |
| Embedding | `embedding` | DeepSeek | — | 仅登记矩阵以保持决策四可追溯 |

```python
ROUTING_MATRIX = {
    TaskType.CONVERSATION:   (Provider.DEEPSEEK, Provider.MOONSHOT),
    TaskType.REASONING:      (Provider.DEEPSEEK, Provider.ZHIPU),
    TaskType.CODE:            (Provider.EXTERNAL, Provider.DEEPSEEK),
    TaskType.SUMMARIZATION:  (Provider.DEEPSEEK, Provider.MOONSHOT),
    TaskType.EMBEDDING:      (Provider.DEEPSEEK,),
}
```

### 1.2 4.0 层级语义 → 5.0 任务类型

全仓 21 个模块仍在传 `tier=ModelTier.STRONG/CHEAP/DEFAULT`。这两个常量**不删除**，
而是在 `router.build_candidates` 入口映射为任务类型，从而让旧调用方零改动接入新矩阵：

| 4.0 `tier` | → 5.0 `TaskType` | 旧调用方举例 |
|---|---|---|
| `strong` | `REASONING` | `agents/chat.py`、`rag/agentic_rag.py` |
| `cheap` | `SUMMARIZATION` | `rag/query_classifier.py`、`rag/reranker_service.py` |
| `default` | `CONVERSATION` | `skill_executor.py`、`task_planner.py` |

未识别的 tier 回落 `CONVERSATION`（最保守的日常对话路径）。

### 1.3 模型 id 的单一真源

**代码不硬编码供应商模型名。** 所有模型 id 一律取自 `settings`：

| Provider | 配置项 | 默认值 |
|---|---|---|
| DeepSeek | `DEEPSEEK_MODEL` / `_STRONG` / `_CHEAP` | `deepseek-chat` |
| Moonshot (Kimi) | `MOONSHOT_MODEL` | `moonshot-v1-128k` |
| Zhipu (GLM-4) | `ZHIPU_MODEL` | `glm-4-plus` |
| OpenAI | `OPENAI_MODEL` / `_STRONG` / `_CHEAP` | `gpt-4` / `gpt-3.5-turbo` |

> 决策四要求的主力模型 **DeepSeek-V4.1-Flash** 对应 `DEEPSEEK_MODEL`，
> 由运维在 `.env` 中配置。代码**不假定该 id 在上游一定存在**——这样模型下线/改名
> 只需改配置，不需改代码或发版。

---

## 二、Provider 优先级与候选链装配

### 2.1 五级装配优先级

`router.build_candidates()` 按固定优先级装配候选链，**前者覆盖后者**：

| 级别 | 来源 | 语义 |
|---|---|---|
| ① | 调用点 `explicit_model` | 完全接管，**不再追加任何矩阵候选** |
| ② | BYOK（调用点级 > 会话级） | 换凭证但仍走矩阵（决策四：切换即时生效） |
| ③ | 决策四矩阵链 | 按任务类型逐 Provider 装配 |
| ④ | `LLM_FALLBACK_MODELS` | 运维显式配置的矩阵外兜底 |
| ⑤ | 实例自带 `model`/`api_key` | 4.0 语义：全部不可用时回到构造期参数 |

级别 ⑤ 存在的理由：`LLMService(api_key="sk-xxx", model="gpt-4")` 即使矩阵 Provider
全未配置 Key，该实例仍应能调用。这是 4.0 既有行为，`test_llm_service.py` 有断言。

### 2.2 无凭证 Provider 的处理

矩阵链中未配置凭证的 Provider **保留在链上但标记 `usable=False`**（`reason="no_credentials"`），
调用方遇到即跳过。这样做的原因：

- 不会发起注定 401 的调用浪费 RTT 与配额；
- 但**不丢弃**，使 `router.describe_route()` 能如实展示「哪些候选缺凭证」，
  设置页可直接据此提示用户去配置 Kimi / GLM 的 Key。

### 2.3 `Provider.EXTERNAL` 不由本路由器代调

代码任务首选 `EXTERNAL`（外接本地 Codex/Claude Code），但它**没有 LiteLLM 模型 id**。
`build_candidates` 会跳过它（`model is None`），实际调用交由本地 Agent 桥接层处理。
本包不代为实现桥接，只在矩阵中保留该位置以对齐决策四。

---

## 三、BYOK 运行时即时切换

### 3.1 决策四的原始要求

> - 用户在设置页配置自己的 API Key
> - 支持 OpenAI / DeepSeek / Kimi (Moonshot) / GLM (Zhipu) / 任意 OpenAI 兼容接口
> - 密钥加密存储（复用现有 credential_crypto 模块）
> - **模型切换即时生效，无需重启**

### 3.2 凭证解析优先级

`credentials.resolve_credentials()` 自高而低：

1. 调用点级 BYOK（`ByokProfile`）
2. 会话级 BYOK 覆盖（`set_active_byok()` 设置，进程内全局）
3. 目标模型 **provider 前缀**对应的 `settings` 凭证
4. 全局 `OPENAI_*` 凭证（OpenAI 兼容端点兜底）

第 3 条是「多 Provider 降级」的基础：主模型走 `DEEPSEEK_*`、降级模型走 `MOONSHOT_*`，
各自取各自前缀的凭证，互不污染。

### 3.3 切换入口

```python
from app.services.ai import ByokProfile, set_active_byok

# 设置页保存后调用，下一次 LLM 调用立即走新凭证，无需重启进程
set_active_byok(ByokProfile(provider="moonshot", api_key="<密文>"))
set_active_byok(None)   # 恢复默认，回落到 settings
```

**任意 OpenAI 兼容自建端点**（vLLM / Ollama / 自建网关）通过 `provider` + `model` +
`api_base` 接入，无需改动路由器：

```python
set_active_byok(ByokProfile(
    provider="vllm", api_key="<密文>",
    model="my-org/custom-7b", api_base="http://10.0.0.5:8000/v1",
))
```

自建 Provider 若**未提供 `model`**，无法从 settings 推断，候选会被跳过并告警——
而不是发出一个注定失败的请求。

### 3.4 密钥加密存储：显式双构造，不做隐式回退

这是本包最容易写错、也最需要说清的一处。

```python
# 内存态：api_key 是明文
profile = ByokProfile(provider="deepseek", api_key="sk-...")
profile.key                      # → "sk-..."

# 落库读取：api_key_cipher 是密文，严格解密
profile = ByokProfile.from_encrypted(provider="deepseek", api_key_cipher=encrypt_credential(plain))
profile.key                      # → 明文
```

**为什么不做「解密失败自动当明文用」**：`app/utils/credential_crypto.py` 的 P2-11
修复明确删除了该回退，理由写在原注释里——

> 原容错设计使加密形同虚设：数据库泄露时攻击者写入的明文凭证会被原样采用，
> 历史明文也永远无需迁移。

若在 `ByokProfile.key` 里按 `sk-` 前缀放行，等于把这个洞重新打开。因此：

- `ByokProfile(...)` **不做任何解密**，只持有内存明文；
- `ByokProfile.from_encrypted(...)` **只接受密文**，解密失败抛 `CredentialDecryptError`；
- 两者不混用，调用方必须明确自己处于哪一侧。

### 3.5 凭证不外泄

- `RouteCandidate.__repr__` 只输出 `model` / `provider` / `usable`，**不含 `api_key`**；
- `router.describe_route()` 返回的快照字段为 `model` / `provider` / `usable` / `reason`，无凭证；
- 日志只记录 provider 与模型名。

`test_llm_router.py::test_route_snapshot_never_leaks_credentials` 对此有断言。

---

## 四、重试与熔断规范

### 4.1 三个层次互不干扰

| 机制 | 作用域 | 计数依据 | 配置项 |
|---|---|---|---|
| 单模型内重试 | 同一个 model | `_is_retryable_error()` 判定 | `LLM_REQUEST_RETRY_ATTEMPTS`（默认 2） |
| 模型间故障转移 | 候选链 | 逐个候选 | 候选链本身（§2） |
| 熔断 | 按 model 名 | 连续失败次数 | `LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD`（5） |

分层的原因：单模型瞬态抖动不该立刻换供应商（会丢上下文），
而持续失败也不该无限重试（会放大调用量）。

### 4.2 可重试判定

```python
return code in (408, 409, 425, 429) or code >= 500
```

- **可重试**：408 超时、409 冲突、425 过早、429 限流、5xx 服务端；
- **不可重试**：其余 4xx（参数错误 400、鉴权 401/403 等）——立即故障转移，重试无意义；
- **取不到状态码**（网络层异常）时视为可重试。

### 4.3 指数退避 + 抖动

```python
delay = min(LLM_RETRY_MAX_BACKOFF_SECONDS,      # 上限 4.0s
            LLM_RETRY_INITIAL_BACKOFF_SECONDS * (2 ** attempt))  # 0.5s → 1s → 2s
delay *= 0.8 + random.random() * 0.4           # ±20% 抖动
```

抖动是必需的：多个并发请求在 429 限流窗口后会**同时**退避到同一时刻，
形成同步重试风暴。

### 4.4 异常传播：原样重抛，不包壳

不可重试错误或最后一次尝试失败时，**原样 `raise` 底层异常**，不包成 `RuntimeError`。

原因：调用方的故障转移与错误分类都依赖异常类型（`status_code`）。
包一层会把 4xx/5xx 全部退化成同一种错误，监控与降级策略随之失效。
`test_unit/test_llm_resilience.py` 对此有断言（`pytest.raises(InvalidRequestError)`）。

### 4.5 熔断器

```python
if service.is_open(model):        # 冷却期内
    raise LLMCircuitOpenError(...) # 快速失败 → 调用方换下一个候选
```

- 连续失败达 `LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD`（5）次 → 进入冷却；
- 冷却时长 `LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS`（30s）；
- **任意一次成功立即清零**该模型的失败计数与冷却状态。

作用域是**进程内**：防止同一 API 实例对已知故障模型持续重试造成级联超时。
跨实例的集中式限流仍由网关/供应商侧负责。

### 4.6 流式调用的例外

`chat_stream` **不做模型间故障转移**：一旦已输出 token，切换模型会造成内容重复
（前半段来自 A、后半段来自 B）。因此流式只在**建立流之前**于候选链内尝试，
一旦开始产出即锁定该模型。

---

## 五、4.0 兼容性约束

既有测试（`test_llm_service.py` / `test_litellm_integration.py`）锁定了三个历史语义，
重构**不得改变其返回值形态**：

| API | 返回形态 | 示例 |
|---|---|---|
| `LLMService._normalize_model()` | **带** provider 前缀 | `"gpt-4"` → `"openai/gpt-4"` |
| `LLMService.get_model_for_tier()` | **不带**前缀 | `STRONG` → `"gpt-4"` |
| `LLMService.get_preset_model()` | **带**前缀的 provider 预设 | `STRONG` → `"doubao/doubao-pro-128k"` |

`llm_service.py` 还需保留模块级 `settings` 符号——测试以
`app.services.llm_service.settings.OPENAI_MODEL_STRONG` 路径做 monkeypatch。

---

## 六、使用示例

```python
from app.services.llm_service import llm_service, ModelTier, LLMUsageStats, TaskType

# 1) 常规调用（沿用 4.0 写法，tier 会被映射到决策四矩阵）
stats = LLMUsageStats()
reply = await llm_service.chat(messages, tier=ModelTier.STRONG, stats=stats)
print(stats.model_used, stats.cost_usd)

# 2) 显式指定任务类型，绕开 tier 映射
reply = await llm_service.chat(messages, task_type=TaskType.SUMMARIZATION)

# 3) 企业私有 Key 一次性生效
from app.services.ai import ByokProfile
set_active_byok(ByokProfile(provider="zhipu", api_key=cipher))
reply = await llm_service.chat(messages)   # 无需重启

# 4) 路由链快照（无凭证），用于设置页展示与排障
for item in llm_service.describe_route(TaskType.REASONING):
    print(item["model"], item["usable"], item["reason"])
```

---

## 七、测试

```bash
cd backend
python -m pytest tests/test_llm_router.py -v          # 33 例：矩阵/优先级/BYOK/熔断
python -m pytest tests/test_llm_service.py tests/test_litellm_integration.py   # 4.0 兼容
python -m pytest tests/test_unit/test_llm_resilience.py                       # 重试与熔断
```

全部用例**不发起真实网络请求**，只验证路由与凭证决策；真实 HTTP 行为由
LiteLLM 集成测试覆盖。
