import logging
import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 不安全的默认密钥，用于检测是否未配置
_INSECURE_DEFAULT_KEY = "your-secret-key-change-in-production"

# AUD-14: 生产环境开启演示模式时必须显式写入的确认串。
DEMO_MODE_ACKNOWLEDGEMENT_VALUE = "I_UNDERSTAND_DEMO_RESULTS_ARE_NOT_DELIVERY"


class Settings(BaseSettings):
    PROJECT_NAME: str = "AutoTeams"
    PROJECT_VERSION: str = "4.0.0"
    PLATFORM_TITLE: str = "AutoTeams — 企业级 AI 数字员工平台"

    # P3-1: 显式声明 USE_SQLITE，避免被 pydantic_settings 当作 extra 字段拒绝。
    # database.py 会根据该环境变量决定使用 SQLite 还是 DATABASE_URL。
    USE_SQLITE: str = ""
    DATABASE_URL: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"
    # 独立安全状态库：不得使用可能驱逐撤销/重放键的缓存实例。
    TOKEN_BLACKLIST_REDIS_URL: str = ""

    # P1/P2-INFRA: 数据库连接池调优（仅 PostgreSQL 等外置 DB 生效；SQLite 使用 NullPool）
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    # 连接回收时间（秒），防止长时间空闲连接被中间件/防火墙断开
    DB_POOL_RECYCLE: int = 1800
    # 启用 pool_pre_ping，在取出连接前发送 SELECT 1 检测连接是否有效
    DB_POOL_PRE_PING: bool = True

    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8000
    # P0-06: ChromaDB 认证 token，为空表示不启用认证（仅开发环境）
    CHROMA_AUTH_TOKEN: str = ""
    # 部署简化: CHROMA_HOST 为空时使用嵌入式模式（PersistentClient），无需独立 ChromaDB 服务
    CHROMA_PERSIST_DIR: str = os.path.abspath("./data/chroma")
    JWT_SECRET_KEY: str = _INSECURE_DEFAULT_KEY
    JWT_ALGORITHM: str = "HS256"
    # 模型 API 密钥等敏感数据的静态加密密钥（Fernet）。
    # 留空时回退到 JWT_SECRET_KEY（向后兼容）；生产环境建议设置独立的强随机密钥，
    # 避免 JWT 轮换导致已加密数据无法解密。生成：python -c "import secrets; print(secrets.token_urlsafe(48))"
    ENCRYPTION_KEY: str = ""
    # 3.1.2: 审计链 HMAC 签名密钥（独立于 JWT_SECRET_KEY）
    # - 留空时回退到 JWT_SECRET_KEY（向后兼容；启动时记录 warning）
    # - 生产环境建议设置独立的强随机密钥，避免 JWT 轮换导致审计链校验失败
    #   生成方式：python -c "import secrets; print(secrets.token_urlsafe(32))"
    AUDIT_SIGNING_KEY: str = ""
    # 外部 Agent REST API 密钥的 HMAC 签名密钥。生产环境应独立设置；为空时
    # 仅为兼容旧部署回退 AUDIT_SIGNING_KEY/JWT_SECRET_KEY，并在使用处记录告警。
    AGENT_API_KEY_HMAC_SECRET: str = ""
    AGENT_API_KEY_PREFIX: str = "at_sk_"
    AGENT_API_RATE_LIMIT_PER_MINUTE: int = 60
    # P0-S3: access token 默认 1 小时，降低被盗用后的有效窗口

    JWT_EXPIRATION_HOURS: int = 1
    # P2-3: Refresh token 配置
    # access token 短期有效（默认 1h）
    # refresh token 长期有效（默认 7 天）
    JWT_REFRESH_EXPIRATION_DAYS: int = 7
    # P2-09: JWT iss/aud 声明（可选，用于防止 token 跨系统重放）
    # JWT_ISSUER: 签发方标识（如 "autoteams-backend"）
    # JWT_AUDIENCE: 接收方标识（如 "autoteams-frontend"）
    # 为空时不添加/验证这些声明（向后兼容）
    JWT_ISSUER: str = ""
    JWT_AUDIENCE: str = ""

    # P1-1: Cookie 安全配置（HttpOnly + SameSite + Secure）
    # DEBUG=true 时默认关闭 Secure，方便本地 HTTP 开发；生产环境必须设为 true
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "Lax"
    COOKIE_DOMAIN: Optional[str] = None
    COOKIE_ACCESS_TOKEN_NAME: str = "access_token"
    COOKIE_REFRESH_TOKEN_NAME: str = "refresh_token"

    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4"
    # P0-S2: 默认 LLM provider 前缀；无 provider 前缀的模型名会自动拼接为 <provider>/<model>
    # 常用值：openai, doubao, qwen, zhipu, deepseek, moonshot, anthropic, azure 等
    DEFAULT_LLM_PROVIDER: str = "openai"
    # P1-RAG: Embedding 模型；默认使用中文效果更好的 BAAI/bge-large-zh-v1.5
    # 留空则使用 ChromaDB 默认的 all-MiniLM-L6-v2（英文模型）
    EMBEDDING_MODEL: str = "BAAI/bge-large-zh-v1.5"
    # P1-RAG: 远程 embedding API 配置（如 OpenRouter 的 nvidia/llama-nemotron-embed-vl-1b-v2:free）
    # 当 EMBEDDING_API_KEY 非空时，vector_store 优先调用远程 /embeddings 接口，无需本地下载模型；
    # 留空则回退到本地 sentence-transformers 模型。
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_BASE: str = "https://openrouter.ai/api/v1"
    # P1-RAG: 远程 embedding API 多 Key 轮换。
    # 免费 API 常有每日/每分钟调用限制（如单 key 每天 50 次、每分钟 20 次），
    # 配置 EMBEDDING_API_KEY_1/2/3 后，vector_store 会按序尝试多把 key，
    # 单把 key 失败或限流（429）时自动切换到下一把，提高可用性。
    EMBEDDING_API_KEY_1: str = ""
    EMBEDDING_API_KEY_2: str = ""
    EMBEDDING_API_KEY_3: str = ""
    # P1-RAG: 是否启用 LLM 重排序；复杂查询启用可提升相关性，简单查询或小结果集可关闭以降低成本
    RERANK_ENABLED: bool = True
    # P1-RAG: 远程 reranker API 配置（如 OpenRouter 的 nvidia/llama-nemotron-rerank-vl-1b-v2:free）。
    # 云服务器不内置本地 bge-reranker 模型（构建时 SKIP_MODEL_DOWNLOAD=true），
    # 配置 RERANKER_MODEL + RERANKER_API_KEY 后，reranker_service 将优先调用远程 /rerank
    # 接口完成重排序，避免消耗 LLM token；同样支持 _1/_2/_3 多 Key 轮换。
    RERANKER_MODEL: str = ""
    RERANKER_API_BASE: str = "https://openrouter.ai/api/v1"
    RERANKER_API_KEY: str = ""
    RERANKER_API_KEY_1: str = ""
    RERANKER_API_KEY_2: str = ""
    RERANKER_API_KEY_3: str = ""
    # P1-3 LiteLLM 多模型路由配置
    # 强模型：用于复杂分析、Agentic RAG 推理（默认与 OPENAI_MODEL 相同）
    OPENAI_MODEL_STRONG: str = "gpt-4"
    # 廉价模型：用于简单 FAQ、查询分类、Self-RAG 评估（默认 gpt-3.5-turbo）
    OPENAI_MODEL_CHEAP: str = "gpt-3.5-turbo"
    # DeepSeek 配置：DEFAULT_LLM_PROVIDER=deepseek 时作为主模型凭证/模型；
    # 也可作为降级备选 provider（llm_service 按模型 provider 前缀分别取凭证）。
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_MODEL_STRONG: str = "deepseek-chat"
    DEEPSEEK_MODEL_CHEAP: str = "deepseek-chat"
    # AutoTeams 5.0 决策四：多模型矩阵。DeepSeek 为主力，Kimi / GLM-4 为按场景备选。
    # Kimi (Moonshot)：超长上下文与精细文档研读，文档总结/摘要场景首选备选。
    MOONSHOT_API_KEY: str = ""
    MOONSHOT_API_BASE: str = "https://api.moonshot.cn/v1"
    MOONSHOT_MODEL: str = "moonshot-v1-128k"
    # GLM-4 (智谱 AI)：复杂业务逻辑与 Function Call，复杂推理/分析场景首选备选。
    ZHIPU_API_KEY: str = ""
    ZHIPU_API_BASE: str = "https://open.bigmodel.cn/api/paas/v4"
    ZHIPU_MODEL: str = "glm-4-plus"
    # 故障转移备用模型列表（逗号分隔，litellm 格式如 "openai/gpt-4,openai/gpt-3.5-turbo"）
    # 主模型不可用时按顺序尝试；各模型凭证按其 provider 前缀（deepseek/openai）分别解析
    LLM_FALLBACK_MODELS: str = ""
    # LLM 请求超时（秒）
    LLM_REQUEST_TIMEOUT: int = 120
        # LLM 最大重试次数（主模型失败后尝试备用模型的次数）
    LLM_MAX_RETRIES: int = 2
    # 单个模型的瞬态错误重试次数；与备用模型故障转移分别计数，避免无限放大调用。
    LLM_REQUEST_RETRY_ATTEMPTS: int = 2
    LLM_RETRY_INITIAL_BACKOFF_SECONDS: float = 0.5
    LLM_RETRY_MAX_BACKOFF_SECONDS: float = 4.0
    # 进程级熔断：连续失败达到阈值后，在冷却期内快速失败并尝试备用模型。
    LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 30

    # P1-5: 是否允许 LLM 演示模式（无 API Key 时返回 fallback 响应）
    # 生产环境默认禁用（false），必须配置真实 LLM API Key；开发环境可显式开启
    LLM_ALLOW_FALLBACK: bool = False
    # 对话上下文最大 token 预算（含 system + history + context + query）
    # P2-3: 使用 tiktoken 精确估算；默认按 GPT-4 8K 上下文预留 2K 回复空间
    MAX_CONTEXT_TOKENS: int = 6000
    MULTIMODAL_API_KEY: str = ""
    MULTIMODAL_API_BASE: str = "https://api.openai.com/v1"
    MULTIMODAL_MODEL: str = "gpt-4-vision-preview"
    HOST: str = "0.0.0.0"  # noqa: S104
    PORT: int = 8000
    DEBUG: bool = False

    # AUD-14: 演示模式总开关。默认关闭。任何在演示模式下产生的回执都必须显式
    # 标注为 DEMO/模拟，绝不允许混入"已完成"的正式响应。
    DEMO_MODE_ENABLED: bool = False
    # 生产环境（DEBUG=false）开启演示模式时，必须同时提供下面这个确认串，
    # 否则拒绝启动 —— 避免演示数据被误当成真实履约。
    DEMO_MODE_ACKNOWLEDGEMENT: str = ""

    # BE-SEC-06: CORS 允许来源列表（逗号分隔）。
    # 生产环境禁止设置为 *，必须与 allow_credentials=True 同时使用显式域名列表。
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000"

    # BE-SEC-07: 受信反向代理跳数，用于从 X-Forwarded-For 链中解析真实客户端 IP。
    # 0 = 不信任任何代理（默认，XFF 视为可伪造，将使用 request.client.host）；
    # 部署在 nginx / Railway / Cloudflare 等反向代理后应设置为对应跳数（通常为 1，
    # 多层代理时取实际层数）。设置后会取 XFF 链中倒数第 (TRUSTED_PROXY_COUNT+1) 位
    # 作为客户端 IP，避免旧实现取最右侧（=最近一跳代理 IP）导致 IP 检测失效。
    TRUSTED_PROXY_COUNT: int = 0

    # FE-SEC-03: 前端应用公网地址，用于后端生成完整邀请链接。
    # 若为空，则后端返回相对链接，由前端基于当前 origin 拼接（需确保 origin 可信）。
    FRONTEND_URL: str = ""

    # P0-04: 文件上传根目录。所有用户提供的路径必须在此目录下。
    UPLOAD_ROOT: str = os.path.abspath("./uploads")

    # 示例企业数据目录（编译端点 folder_path 为空时的默认数据源）
    SAMPLE_DATA_DIR: str = os.path.abspath("./sample_data")

    # P0-07: 各类文件大小上限（字节）
    MAX_FILE_SIZE_DOCUMENT: int = 50 * 1024 * 1024  # 50MB
    MAX_FILE_SIZE_IMAGE: int = 20 * 1024 * 1024     # 20MB
    MAX_FILE_SIZE_VIDEO: int = 500 * 1024 * 1024   # 500MB
    MAX_FILE_SIZE_AUDIO: int = 200 * 1024 * 1024   # 200MB
    # chunk_text 单次最大输入字符数
    MAX_CHUNK_INPUT_CHARS: int = 5_000_000

    # P0-14: LangGraph checkpoint 持久化路径（SqliteSaver）
    # 生产环境（Docker/Railway）中应覆盖为 /app/data/langgraph_checkpoints.sqlite，
    # 与 docker-compose 的 langgraph_data 卷挂载保持一致，确保 read_only 根文件系统仍可写。
    LANGGRAPH_CHECKPOINT_PATH: str = os.path.abspath("./langgraph_checkpoints.sqlite")

    # P0-11: 速率限制默认值（每分钟）
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10
    RATE_LIMIT_CHAT_PER_MINUTE: int = 30
    RATE_LIMIT_UPLOAD_PER_MINUTE: int = 20
    RATE_LIMIT_SCAN_PER_MINUTE: int = 5
    # P1-01: 通用业务端点与管理操作限流
    RATE_LIMIT_API_PER_MINUTE: int = 60
    RATE_LIMIT_ADMIN_PER_MINUTE: int = 20
    # P1-02-D: SSE 流式聊天限流（比普通 chat 更严格，每连接占用时间长）
    RATE_LIMIT_SSE_PER_MINUTE: int = 10
    # BE-SEC-05: /health 低频限流（防止慢速探测占用连接）
    RATE_LIMIT_HEALTH_PER_MINUTE: int = 60
    # BE-SEC-05: /metrics 限流（不影响 Prometheus 15-60s 正常抓取）
    RATE_LIMIT_METRICS_PER_MINUTE: int = 60
    # 5.3.1: 生产环境是否强制要求 Redis 作为限流存储后端
    # - true（生产默认）：Redis 不可用时拒绝启动，避免多实例各自内存计数被绕过
    # - false（开发/测试）：Redis 不可用时回退到内存存储（单实例）
    RATE_LIMIT_REQUIRE_REDIS: bool = False

    # M9: 账户锁定策略（C4 依赖项，由 C3 在 config.py 统一添加）
    # 连续登录失败达到该阈值后锁定账户
    ACCOUNT_LOCKOUT_THRESHOLD: int = 5
    # 账户锁定持续时间（分钟）
    ACCOUNT_LOCKOUT_DURATION_MINUTES: int = 15

    # BE-SEC-08: 公开注册开关
    # BE-SEC-08 / P1-4: 生产环境默认强制关闭公开注册，防止任意互联网用户建企提权
    # - false（生产/默认安全基线）：关闭公开注册，用户必须通过已有企业邀请链接注册
    # - true（仅开发/测试显式开启）：允许 /auth/register 注册
    REGISTRATION_ENABLED: bool = False
    # T21: ClamAV 病毒扫描服务连接配置（C6 依赖项，由 C3 在 config.py 统一添加）
    # 为空表示不启用 ClamAV 扫描（仅开发环境）；生产环境应配置 ClamAV 守护进程地址
    CLAMD_HOST: str = ""
    CLAMD_PORT: int = 3310

    # P1-06: 结构化日志输出目录（生产环境挂载为持久化卷）
    LOG_DIR: str = "logs"

    # P1-06-E: /metrics 端点访问控制
    # 为空表示不启用认证（仅开发环境或通过反向代理限制访问时使用）
    # 生产环境建议设置一个随机 token，Prometheus 抓取时带 ?token=xxx 参数
    METRICS_AUTH_TOKEN: str = ""

    # P3-2: Sentry DSN（为空表示禁用 Sentry 错误上报）
    # 在 Sentry 项目设置中获取，格式：https://xxx@o0.ingest.sentry.io/0
    SENTRY_DSN: str = ""
    # Sentry 环境标识（如 production / staging / development）
    SENTRY_ENVIRONMENT: str = "development"
    # Sentry 采样率：1.0 表示全部事务追踪，0.0 表示仅上报错误
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

        # 耐久编译 Worker：队列轮询与数据库租约配置。
    COMPILATION_WORKER_POLL_SECONDS: float = 2.0
    COMPILATION_WORKER_LEASE_SECONDS: int = 90

    # O-07: Loop 闭环 APScheduler 自动优化定时任务开关

    # 测试环境应在 conftest 中通过环境变量设为 false，避免调度器干扰测试
    LOOP_SCHEDULER_ENABLED: bool = True
    # 自动检索优化执行时刻（CronTrigger hour/minute，UTC）
    LOOP_AUTO_OPTIMIZE_HOUR: int = 3
    LOOP_AUTO_OPTIMIZE_MINUTE: int = 0
    # 知识缺口扫描执行时刻（CronTrigger hour/minute，UTC）
    LOOP_AUTO_SCAN_GAPS_HOUR: int = 4
    LOOP_AUTO_SCAN_GAPS_MINUTE: int = 0
    # 多实例 Scheduler 的分布式 leader lock；PostgreSQL 使用 advisory lock，
    # SQLite 开发模式保持单实例语义。
    SCHEDULER_LEADER_LOCK_ENABLED: bool = True

    # Evolution 层周期任务配置。此前通过 getattr 读取，因 Settings.extra=ignore
    # 导致部署环境设置无法生效；现显式声明并可由环境变量可靠覆盖。
    EVOLUTION_SCHEDULER_ENABLED: bool = True
    EVOLUTION_OPTIMIZE_HOUR: int = 5
    EVOLUTION_OPTIMIZE_MINUTE: int = 0
    EVOLUTION_METRICS_HOUR: int = 6
    EVOLUTION_METRICS_MINUTE: int = 0
    EVOLUTION_ADVISOR_HOUR: int = 7
    EVOLUTION_ADVISOR_MINUTE: int = 0

    # ============================================================
    # 影子模式转正政策（AUD-30）
    # ============================================================
    # 门槛由服务器决定并版本化，调用方不得自带阈值；详见
    # app/services/evolution/graduation_policy.py。有效样本数下限至少为 1，
    # 通过率阈值下限 0.01，避免"零样本自动达标"。
    SHADOW_GRADUATION_MIN_SAMPLES: int = 5
    SHADOW_GRADUATION_PASS_RATE: float = 0.85


    # AUD-08: 渠道回调验签与模拟入站是两个"只应存在于本地"的逃逸开关。
    # 生产环境（DEBUG=false）启用任一开关都会让伪造的外部消息直接进入
    # 身份绑定与业务调度，因此 config.py 在生产启动时直接拒绝。
    DEV_ALLOW_UNSIGNED_CHANNEL_WEBHOOK: bool = False
    DEV_ALLOW_SIMULATE_CHANNEL_INBOUND: bool = False

    # AUD-19: RLS 运行账户。Compose 目前用 POSTGRES_USER（官方镜像创建的超级用户）
    # 同时做迁移账户与运行账户，超级用户会绕过 RLS，策略形同虚设。
    # 生产 PostgreSQL 连接由 database.py 核验实际角色、权限与所有权；
    # 未配置或连接不符合最小权限要求时拒绝连接。迁移使用独立管理员引擎。
    DATABASE_APP_ROLE: str = ""

    # AUD-19：队列 Worker 的独立连接。跨租户领取/恢复函数只授予 autoteams_worker，
    # API 运行角色调用会得到 42501，因此 Worker 必须用自己的连接串。
    # 未配置时 Worker 进程在生产环境拒绝启动（见 database.ensure_worker_database_ready）。
    DATABASE_WORKER_URL: str = ""
    DATABASE_WORKER_ROLE: str = ""
    # 渠道回调引导连接：该角色没有任何表权限，只能取指定账号的验签材料。
    # 验签通过后由业务会话用账号密钥摘要绑定租户。
    DATABASE_BOOTSTRAP_URL: str = ""
    DATABASE_BOOTSTRAP_ROLE: str = ""

    # ============================================================
    # 协作工作台与本地工具桥接（Local Runner）
    # ============================================================
    # 本地守护进程（Local Runner）外连的协作服务地址（用于 Backend 与 Runner 之间
    # 通过 collaboration-service 中转的 REST 调用）。为空时默认使用本机协作服务。
    # 云端部署时（compose 内）应设为协作服务容器地址，如 http://collaboration-service:3001。
    COLLAB_SERVICE_URL: str = "http://127.0.0.1:3001"
    # 本地 Runner 公网桥接地址（仅用于生成给用户的 setup_command 中的 /bridge WebSocket 地址）。
    # 云端部署时为公网域名根（如 https://your-domain.com，nginx 需把 /bridge 反代到协作服务）；
    # 为空时回退到 COLLAB_SERVICE_URL（本地开发直连场景）。
    RUNNER_PUBLIC_BRIDGE_URL: str = ""
    # Backend 与 collaboration-service 之间的共享内部密钥（service-to-service 鉴权）。
    # 协作服务调用 Backend 内部端点（claim/connected/offline）时须携带
    # X-Bridge-Secret 头与之匹配。为空时（仅开发）跳过校验并记录 warning。
    BRIDGE_INTERNAL_SECRET: str = ""
    # P1-3: 一次性 setup token 配对码有效期（分钟），默认 10 分钟一次性失效
    RUNNER_SETUP_TOKEN_TTL_MINUTES: int = 10
    RUNNER_TOKEN_TTL_DAYS: int = 30  # 废弃保留，兼容历史调用
    # Runner 心跳超时（秒）：超过该时长未收到心跳视为离线
    RUNNER_HEARTBEAT_TIMEOUT: int = 45
    # 本地路径最深允许的目录层级（防异常嵌套）
    LOCAL_PATH_MAX_DEPTH: int = 8
    # 本地授权有效期（天），到期后自动置为 revoked
    LOCAL_PATH_EXPIRY_DAYS: int = 30
    # 单用户最多可同时授权的本地路径数
    LOCAL_PATH_MAX_PER_USER: int = 20

    # Pydantic v2: 使用 SettingsConfigDict 替代遗留 class Config（消除弃用警告）。
    # 统一使用项目根目录的 .env（单文件配置），避免各服务各自维护 .env。
    # 基于 __file__ 计算绝对路径，与启动时的工作目录无关。
    model_config = SettingsConfigDict(
        env_file=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            ".env",
        ),
        case_sensitive=True,
        # 共享 .env 中包含前端/协作服务等变量（VITE_*/AGNES_*/BACKEND_PORT 等），
        # 后端忽略这些未声明字段，避免 extra_forbidden 启动失败。
        extra="ignore",
    )


