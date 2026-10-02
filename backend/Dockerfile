FROM python:3.11-slim

WORKDIR /app

# P0-10-A: 系统依赖（清理 apt 缓存减小镜像体积）
# P3-4: 新增 gpg（备份加密）和 sqlite3（SQLite 备份一致性）
# DEP-SYS-01: 基础镜像 python:3.11-slim 基于 Debian bookworm(12)。
#   注：bookworm 点版本更新后，libmagic1 已改名为 libmagic1t64，且各包版本号会随
#   上游升级变化，故此处不再锁定具体版本号，直接安装 bookworm 主仓库当前稳定版
#   （避免因上游点版本升级导致版本号失配而构建失败；如需锁定请按
#   `apt-cache policy <pkg>` 查询后填入）。升级前需在 CI 验证音视频处理与备份链路。
# 可选 APT_MIRROR 构建参数：国内网络构建时传国内 Debian 镜像可加速/提高可靠性，
#   例如 --build-arg APT_MIRROR=http://mirrors.aliyun.com/debian；默认空=官方源（CI 用）。
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org/debian|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
      sed -i "s|http://security.debian.org/debian-security|${APT_MIRROR}-security|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    libmagic1t64 \
    ffmpeg \
    curl \
    gpg \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# P0-10-A: 先复制依赖清单利用 Docker 缓存层
# 可选 PIP_INDEX_URL 构建参数：国内网络构建时传国内 PyPI 镜像可加速/提高可靠性，
#   例如 --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/；默认空=官方 PyPI（CI 用）。
ARG PIP_INDEX_URL=
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
# 默认安装由 uv 生成、纳入版本控制且附带分发包哈希的锁文件。
# 如需精简镜像，必须提供同样带哈希的 requirements-slim.lock，不能绕过锁定策略。
ARG REQUIREMENTS_FILE=requirements.lock
COPY ${REQUIREMENTS_FILE} requirements.lock
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --require-hashes -r requirements.lock

# D2-S11: 复制并预下载 bge-reranker 模型到镜像，避免生产环境运行时下载超时
# 该步骤在 requirements 安装之后、应用代码复制之前执行，充分利用 Docker 缓存层
# 部署时可传 --build-arg SKIP_MODEL_DOWNLOAD=true 跳过模型预下载：
#   当使用远程 embedding API（EMBEDDING_API_KEY）+ LLM 重排序时，无需在镜像内置约 2.4GB 模型，
#   可显著减小镜像体积与传输成本；默认 false 保持原有「内置模型」行为不变。
ARG SKIP_MODEL_DOWNLOAD=false
COPY scripts/download_bge_reranker.py ./scripts/download_bge_reranker.py
RUN if [ "$SKIP_MODEL_DOWNLOAD" != "true" ]; then \
      python scripts/download_bge_reranker.py; \
    else \
      echo "SKIP_MODEL_DOWNLOAD=true，跳过模型预下载（使用远程 embedding API + LLM 重排）"; \
    fi

# 复制应用代码（.dockerignore 会过滤掉 .env / __pycache__ / tests 等）
COPY . .

# 规范化 shell 脚本行尾：Windows 检出（core.autocrlf=true）会把 *.sh 转为 CRLF，
# 会破坏 bash 解析——例如 migrate_and_start.sh 的 `set -o pipefail` 因尾随 \r
# 报 "invalid option name" 导致容器不健康。此步与检出环境无关，统一转回 LF。
RUN find scripts -name '*.sh' -exec sed -i 's/\r$//' {} +

# P0-10-A: 创建非 root 用户并切换
# 使用固定的 UID/GID 1000（与大多数 Linux 发行版一致），避免与宿主机权限冲突
RUN groupadd -r -g 1000 autoteams \
    && useradd -r -u 1000 -g 1000 -d /app -s /sbin/nologin autoteams \
    && mkdir -p /app/uploads /app/data /app/models \
    && chown -R autoteams:autoteams /app

# P0-14: 默认 checkpoint 路径指向已挂载为持久化卷的 /app/data，
# 避免生产环境 read_only 根文件系统无法写入导致回退 MemorySaver.
ENV LANGGRAPH_CHECKPOINT_PATH=/app/data/langgraph_checkpoints.sqlite
# D2-S11: 运行时复用构建阶段预下载的 bge-reranker 模型缓存
ENV SENTENCE_TRANSFORMERS_HOME=/app/models

USER autoteams

EXPOSE 8000

# P0-10-A: 健康检查（uvicorn 起来后 /health 端点可用）
# curl 由 apt 安装阶段引入
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# P0-10-A: 生产环境禁用 reload，开启多 worker（受 CPU 限制约束）
# 使用 --workers 1 保持与现有 SSE/asyncio 单实例代码兼容；
# 若需水平扩展，需引入 Redis pubsub 同步 SSE 状态
# INF-DEP-02: 使用迁移包装脚本，失败时阻止容器进入不健康的服务状态
CMD ["bash", "scripts/migrate_and_start.sh"]
