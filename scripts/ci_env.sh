#!/usr/bin/env bash
# ============================================================
# 生成 CI 专用的完整 Compose env 集（AUD-21）
#
# 问题：`docker compose --env-file .env.ci ... config` 里的 --env-file
# 只提供 **插值** 来源，并不会替换服务级 `env_file:` 指向的文件。
# docker-compose.yml 的 backend/worker 服务读 `.env`，
# docker-compose.prod.yml 的服务读 `.env.prod`。
# 原 CI 只创建 `.env.ci`，因此同一条 `docker compose config` 仍然报
#   env file ...\.env not found
# 也就是说这条"配置检查"从来没有真正校验过任何一份可用配置。
#
# 做法：本脚本在指定目录生成四份 **完全隔离、只含占位值** 的 env 文件：
#   .env.ci          插值来源（--env-file 用这个）
#   .env             docker-compose.yml 的 env_file（CI 自建，绝非真实凭证）
#   .env.prod        docker-compose.prod.yml 的 env_file（不含数据库口令）
#   .env.prod.db     生产数据库插值来源（不会注入应用容器）
#
# 四份文件的内容都由本脚本生成，CI 工作区里不存在任何真实 .env/.env.prod，
# 因此校验结果与开发者本机配置无关，也不会泄露任何密钥。
#
# 用法：
#   scripts/ci_env.sh /path/to/checkout/dir
#   然后：
#     docker compose --env-file .env.ci -f docker-compose.yml config --quiet
#     docker compose --env-file .env.ci --env-file .env.prod.db -f docker-compose.prod.yml config --quiet
# ============================================================
set -euo pipefail

TARGET_DIR="${1:-.}"
if [ ! -d "${TARGET_DIR}" ]; then
  echo "错误：目标目录不存在: ${TARGET_DIR}" >&2
  exit 1
fi

if [ -e "${TARGET_DIR}/.env" ] || [ -e "${TARGET_DIR}/.env.prod" ] || [ -e "${TARGET_DIR}/.env.prod.db" ]; then
  echo "错误：${TARGET_DIR} 已存在 .env/.env.prod/.env.prod.db，CI 必须使用完全隔离的 env 集" >&2
  exit 1
fi

# --- 插值文件（--env-file）：只放插值必需的键 + 生产插值缺省值 -------------
cat > "${TARGET_DIR}/.env.ci" <<'EOF'
# 由 scripts/ci_env.sh 生成的 CI 插值文件，全部为占位值。
# 不要在此处放任何真实凭证：CI 只需要能让 compose 解析出完整配置。
POSTGRES_USER=autoteams
POSTGRES_PASSWORD=ci-admin-password-for-config-check-only-2026
POSTGRES_DB=autoteams_ci
REDIS_PASSWORD=ci-redis-password
BRIDGE_INTERNAL_SECRET=ci-bridge-secret
JWT_SECRET_KEY=ci-jwt-secret-key-at-least-32-characters
COOKIE_SECURE=true
CORS_ALLOWED_ORIGINS=https://ci.example.invalid
CHROMA_AUTH_TOKEN=ci-chroma-token
VITE_API_BASE_URL=https://ci.example.invalid
RUNNER_PUBLIC_BRIDGE_URL=https://ci.example.invalid/bridge
AGNES_API_KEY=ci-agnes-placeholder
FRONTEND_PORT=3000
BACKEND_PORT=8000
EOF

# --- docker-compose.yml 的服务级 env_file ---------------------------------
# compose 的 env_file 为服务注入容器环境变量；这里覆盖后端读取的关键配置。
cat > "${TARGET_DIR}/.env" <<'EOF'
# 由 scripts/ci_env.sh 生成的 CI 服务 env 文件（docker-compose.yml 的 env_file）。
# 全部为占位值；仅用于让 `docker compose config` 能解析出完整、可校验的配置。
POSTGRES_USER=autoteams
POSTGRES_PASSWORD=ci-admin-password-for-config-check-only-2026
POSTGRES_DB=autoteams_ci
REDIS_PASSWORD=ci-redis-password
BRIDGE_INTERNAL_SECRET=ci-bridge-secret
JWT_SECRET_KEY=ci-jwt-secret-key-at-least-32-characters
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7
COOKIE_SECURE=true
CORS_ALLOWED_ORIGINS=https://ci.example.invalid
DEBUG=false
USE_SQLITE=false
CHROMA_HOST=chromadb
CHROMA_PORT=8000
CHROMA_AUTH_TOKEN=ci-chroma-token
COLLAB_SERVICE_URL=http://collaboration-service:3001
RUNNER_PUBLIC_BRIDGE_URL=https://ci.example.invalid/bridge
LANGGRAPH_CHECKPOINT_PATH=/app/data/langgraph_checkpoints.sqlite
VITE_API_BASE_URL=https://ci.example.invalid
AGNES_API_KEY=ci-agnes-placeholder
OPENAI_API_KEY=ci-openai-placeholder
EOF

# --- docker-compose.prod.yml 的服务级 env_file ----------------------------
cat > "${TARGET_DIR}/.env.prod" <<'EOF'
# 由 scripts/ci_env.sh 生成的 CI 生产 env 文件（docker-compose.prod.yml 的 env_file）。
# 全部为占位值；仅用于让 `docker compose config` 能解析出完整、可校验的配置。
REDIS_PASSWORD=ci-redis-password
BRIDGE_INTERNAL_SECRET=ci-bridge-secret
JWT_SECRET_KEY=ci-jwt-secret-key-at-least-32-characters
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7
COOKIE_SECURE=true
CORS_ALLOWED_ORIGINS=https://ci.example.invalid
DEBUG=false
USE_SQLITE=false
SEED_DEMO=0
CHROMA_HOST=chromadb
CHROMA_PORT=8000
CHROMA_AUTH_TOKEN=ci-chroma-token
COLLAB_SERVICE_URL=http://collaboration-service:3001
RUNNER_PUBLIC_BRIDGE_URL=https://ci.example.invalid/bridge
LANGGRAPH_CHECKPOINT_PATH=/app/data/langgraph_checkpoints.sqlite
VITE_API_BASE_URL=/
AGNES_API_KEY=ci-agnes-placeholder
OPENAI_API_KEY=ci-openai-placeholder
BACKUP_ENCRYPTION_KEY=
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_DAYS=30
WORKER_HEARTBEAT_DIR=/app/data
EOF

# 数据库口令仅用于配置验证；实际生产必须替换为独立随机值。
cat > "${TARGET_DIR}/.env.prod.db" <<'EOF'
POSTGRES_USER=autoteams
POSTGRES_DB=autoteams_ci
POSTGRES_PASSWORD=ci-admin-password-for-config-check-only-2026
APP_DB_PASSWORD=ci-app-password-for-config-check-only-2026
WORKER_DB_PASSWORD=ci-worker-password-for-config-check-only-2026
BOOTSTRAP_DB_PASSWORD=ci-bootstrap-password-for-config-check-only-2026
EOF

echo "已生成隔离 CI env 集："
for f in .env.ci .env .env.prod .env.prod.db; do
  printf '  %s (%s 行)\n' "${TARGET_DIR}/${f}" "$(wc -l < "${TARGET_DIR}/${f}" | tr -d ' ')"
done