settings = Settings()

# D7 + P0-09: JWT 安全检查
# - DEBUG=true（开发）时仅警告，允许使用默认密钥
# - DEBUG=false（生产）时使用默认密钥直接拒绝启动
if settings.JWT_SECRET_KEY == _INSECURE_DEFAULT_KEY:
    if settings.DEBUG:
        logger.warning(
            "安全警告：JWT_SECRET_KEY 使用了不安全的默认值（DEBUG 模式允许）。"
            "生产环境请在 .env 中设置一个至少 32 字符的随机密钥。"
            "可用 python -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成。"
        )
    else:
        raise RuntimeError(
            "拒绝启动：JWT_SECRET_KEY 使用了不安全的默认值，且非 DEBUG 模式。"
            "请在 .env 中设置一个至少 32 字符的随机密钥。"
            "可用 python -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成。"
        )

# BE-SEC-01: 生产环境必须启用 Secure Cookie，防止 Cookie 在明文 HTTP 上传输被窃取
if not settings.DEBUG and not settings.COOKIE_SECURE:
    raise RuntimeError(
        "拒绝启动：生产环境（DEBUG=false）必须设置 COOKIE_SECURE=true，"
        "确保 HttpOnly Cookie 仅通过 HTTPS 传输。"
    )

# BE-SEC-06: 生产环境禁止 CORS 通配符 *，防止与 allow_credentials=True 组合引发 CSRF 风险
if not settings.DEBUG:
    cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
    if "*" in cors_origins or not cors_origins:
        raise RuntimeError(
            "拒绝启动：生产环境（DEBUG=false）的 CORS_ALLOWED_ORIGINS 不能为 * 或空，"
            "必须设置为前端域名的显式列表（如 https://app.example.com）。"
        )

