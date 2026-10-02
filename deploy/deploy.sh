#!/usr/bin/env bash
# ============================================================
# AutoTeams 一键部署脚本（服务器端，Linux/Ubuntu 推荐）
#
# 用法：
#   bash deploy/deploy.sh
#
# 脚本会自动完成：
#   1. 检查 Docker / Compose 是否安装（缺失自动安装）
#   2. 拉取代码（或复用当前目录）
#   3. 生成/.env.prod（从 .env.prod.example 复制，自动生成强密码）
#   4. 交互式引导填写域名 / API Key / 镜像地址
#   5. 拉取预构建镜像并 docker compose 启动全部服务
#   6. 生成宿主机 nginx 反代配置（HTTPS + 域名 → 前端容器）
#   7. 健康检查
#
# 前置条件（你只需准备这些）：
#   - 一台可公网访问的服务器（已装/可自动装 Docker）
#   - 一个域名（DNS A 记录已解析到服务器 IP）
#   - 至少一个 LLM API Key（推荐 DeepSeek）
#   - AgnesAI API Key（协作服务 pi.dev 用）
#   - GitHub 用户名（拉取预构建镜像）
# ============================================================
set -euo pipefail

# ---------- 常量 ----------
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"
REPO_URL="https://github.com/guoyangzhen/AutoTeams.git"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_GH_OWNER="guoyangzhen"

# ---------- 颜色输出 ----------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { printf "${CYAN}[INFO]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[ OK ]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
die()   { printf "${RED}[ERROR]${NC} %s\n" "$*"; exit 1; }

# ---------- 询问输入（带默认值） ----------
ask() {
  local prompt="$1" default="$2" var
  if [ -n "$default" ]; then
    read -r -p "$(printf "${CYAN}?${NC} %s [%s]: " "$prompt" "$default")" var
    var="${var:-$default}"
  else
    read -r -p "$(printf "${CYAN}?${NC} %s: " "$prompt")" var
  fi
  printf '%s' "$var"
}

