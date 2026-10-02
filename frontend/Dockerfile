FROM node:18-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./

RUN \
  if [ -f yarn.lock ]; then yarn install --frozen-lockfile; \
  elif [ -f package-lock.json ]; then npm ci; \
  elif [ -f pnpm-lock.yaml ]; then npm install -g pnpm && pnpm install --frozen-lockfile; \
  else npm install; \
  fi

COPY . .

# AUD-27: 保留构建期注入能力（默认留空 → 同源 /api/v1），
# 与运行时 config.json 互为补充：前者适合按环境产出不同制品，
# 后者让同一个镜像可以在不同域名 / API 主机下运行。
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# P0-10-B: 使用 nginx-unprivileged 镜像（默认以非 root 用户 101 运行，监听 8080）
# 这样无需在 nginx.conf 中降权，且符合企业安全基线
FROM nginxinc/nginx-unprivileged:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# AUD-27: 入口脚本在容器启动时按环境变量覆写 config.json。
# nginxinc 官方入口会顺序执行 /docker-entrypoint.d/*.sh。
COPY docker-entrypoint.d/ /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/40-autoteams-runtime-config.sh

# AUD-27: 可在运行时通过 `environment:` 覆盖，无需重建镜像。
ENV API_BASE_URL="" \
    COLLAB_WS_URL="" \
    ANALYTICS_URL=""

# nginx-unprivileged 默认监听 8080；nginx.conf 中也需 listen 8080
EXPOSE 8080

# P0-10-B: 健康检查（nginx 自带 curl，检查首页可达）
# 注意：容器内 localhost 会解析到 IPv6 ::1，而 nginx-unprivileged 仅监听
# IPv4 0.0.0.0:8080，用 localhost 探测会 "Connection refused" 导致镜像始终 unhealthy。
# 改用显式 127.0.0.1 进行 IPv4 探测。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ || exit 1

USER 101

CMD ["nginx", "-g", "daemon off;"]