# BE-SEC-08 / P1-4: 生产环境强制阻断无保护的公开注册
if not settings.DEBUG and settings.REGISTRATION_ENABLED:
    if not os.getenv("ALLOW_INSECURE_REGISTRATION"):
        raise RuntimeError(
            "安全致命错误 (P1-4)：生产环境严禁开启无防护的公开注册 (REGISTRATION_ENABLED=True)！"
            "新用户必须通过租户邀请链加入。若临时强制测试，请显式配置 ALLOW_INSECURE_REGISTRATION=1。"
        )
# T15: 生产环境拒绝使用 SQLite 启动
# SQLite 不支持并发写入、无行级安全、无连接池管理，不适合多实例生产部署
# 且与限流 Redis 存储策略不匹配（单文件 DB + 内存限流可被多实例绕过）
_use_sqlite_env_t15 = os.getenv("USE_SQLITE", "").strip().lower()
if not settings.DEBUG and (_use_sqlite_env_t15 == "true" or "sqlite" in settings.DATABASE_URL.lower()):
    raise RuntimeError(
        "拒绝启动：生产环境（DEBUG=false）不允许使用 SQLite 数据库。"
        "SQLite 不支持并发写入与多实例部署，请配置 PostgreSQL DATABASE_URL。"
        "开发环境请设置 DEBUG=true。"
    )