# 生成随机强密码（兼容 openssl 或 /dev/urandom）
gen_secret() {
  local len="${1:-48}"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$len"
  else
    head -c "$len" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

# ---------- 1. 检查 / 安装 Docker ----------
check_docker() {
  if command -v docker >/dev/null 2>&1; then
    ok "Docker 已安装: $(docker --version)"
  else
    warn "未检测到 Docker，正在自动安装（Ubuntu/Debian）..."
    apt-get update -y
    apt-get install -y ca-certificates curl
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker || true
    ok "Docker 安装完成"
  fi
  # 检查 compose 插件
  if ! docker compose version >/dev/null 2>&1; then
    die "docker compose 插件不可用，请手动安装（docker compose v2）"
  fi
  ok "docker compose: $(docker compose version)"
}

# ---------- 2. 进入项目目录 / 拉取代码 ----------
ensure_code() {
  cd "$APP_DIR"
  if [ -f "$COMPOSE_FILE" ]; then
    ok "已在 AutoTeams 项目目录: $APP_DIR"
  else
    warn "未找到项目，正在从 GitHub 克隆..."
    git clone "$REPO_URL" /tmp/autoteams-deploy || die "克隆失败，请检查网络或 REPO_URL"
    cd /tmp/autoteams-deploy
    APP_DIR="$(pwd)"
  fi
}

# ---------- 3. 生成 .env.prod ----------
gen_env() {
  cd "$APP_DIR"
  if [ -f "$ENV_FILE" ]; then
    if ask "检测到已有 .env.prod，是否复用并按提示更新关键项？" "y" | grep -qiE '^y|^yes'; then
      info "复用现有 .env.prod，跳过自动生成"
      return
    fi
    mv "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    warn "已将旧配置备份为 ${ENV_FILE}.bak.*"
  fi
  [ -f ".env.prod.example" ] || [ -f "$COMPOSE_FILE" ] || die "缺少 .env.prod.example"

  info "以下信息可后续在 .env.prod 中修改："
  local domain gh_owner deepseek_key agnes_key
  domain=$(ask "你的公网域名 (如 autoteams.example.com)" "")
  domain="${domain:-autoteams.example.com}"
  gh_owner=$(ask "GitHub 用户名（拉取镜像用）" "$DEFAULT_GH_OWNER")
  deepseek_key=$(ask "DeepSeek API Key (sk-...)" "")
  agnes_key=$(ask "AgnesAI API Key (sk-...)" "")

  # 从 example 复制为 .env.prod
  cp .env.prod.example "$ENV_FILE"
  local s="$(gen_secret 48)"
  local r="$(gen_secret 48)"
  local j="$(gen_secret 48)"
  local a="$(gen_secret 48)"
  local c="$(gen_secret 32)"
  local m="$(gen_secret 16)"
  local b="$(gen_secret 32)"
  local br="$(gen_secret 48)"

  # 替换必填项
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${s}|"      "$ENV_FILE"
  sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${r}|"            "$ENV_FILE"
  sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=${j}|"            "$ENV_FILE"
  sed -i "s|^AUDIT_SIGNING_KEY=.*|AUDIT_SIGNING_KEY=${a}|"      "$ENV_FILE"
  sed -i "s|^CHROMA_AUTH_TOKEN=.*|CHROMA_AUTH_TOKEN=${c}|"      "$ENV_FILE"
  sed -i "s|^METRICS_AUTH_TOKEN=.*|METRICS_AUTH_TOKEN=${m}|"    "$ENV_FILE"
  sed -i "s|^BACKUP_ENCRYPTION_KEY=.*|BACKUP_ENCRYPTION_KEY=${b}|" "$ENV_FILE"
  sed -i "s|^BRIDGE_INTERNAL_SECRET=.*|BRIDGE_INTERNAL_SECRET=${br}|" "$ENV_FILE"

  # 域名 / 镜像 / Key
  sed -i "s|^CORS_ALLOWED_ORIGINS=.*|CORS_ALLOWED_ORIGINS=https://${domain}|" "$ENV_FILE"
  sed -i "s|^FRONTEND_URL=.*|FRONTEND_URL=https://${domain}|"   "$ENV_FILE"
  sed -i "s|^RUNNER_PUBLIC_BRIDGE_URL=.*|RUNNER_PUBLIC_BRIDGE_URL=https://${domain}|" "$ENV_FILE"
  sed -i "s|^AUTOTEAMS_RUNNER_DOWNLOAD_BASE=.*|AUTOTEAMS_RUNNER_DOWNLOAD_BASE=https://${domain}/local-runner|" "$ENV_FILE"
  sed -i "s|^BACKEND_IMAGE=.*|BACKEND_IMAGE=ghcr.io/${gh_owner}/autoteams-backend:latest|" "$ENV_FILE"
  sed -i "s|^FRONTEND_IMAGE=.*|FRONTEND_IMAGE=ghcr.io/${gh_owner}/autoteams-frontend:latest|" "$ENV_FILE"
  sed -i "s|^COLLAB_IMAGE=.*|COLLAB_IMAGE=ghcr.io/${gh_owner}/autoteams-collab:latest|" "$ENV_FILE"

  if [ -n "$deepseek_key" ]; then
    sed -i "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=${deepseek_key}|" "$ENV_FILE"
  fi
  if [ -n "$agnes_key" ]; then
    sed -i "s|^AGNES_API_KEY=.*|AGNES_API_KEY=${agnes_key}|" "$ENV_FILE"
  fi

  # 记录域名供后续生成 nginx
  echo "$domain" > /tmp/autoteams_domain.txt

  ok ".env.prod 已生成（敏感值已自动随机化）"
}

# ---------- 4. 启动服务 ----------
start_services() {
  cd "$APP_DIR"
  info "拉取预构建镜像..."
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" pull || \
    warn "镜像拉取失败，若未配置 CI 镜像请改用本地构建（见 DEPLOY.md 3.4）"
  info "启动全部服务..."
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
  ok "服务已启动"
}

# ---------- 5. 生成宿主机 nginx 反代配置 ----------
gen_host_nginx() {
  local domain="$1"
  if [ ! -d "/etc/nginx/conf.d" ] && [ ! -d "/etc/nginx/sites-available" ]; then
    if ask "宿主机未装 nginx，是否安装并使用 nginx 做 HTTPS 反代？(否则直接用 http://IP:80 访问)" "y" | grep -qiE '^y|^yes'; then
      apt-get install -y nginx || die "安装 nginx 失败"
    else
      info "跳过宿主机 nginx，访问 http://<服务器IP> 即可（前端容器已内置服务反代）"
      return
    fi
  fi
  local conf_dir="/etc/nginx/conf.d"
  local conf_file="${conf_dir}/autoteams.conf"
  if [ -d "/etc/nginx/sites-available" ]; then
    conf_dir="/etc/nginx/sites-available"
    conf_file="${conf_dir}/autoteams"
  fi

  cat > "$conf_file" <<EOF
# AutoTeams 宿主机 nginx 反代（HTTPS + 域名 → 前端容器 80）
# 前端容器内的 nginx 已负责 /api、/collab-api、/collab-ws、/bridge、/local-runner 转发，
# 宿主机这一层只做：443/80 → localhost:80（前端容器映射端口，默认80）。
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    # 强制 HTTPS 跳转
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${domain};

    # Let's Encrypt 证书（certbot 自动续期）
    ssl_certificate     /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # WebSocket / SSE 支持
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 360s;
    }
}
EOF

  if [ -d "/etc/nginx/sites-available" ]; then
    ln -sf "$conf_file" "/etc/nginx/sites-enabled/autoteams"
  fi

  # 申请证书
  if ask "是否现在用 certbot 申请 Let's Encrypt 证书（需域名已解析到本机）？" "y" | grep -qiE '^y|^yes'; then
    if ! command -v certbot >/dev/null 2>&1; then
      apt-get install -y certbot python3-certbot-nginx || die "安装 certbot 失败"
    fi
    certbot --nginx -d "${domain}" --non-interactive --agree-tos -m "admin@${domain}" || warn "certbot 申请失败，请手动执行：certbot --nginx -d ${domain}"
  fi

  nginx -t && systemctl reload nginx || warn "nginx 配置测试/reload 失败，请检查"
  ok "宿主机 nginx 反代已配置：https://${domain}"
}

# ---------- 6. 健康检查 ----------
health_check() {
  cd "$APP_DIR"
  info "等待服务就绪（前端容器在 backend healthy 后启动）..."
  sleep 10
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps
  local domain
  domain="$(cat /tmp/autoteams_domain.txt 2>/dev/null || echo autoteams.example.com)"
  info "健康检查："
  curl -fsS "http://127.0.0.1:8000/api/v1/health" && echo || warn "后端健康检查未通过，请 docker compose logs -f backend 查看"
}

# ---------- 主流程 ----------
main() {
  info "AutoTeams 一键部署开始"
  check_docker
  ensure_code
  gen_env
  start_services

  local domain
  domain="$(cat /tmp/autoteams_domain.txt 2>/dev/null || echo autoteams.example.com)"
  if ask "是否配置宿主机 nginx HTTPS 反代（域名 ${domain}）？" "y" | grep -qiE '^y|^yes'; then
    gen_host_nginx "$domain"
  fi
  health_check
  ok "部署完成！访问：https://${domain}（或 http://<服务器IP>）"
}

main "$@"