if not settings.DEBUG and not os.getenv("DATABASE_URL", "").strip() and not settings.DATABASE_URL.strip():
    raise RuntimeError(
        "拒绝启动：生产环境必须显式配置 DATABASE_URL，不能回退到本地 SQLite。"
    )

# 本地工具桥接（Local Runner）：生产环境必须配置内部桥接密钥
# Backend 与 collaboration-service 之间的服务级鉴权（X-Bridge-Secret）。
# 未配置时如果仍让内部端点放行，任何人都可驱动本地守护进程执行文件操作，属高危越权。
if not settings.DEBUG and not settings.BRIDGE_INTERNAL_SECRET:
    raise RuntimeError(
        "拒绝启动：生产环境（DEBUG=false）必须配置 BRIDGE_INTERNAL_SECRET，"
        "用于 Backend 与 collaboration-service 之间的服务级鉴权。"
        "可用 python -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成，"
        "并在部署环境变量中与 collaboration-service 同时配置相同的值。"
    )

# P2-6: /metrics 端点与 JWT/CORS 同等 fail-closed —— 生产环境未配置 METRICS_AUTH_TOKEN
# 时拒绝启动，防止 Prometheus 指标（路由、QPS、错误率等侦察信息）匿名泄露。
# 本地开发（DEBUG=true）允许匿名访问以便调试。
if not settings.DEBUG and not settings.METRICS_AUTH_TOKEN:
    raise RuntimeError(
        "拒绝启动：生产环境（DEBUG=false）必须配置 METRICS_AUTH_TOKEN，"
        "保护 /metrics 端点免受匿名抓取。"
        "可用 python -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成，"
        "Prometheus 抓取时携带 ?token=<值> 或 Authorization: Bearer <值>。"
    )

# AUD-14: 演示模式在生产环境必须显式确认，否则拒绝启动。
# 模拟执行一旦混入正式响应，业务会把未发生的外部操作当成完成（AUD-14）。
if not settings.DEBUG and settings.DEMO_MODE_ENABLED and (
    settings.DEMO_MODE_ACKNOWLEDGEMENT.strip() != DEMO_MODE_ACKNOWLEDGEMENT_VALUE
):
    raise RuntimeError(
        "拒绝启动：生产环境（DEBUG=false）开启 DEMO_MODE_ENABLED 时必须显式配置 "
        f"DEMO_MODE_ACKNOWLEDGEMENT={DEMO_MODE_ACKNOWLEDGEMENT_VALUE}，"
        "以确认模拟结果不会被当成真实履约。生产环境原则上应保持 DEMO_MODE_ENABLED=false。"
    )

# AUD-08: 渠道回调验签与模拟入站的开发逃逸开关在生产必须关闭。
# 打开任一开关都意味着"任意互联网用户可伪造企微/飞书消息进入身份绑定与调度"。
if not settings.DEBUG:
    _dev_channel_switches = [
        ("DEV_ALLOW_UNSIGNED_CHANNEL_WEBHOOK", settings.DEV_ALLOW_UNSIGNED_CHANNEL_WEBHOOK),
        ("DEV_ALLOW_SIMULATE_CHANNEL_INBOUND", settings.DEV_ALLOW_SIMULATE_CHANNEL_INBOUND),
    ]
    for _name, _enabled in _dev_channel_switches:
        if _enabled or str(os.getenv(_name, "")).strip().lower() in ("1", "true", "yes"):
            raise RuntimeError(
                f"拒绝启动：生产环境（DEBUG=false）禁止开启 {_name}。"
                "该开关只用于本地联调；打开后任何人都能伪造渠道消息并完成身份绑定。"
            )

# AUD-19: database.py 在生产 PostgreSQL 每次建立物理连接时执行角色校验。
# 配置阶段只提示部署问题；这里不连接数据库，也不阻止独立的 Alembic 管理员引擎。
if not settings.DEBUG and not settings.DATABASE_APP_ROLE.strip():
    logger.critical(
        "DATABASE_APP_ROLE 未配置：生产 PostgreSQL 应用连接将被拒绝。"
        "请使用独立的最小权限运行角色；迁移管理员不得用于 API/worker。"
    )

# AUD-19：Worker / 引导连接的角色必须与各自的 URL 成对出现。
# 配了连接串却没配角色名时，database.py 无从核验实际身份，只能拒绝启动，
# 不能退回到"用 API 角色跑队列"这种跨租户能力。
if not settings.DEBUG:
    for _url_field, _role_field, _label in (
        ("DATABASE_WORKER_URL", "DATABASE_WORKER_ROLE", "队列 Worker"),
        ("DATABASE_BOOTSTRAP_URL", "DATABASE_BOOTSTRAP_ROLE", "渠道引导"),
    ):
        _url = str(getattr(settings, _url_field, "") or "").strip()
        _role = str(getattr(settings, _role_field, "") or "").strip()
        if _url and not _role:
            raise RuntimeError(
                f"拒绝启动：配置了 {_url_field} 却缺少 {_role_field}。"
                f"{_label}连接必须声明预期角色，否则无法核验最小权限。"
            )
        if _role and not _url:
            raise RuntimeError(
                f"拒绝启动：配置了 {_role_field} 却缺少 {_url_field}。"
                f"{_label}角色不能挂在 API 连接上使用。"
            )
